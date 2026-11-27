#!/usr/bin/env python3
"""Rehearse the LeadBoost Alembic adoption/upgrade on a RESTORED COPY of the production dump.

The dump (``prod-pre-alembic-adoption.dump``) is production data: it is read from stdin by a PostgreSQL client,
restored only into a new throw-away database, and nothing from it is ever printed, committed or uploaded. Output
is table names, row COUNTS, revision ids, exit codes and timings only.

SAFETY (refuses, never warns)
-----------------------------
* The server must be 127.0.0.1:56432 (the throw-away ``c95-pg16`` container) or, only for a dump made by a newer PostgreSQL,
  127.0.0.1:56433 (``c95-pg17``, created by the owner); every database this script creates,
  copies, restores or drops is named ``c95_rehearsal_*``. Anything else exits 2 before a statement is sent.
* Refuses if a ``.env`` exists in ``backend/`` or any parent directory (``load_dotenv()`` would pull real settings in).
* Children get an allow-listed environment: no LLM, SMTP, Stripe or Mailer key, ``ENVIRONMENT=development``,
  ``JOB_WORKER_ENABLED=false`` (the app's in-process job loop would otherwise claim and run the dump's queued jobs
  and scrape real websites), ``LIVE_SENDING_ENABLED=false``, ``AUTO_REPLY_ENABLED=false``.
* ``cleanup`` drops ONLY databases recorded in this script's own state file.

Environment: ``REHEARSAL_ADMIN_URL`` (postgresql://postgres:<pw>@127.0.0.1:56432/postgres, never printed),
``REHEARSAL_STATE_DIR`` (a writable directory outside the repository), optional ``REHEARSAL_PG_CLIENT=docker:c95-pg16``
(run pg_restore/pg_dump/psql inside that container; the dump is fed on STDIN, so no copy is left in the container),
optional ``PYTHON`` (interpreter for alembic/uvicorn children).

Subcommands (run from ``backend/``; all read-only except restore/adopt/wrong-order/cleanup, which only touch
databases named c95_rehearsal_*)::

    inspect-dump --dump FILE          format, archive/server versions (read from the header), TOC counts (no data)
    restore --dump FILE --label L     new database c95_rehearsal_lb_<L>_<UTC>; prints its name and row counts
    probe --db NAME                   which Alembic revisions' objects exist (read-only) + verify_baseline_schema result
    counts --db NAME                  row counts only (read-only)
    schema-diff --db NAME             exact differences between the copy and the HEAD models (read-only; names/types/counts)
    apply-sql --db NAME --file F      apply one .sql file from backend/ to a COPY this harness created (never anything else)
    adopt --db NAME [--stamp baseline|head] [--continue-after-verify-failure] [--full-downgrade-chain]
    wrong-order --source NAME         the realistic failure: app started (create_all) before Alembic, on two copies
    app-start --db NAME               start the real app on a free port, hit /live /ready /health, compare tables+counts
    status | cleanup

Exit status: 0 ok; 1 an expectation failed / verify refused; 2 refused by the guard; 3 environment or dump problem.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

BACKEND = Path(__file__).resolve().parent.parent
GUARD_HOST, GUARD_PORT, DB_PREFIX = "127.0.0.1", 56432, "c95_rehearsal_"
# A second throw-away server is allowed ONLY for a dump made by a newer PostgreSQL than c95-pg16 can read
# (c95-pg17 on 127.0.0.1:56433, created by the owner). Nothing else is ever accepted.
ALLOWED_PORTS = (GUARD_PORT, 56433)
EXIT_OK, EXIT_FAIL, EXIT_REFUSED, EXIT_ENV = 0, 1, 2, 3
_DB_NAME_OK = re.compile(r"^c95_rehearsal_[a-z0-9_]{1,50}$")
BASELINE, HEAD = "eaa40e596fcc", "e5c9a3b7d214"
CHILD_ENV_ALLOW = ("PATH", "HOME", "LANG", "LC_ALL", "PYTHONPYCACHEPREFIX", "TMPDIR", "SYSTEMROOT")
SECRET_KEY = "rehearsal-" + "k" * 40  # >= 32 chars, synthetic, not a real secret


class Refused(Exception):
    pass


class EnvProblem(Exception):
    """The environment cannot do what was asked (for example the dump needs a newer PostgreSQL client)."""


# ---------------------------------------------------------------------------------------------------------------
# What each Alembic revision adds (read from alembic/versions/*; tests/infrastructure/test_rollout_rehearsal.py
# proves this table against real `alembic upgrade` runs, so it cannot silently drift)
# ---------------------------------------------------------------------------------------------------------------
BASELINE_TABLES = [
    "organizations",
    "plans",
    "daily_lead_quota_usage",
    "discovery_run_logs",
    "invoices",
    "subscriptions",
    "usage_records",
    "users",
    "api_keys",
    "leads",
    "active_pipeline_locks",
    "ai_decision_logs",
    "evaluation_report_logs",
    "jobs",
    "lead_enrichment_logs",
    "pipeline_execution_logs",
    "prompt_execution_logs",
    "scraping_logs",
]
REVISION_SPEC: list[dict] = [
    {
        "rev": BASELINE,
        "what": "baseline: 18 tables, their P0 columns, uq_leads_org_website",
        "tables": BASELINE_TABLES,
        # Columns/constraint the baseline revision creates inside its tables that a pre-P0 database lacks
        # (the same ones scripts/verify_baseline_schema.py names).
        "columns": {
            "scraping_logs": ["organization_id", "pipeline_id"],
            "lead_enrichment_logs": ["organization_id", "pipeline_id", "success"],
            "ai_decision_logs": ["evaluation_version", "pipeline_id", "source"],
            "pipeline_execution_logs": ["error_message"],
            "evaluation_report_logs": ["evaluation_version"],
            "prompt_execution_logs": ["model"],
        },
        "constraints": {"leads": ["uq_leads_org_website"]},
    },
    {
        "rev": "61258798a87a",
        "what": "P1.2: organization_qualification_settings + 4 columns",
        "tables": ["organization_qualification_settings"],
        "columns": {"organizations": ["industry", "icp_description"], "users": ["job_title", "signature"]},
    },
    {"rev": "1e7ab6ba0976", "what": "P1.3: email_accounts", "tables": ["email_accounts"], "columns": {}},
    {
        "rev": "baee12037c73",
        "what": "P1.4: organization_outreach_policies + outreach_actions",
        "tables": ["organization_outreach_policies", "outreach_actions"],
        "columns": {},
    },
    {
        "rev": "c1a7e0d2b9f4",
        "what": "L1: 3 columns on email_accounts (+ unique constraint)",
        "tables": [],
        "columns": {"email_accounts": ["mailer_mailbox_ref", "mailer_sync_state", "mailer_sync_error_code"]},
        "constraints": {"email_accounts": ["uq_email_accounts_mailer_mailbox_ref"]},
    },
    {
        "rev": "d4b8f2a6c113",
        "what": "C9.4: outreach_actions.reply_to_message_reference",
        "tables": [],
        "columns": {"outreach_actions": ["reply_to_message_reference"]},
    },
    {
        "rev": HEAD,
        "what": "C9.5: 3 columns on outreach_actions",
        "tables": [],
        "columns": {
            "outreach_actions": [
                "reply_draft_requested_at",
                "approved_content_sha256",
                "approved_draft_version",
            ]
        },
    },
]
# Tables/columns a `downgrade -1` from each revision removes (for the data-loss report)
DOWNGRADE_DROPS = {
    HEAD: {
        "tables": [],
        "columns": {
            "outreach_actions": [
                "reply_draft_requested_at",
                "approved_content_sha256",
                "approved_draft_version",
            ]
        },
    },
    "d4b8f2a6c113": {"tables": [], "columns": {"outreach_actions": ["reply_to_message_reference"]}},
    "c1a7e0d2b9f4": {
        "tables": [],
        "columns": {"email_accounts": ["mailer_mailbox_ref", "mailer_sync_state", "mailer_sync_error_code"]},
    },
    "baee12037c73": {"tables": ["outreach_actions", "organization_outreach_policies"], "columns": {}},
    "1e7ab6ba0976": {"tables": ["email_accounts"], "columns": {}},
    "61258798a87a": {
        "tables": ["organization_qualification_settings"],
        "columns": {"organizations": ["industry", "icp_description"], "users": ["job_title", "signature"]},
    },
}


# ---------------------------------------------------------------------------------------------------------------
# Guard + pure logic (unit-tested without a database)
# ---------------------------------------------------------------------------------------------------------------
def assert_rehearsal_server(host, port) -> None:
    if (host or "").lower() != GUARD_HOST or port not in ALLOWED_PORTS:
        raise Refused(
            f"refusing: the PostgreSQL server must be {GUARD_HOST}:{' or '.join(str(x) for x in ALLOWED_PORTS)} "
            f"(the throw-away c95-pg* containers); got host={host!r} port={port!r}. Nothing was sent."
        )


def assert_rehearsal_db_name(name) -> None:
    if not isinstance(name, str) or not _DB_NAME_OK.match(name):
        raise Refused(
            f"refusing: database name {name!r} does not match {DB_PREFIX}[a-z0-9_]+ . Nothing was sent."
        )


def parse_admin_url(raw: str) -> dict:
    if not raw:
        raise Refused(
            "refusing: REHEARSAL_ADMIN_URL is not set (postgresql://postgres:<pw>@127.0.0.1:56432/postgres)."
        )
    parts = urlsplit(raw.replace("postgresql+psycopg2://", "postgresql://", 1))
    if parts.scheme not in ("postgresql", "postgres"):
        raise Refused("refusing: REHEARSAL_ADMIN_URL is not a PostgreSQL URL.")
    assert_rehearsal_server(parts.hostname, parts.port)
    return {
        "host": parts.hostname,
        "port": parts.port,
        "user": unquote(parts.username or "postgres"),
        "password": unquote(parts.password or ""),
        "admin_db": (parts.path or "/postgres").lstrip("/") or "postgres",
    }


def db_url(admin: dict, dbname: str, driver: str = "") -> str:
    assert_rehearsal_server(admin["host"], admin["port"])
    assert_rehearsal_db_name(dbname)
    return (
        f"postgresql{driver}://{quote(admin['user'])}:{quote(admin['password'], safe='')}"
        f"@{admin['host']}:{admin['port']}/{dbname}"
    )


def make_db_name(label: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dt%H%M%Sz")
    name = f"{DB_PREFIX}lb_{re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')}_{stamp}"
    assert_rehearsal_db_name(name)
    return name


def refuse_if_dotenv(start: Path = BACKEND) -> None:
    for d in [start, *start.parents]:
        if (d / ".env").exists():
            raise Refused(
                f"refusing: a .env exists in {d}; load_dotenv() would read it. Use a clean worktree."
            )


def classify_revisions(
    tables: set[str], columns: dict[str, set[str]], constraints: dict[str, set[str]] | None = None
) -> dict:
    """Per revision: present / absent / partial objects; overall verdict. Pure function of the live catalog."""
    rows = []
    for spec in REVISION_SPEC:
        want: list[tuple[str, bool]] = []
        for t in spec["tables"]:
            want.append((f"table {t}", t in tables))
        for t, cols in spec["columns"].items():
            for c in cols:
                want.append((f"column {t}.{c}", t in tables and c in columns.get(t, set())))
        if constraints is not None:
            for t, names in spec.get("constraints", {}).items():
                for n in names:
                    want.append((f"constraint {n}", n in constraints.get(t, set())))
        have = [w for w, ok in want if ok]
        miss = [w for w, ok in want if not ok]
        status = "present" if not miss else ("absent" if not have else "partial")
        rows.append(
            {"rev": spec["rev"], "what": spec["what"], "status": status, "missing": miss, "present": have}
        )
    st = [r["status"] for r in rows]
    if all(s == "present" for s in st):
        verdict, at = "at-head", HEAD
    elif "partial" not in st and st[0] == "present" and "present" not in st[st.index("absent") :]:
        at = rows[st.index("absent") - 1]["rev"]
        verdict = "clean-at-revision"
    elif st[0] == "absent":
        verdict, at = "not-a-leadboost-schema", None
    elif st[0] == "partial":
        verdict, at = "older-than-baseline", None
    else:
        verdict, at = "mixed", None
    return {"verdict": verdict, "at_revision": at, "revisions": rows}


def normalize_schema_dump(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        s = ln.rstrip()
        if not s or s.startswith("--") or s.startswith("\\restrict") or s.startswith("\\unrestrict"):
            continue
        out.append(s)
    return out


def read_archive_header(path: Path) -> dict:
    """Parse ONLY the version fields of a pg_dump custom-format header (the layout is the same for 1.15 and 1.16).

    Lets us learn which PostgreSQL made a dump without a client that can read it. The database name is read only to
    advance past it and is never returned or printed.
    """
    with open(path, "rb") as f:
        buf = f.read(4096)
    if not buf.startswith(b"PGDMP"):
        raise ValueError("not a custom-format archive")
    pos = 5

    def byte() -> int:
        nonlocal pos
        pos += 1
        return buf[pos - 1]

    ver = (byte(), byte(), byte())
    int_size, _off_size, fmt = byte(), byte(), byte()

    def rint() -> int:
        sign, v = byte(), 0
        for i in range(int_size):
            v |= byte() << (8 * i)
        return -v if sign else v

    def rstr() -> str | None:
        nonlocal pos
        n = rint()
        if n < 0:
            return None
        pos += n
        return buf[pos - n : pos].decode("utf-8", "replace")

    if ver >= (1, 15, 0):
        byte()  # compression algorithm
    else:
        rint()  # compression level
    for _ in range(7):
        rint()  # creation time
    out: dict = {"archive_version": ".".join(str(x) for x in ver), "format": fmt}
    if ver >= (1, 4, 0):
        rstr()  # database name: skipped, never kept
    if ver >= (1, 10, 0):
        out["server_version"] = rstr()
        out["pg_dump_version"] = rstr()
    return out


def major_of(version_text: str | None) -> int | None:
    m = re.match(r"\s*(\d+)", version_text or "")
    return int(m.group(1)) if m else None


def suggest_pg_major(header: dict) -> int | None:
    """The smallest PostgreSQL major that can restore the dump: the newer of the server and pg_dump that produced it."""
    majors = [
        m for m in (major_of(header.get("server_version")), major_of(header.get("pg_dump_version"))) if m
    ]
    return max(majors) if majors else None


def dump_kind(head: bytes) -> str:
    if head.startswith(b"PGDMP"):
        return "custom"
    if head.startswith(b"\x1f\x8b"):
        return "gzip"
    stripped = head.lstrip()
    if stripped.startswith((b"--", b"SET ", b"CREATE ", b"BEGIN", b"\\connect", b"\\restrict")):
        return "plain"
    return "unknown"


def parse_toc_header(listing: str) -> dict:
    info: dict = {"entries": 0, "table_data": 0, "tables": 0}
    for ln in listing.splitlines():
        if ln.startswith(";"):
            m = re.match(
                r";\s*(Dumped from database version|Dumped by pg_dump version|Format|Compression|"
                r"Archive created at|Entries):?\s*(.*)",
                ln,
            )
            if m and m.group(1) != "Archive created at":
                info[m.group(1)] = m.group(2).strip()
            continue
        if re.match(r"^\d+; \d+ \d+ ", ln):
            info["entries"] += 1
            if " TABLE DATA " in ln:
                info["table_data"] += 1
            elif re.search(r"\d+ \d+ TABLE public ", ln):
                info["tables"] += 1
    return info


# ---------------------------------------------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------------------------------------------
class Transcript:
    def __init__(self):
        self.results, self.t0 = [], time.monotonic()

    def say(self, m=""):
        print(m, flush=True)

    def step(self, title):
        self.say(f"\n=== {title}  [t+{time.monotonic() - self.t0:6.1f}s]")

    def expect(self, name, ok, got=""):
        self.results.append((name, bool(ok), got))
        self.say(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   (got: {got})" if got else ""))
        return bool(ok)

    @property
    def ok(self):
        return all(o for _n, o, _g in self.results)


T = Transcript()


def python_exe() -> str:
    return os.environ.get("PYTHON") or sys.executable


def child_env(admin: dict, dbname: str, extra: dict | None = None) -> dict:
    env = {k: os.environ[k] for k in CHILD_ENV_ALLOW if k in os.environ}
    env.update(
        {
            "DATABASE_URL": db_url(admin, dbname),
            "SECRET_KEY": SECRET_KEY,
            "ENVIRONMENT": "development",
            "JOB_WORKER_ENABLED": "false",
            "LIVE_SENDING_ENABLED": "false",
            "AUTO_REPLY_ENABLED": "false",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if extra:
        env.update(extra)
    return env


def sh(args, env, cwd=BACKEND, timeout=1800, stdin_path: Path | None = None, stdout_path: Path | None = None):
    t = time.monotonic()
    fin = open(stdin_path, "rb") if stdin_path else None
    fout = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
    try:
        p = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            stdin=fin,
            stdout=fout,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    finally:
        if fin:
            fin.close()
        if stdout_path:
            fout.close()
    out = "" if stdout_path else (p.stdout or b"").decode("utf-8", "replace")
    return p.returncode, out + (p.stderr or b"").decode("utf-8", "replace"), time.monotonic() - t


def err_line(text: str) -> str:
    """The most informative single line of a failed command (SQL error first), else the tail."""
    for key in ("already exists", "does not exist", "psycopg2.errors", "Error"):
        for ln in text.splitlines():
            if key in ln:
                return ln.strip()[:300]
    return tail(text, 3)


def tail(text: str, n=6) -> str:
    return " | ".join([ln for ln in text.splitlines() if ln.strip()][-n:])[:700]


def state_path() -> Path:
    d = os.environ.get("REHEARSAL_STATE_DIR")
    if not d:
        raise Refused(
            "refusing: REHEARSAL_STATE_DIR is not set (a writable directory outside the repository)."
        )
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p / "rehearsal_lb_state.json"


def load_state() -> dict:
    p = state_path()
    return json.loads(p.read_text()) if p.exists() else {"databases": []}


def record(name: str) -> None:
    st = load_state()
    if name not in st["databases"]:
        st["databases"].append(name)
    state_path().write_text(json.dumps(st, indent=2))


def _engine(admin: dict, dbname: str | None = None):
    from sqlalchemy import create_engine

    if dbname is None:
        assert_rehearsal_server(admin["host"], admin["port"])
        url = (
            f"postgresql+psycopg2://{quote(admin['user'])}:{quote(admin['password'], safe='')}"
            f"@{admin['host']}:{admin['port']}/{admin['admin_db']}"
        )
    else:
        url = db_url(admin, dbname, "+psycopg2")
    return create_engine(url, isolation_level="AUTOCOMMIT")


def create_db(admin, name, template=None):
    from sqlalchemy import text

    assert_rehearsal_db_name(name)
    if template:
        assert_rehearsal_db_name(template)
    with _engine(admin).connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"' + (f' TEMPLATE "{template}"' if template else "")))
    record(name)


def drop_db(admin, name):
    from sqlalchemy import text

    assert_rehearsal_db_name(name)
    with _engine(admin).connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def q(admin, dbname, sql, params=None):
    from sqlalchemy import text

    eng = _engine(admin, dbname)
    try:
        with eng.connect() as c:
            r = c.execute(text(sql), params or {})
            return r.fetchall() if r.returns_rows else r.rowcount
    finally:
        eng.dispose()


def table_counts(admin, dbname) -> dict[str, int]:
    names = [
        r[0] for r in q(admin, dbname, "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
    ]
    return {n: q(admin, dbname, f'SELECT count(*) FROM "{n}"')[0][0] for n in names}


def catalog(admin, dbname) -> tuple[set[str], dict[str, set[str]], dict[str, set[str]]]:
    tables = {r[0] for r in q(admin, dbname, "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
    cols: dict[str, set[str]] = {}
    for t, c in q(
        admin,
        dbname,
        "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema='public'",
    ):
        cols.setdefault(t, set()).add(c)
    cons: dict[str, set[str]] = {}
    for t, n in q(
        admin,
        dbname,
        "SELECT rel.relname, con.conname FROM pg_constraint con JOIN pg_class rel ON rel.oid=con.conrelid "
        "JOIN pg_namespace ns ON ns.oid=rel.relnamespace WHERE ns.nspname='public'",
    ):
        cons.setdefault(t, set()).add(n)
    return tables, cols, cons


def alembic_version(admin, dbname) -> str | None:
    if "alembic_version" not in catalog(admin, dbname)[0]:
        return None
    rows = q(admin, dbname, "SELECT version_num FROM alembic_version")
    return ",".join(r[0] for r in rows) or "(empty)"


def pg_client_cmd(admin, tool, dbname, extra):
    mode = os.environ.get("REHEARSAL_PG_CLIENT", "local")
    env = {k: os.environ[k] for k in CHILD_ENV_ALLOW if k in os.environ}
    env["PGPASSWORD"] = admin["password"]
    if mode.startswith("docker:"):
        c = mode.split(":", 1)[1]
        if not c.startswith("c95-"):
            raise Refused("refusing: REHEARSAL_PG_CLIENT container must be named c95-*")
        return [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGPASSWORD",
            c,
            tool,
            "-U",
            admin["user"],
            *(["-d", dbname] if dbname else []),
            *extra,
        ], env
    if not shutil.which(tool):
        raise Refused(
            f"{tool} not found on PATH (set REHEARSAL_PG_CLIENT=docker:c95-pg16 or install the PostgreSQL client)."
        )
    return [
        tool,
        "-h",
        admin["host"],
        "-p",
        str(admin["port"]),
        "-U",
        admin["user"],
        *(["-d", dbname] if dbname else []),
        *extra,
    ], env


def alembic(admin, dbname, *args, timeout=900):
    return sh([python_exe(), "-m", "alembic", *args], child_env(admin, dbname), timeout=timeout)


def schema_snapshot(admin, dbname) -> list[str]:
    cmd, env = pg_client_cmd(admin, "pg_dump", dbname, ["--schema-only", "--no-owner", "--no-privileges"])
    rc, out, _ = sh(cmd, env, timeout=300)
    if rc != 0:
        raise Refused(f"pg_dump --schema-only failed: {tail(out, 3)}")
    return normalize_schema_dump(out)


def verify_baseline(admin, dbname):
    rc, out, dt = sh(
        [python_exe(), "scripts/verify_baseline_schema.py"], child_env(admin, dbname), timeout=300
    )
    problems = [ln.strip()[2:] for ln in out.splitlines() if ln.strip().startswith("- ")]
    return rc, problems, dt


# ---------------------------------------------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------------------------------------------
def cmd_inspect_dump(admin, dump: Path) -> int:
    T.step("inspect-dump (no data is read into the output)")
    if not dump.is_file():
        raise Refused(f"dump not found: {dump.name}")
    with open(dump, "rb") as f:
        head = f.read(64)
    kind = dump_kind(head)
    T.say(f"  size: {dump.stat().st_size} bytes; kind: {kind}")
    if kind == "custom":
        hdr = {}
        try:
            hdr = read_archive_header(dump)
            T.say("  header (versions only): " + json.dumps(hdr))
        except (ValueError, IndexError) as exc:
            T.say(f"  header not readable here ({type(exc).__name__}); continuing with pg_restore --list")
        cmd, env = pg_client_cmd(admin, "pg_restore", None, ["--list"])
        rc, out, _ = sh(cmd, env, stdin_path=dump, timeout=300)
        if rc != 0:
            T.say(f"  pg_restore --list FAILED (rc={rc}): {tail(out, 3)}")
            if "unsupported" in out:
                need = suggest_pg_major(hdr)
                T.say(
                    "  STOP: the dump was made by a newer PostgreSQL than the client in this container can read."
                )
                if need:
                    T.say(
                        f"  It needs a PostgreSQL {need} (or newer) server container: postgres:{need}, named c95-pg{need}, 127.0.0.1:56433 only."
                    )
                T.say(
                    "  Creating another container needs the owner's consent. Do not continue until it exists."
                )
            return EXIT_ENV
        info = parse_toc_header(out)
        T.say("  " + json.dumps(info))
        T.say("  -> restore with pg_restore (custom format).")
    elif kind == "plain":
        T.say(
            "  -> a plain SQL dump: restore with psql (stdin). Roles/ownership statements may error; harmless with --no-owner semantics not applying."
        )
    elif kind == "gzip":
        T.say(
            "  gzip-compressed: decompress it OUTSIDE the repository first (gunzip -k), then run inspect-dump on the result."
        )
        return EXIT_ENV
    else:
        T.say("  unrecognised format: do not guess. Check with:  file <dump>")
        return EXIT_ENV
    return EXIT_OK


def restore_dump(admin, dump: Path, label: str) -> str:
    kind = dump_kind(open(dump, "rb").read(64))
    if kind == "custom":  # preflight BEFORE creating anything: can this client read the archive at all?
        cmd, env = pg_client_cmd(admin, "pg_restore", None, ["--list"])
        rc, out, _ = sh(cmd, env, stdin_path=dump, timeout=300)
        if rc != 0:
            need = None
            try:
                need = suggest_pg_major(read_archive_header(dump))
            except (ValueError, IndexError):
                pass
            raise EnvProblem(
                f"pg_restore cannot read this dump ({tail(out, 2)}). No database was created."
                + (
                    f" It needs a PostgreSQL {need} (or newer) throw-away server (see inspect-dump)."
                    if need
                    else ""
                )
            )
    name = make_db_name(label)
    create_db(admin, name)
    if kind == "custom":
        cmd, env = pg_client_cmd(admin, "pg_restore", name, ["--no-owner", "--no-privileges"])
    elif kind == "plain":
        cmd, env = pg_client_cmd(admin, "psql", name, ["-v", "ON_ERROR_STOP=0", "-q", "-o", os.devnull])
    else:
        raise Refused("unsupported dump kind for restore")
    rc, out, dt = sh(cmd, env, stdin_path=dump, timeout=3600)
    errs = [ln for ln in out.splitlines() if "error" in ln.lower()]
    T.say(f"  restore into {name}: rc={rc}, {len(errs)} error line(s), {dt:.1f}s")
    for e in errs[:5]:
        T.say("    " + e[:200])
    if not table_counts(admin, name):
        drop_db(admin, name)
        raise EnvProblem(
            f"the restore produced no tables (rc={rc}); the empty database was dropped. First error: {errs[0][:200] if errs else 'none'}"
        )
    return name


def cmd_restore(admin, dump: Path, label: str) -> int:
    T.step("restore into a NEW throw-away database")
    name = restore_dump(admin, dump, label)
    counts = table_counts(admin, name)
    T.expect("restored database has tables", len(counts) > 0, f"{len(counts)} tables")
    T.say("  row counts (counts only): " + json.dumps({k: v for k, v in counts.items() if v}))
    T.say(f"  RESTORED DATABASE: {name}")
    return EXIT_OK if T.ok else EXIT_FAIL


def cmd_probe(admin, db: str) -> int:
    T.step(f"probe {db}  (read-only)")
    tables, cols, cons = catalog(admin, db)
    res = classify_revisions(tables, cols, cons)
    T.say(f"  alembic_version table: {alembic_version(admin, db) or 'ABSENT'}")
    for r in res["revisions"]:
        T.say(
            f"  {r['rev']}  {r['status']:8s} {r['what']}"
            + (f"   missing: {', '.join(r['missing'])}" if r["missing"] else "")
        )
    T.say(
        f"  VERDICT: {res['verdict']}"
        + (f" (objects match exactly revision {res['at_revision']})" if res["at_revision"] else "")
    )
    extra = sorted(
        t
        for t in tables
        if t not in {x for s in REVISION_SPEC for x in s["tables"]} and t != "alembic_version"
    )
    if extra:
        T.say(f"  tables not created by any revision: {', '.join(extra)}")
    rc, problems, _ = verify_baseline(admin, db)
    T.say(
        f"  scripts/verify_baseline_schema.py exit code: {rc} ({'schema matches HEAD models' if rc == 0 else str(len(problems)) + ' problem(s)'})"
    )
    for p in problems:
        T.say(f"    - {p}")
    if res["verdict"] == "older-than-baseline":
        T.say(
            "  OLDER THAN THE BASELINE: the 18 baseline tables exist but some columns/constraints the baseline revision itself creates are missing,"
        )
        T.say(
            "  so neither `stamp baseline` nor `stamp head` describes this database. Run `schema-diff` for the exact list. The repo's historical"
        )
        T.say(
            "  backend/migrations/001_p0_durable_execution.sql adds the missing P0 COLUMNS (idempotent, additive); it does NOT add uq_leads_org_website."
        )
    T.say(
        "  Reading guide: verify compares against HEAD models. exit 0 = the schema already equals head (the repo's test then stamps HEAD);"
    )
    T.say(
        "  exit 1 = do not stamp. DEPLOYMENT.md's 'stamp eaa40e596fcc' is only coherent for a database that equals the baseline,"
    )
    T.say(
        "  which verify (comparing to head) rejects. This probe tells you which of those you actually have; it changes nothing."
    )
    return EXIT_OK


def assert_child_target(url: str) -> None:
    """The read-only schema-diff child may open SQLite (tests) or a c95_rehearsal_* database on an allowed server only."""
    if url.startswith("sqlite"):
        return
    parts = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
    assert_rehearsal_server(parts.hostname, parts.port)
    assert_rehearsal_db_name((parts.path or "/").lstrip("/"))


def _flatten(diffs) -> list:
    out = []
    for d in diffs:
        if isinstance(d, list):
            out.extend(_flatten(d))
        else:
            out.append(d)
    return out


def describe_diff(d) -> tuple[str, str]:
    """(kind, one-line description) for an Alembic compare_metadata operation. Names and types only."""
    kind = d[0]
    if kind in ("add_table", "remove_table"):
        t = d[1]
        return kind, f"{t.name} ({len(t.columns)} columns)"
    if kind in ("add_column", "remove_column"):
        col = d[3]
        null = "NULL" if col.nullable else "NOT NULL"
        dflt = "server default" if col.server_default is not None else "no server default"
        return kind, f"{d[2]}.{col.name} {col.type} {null}, {dflt}"
    if kind in ("modify_nullable", "modify_type", "modify_default"):
        return kind, f"{d[2]}.{d[3]}: database {d[5]!s} -> models {d[6]!s}"
    if kind in ("add_constraint", "remove_constraint"):
        c = d[1]
        cols = ", ".join(getattr(col, "name", str(col)) for col in getattr(c, "columns", []))
        table = getattr(getattr(c, "table", None), "name", "?")
        return kind, f"{type(c).__name__} {c.name or '(unnamed)'} on {table} ({cols})"
    if kind in ("add_index", "remove_index"):
        i = d[1]
        return (
            kind,
            f"{i.name} on {i.table.name} ({', '.join(c.name for c in i.columns)}){' UNIQUE' if i.unique else ''}",
        )
    if kind in ("add_fk", "remove_fk"):
        f = d[1]
        return kind, f"{f.name or '(unnamed)'} on {f.table.name} -> {f.referred_table.name}"
    return kind, str(d[1:])[:160]


def child_schema_diff() -> int:
    """Child: compare the live database (read-only) with the HEAD models. Prints names, types and counts only."""
    url = os.environ.get("DATABASE_URL", "")
    assert_child_target(url)
    sys.path.insert(0, str(BACKEND))
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from sqlalchemy import create_engine, text

    import application.observability.models  # noqa: F401
    import core.infrastructure.database as db

    eng = create_engine(url)
    kinds: dict[str, int] = {}
    with eng.connect() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": False})
        flat = _flatten(compare_metadata(ctx, db.Base.metadata))
        for d in sorted(flat, key=lambda x: (x[0], describe_diff(x)[1])):
            kind, desc = describe_diff(d)
            if kind == "remove_table" and desc.startswith("alembic_version"):
                continue
            kinds[kind] = kinds.get(kind, 0) + 1
            print(f"DIFF {kind:16s} {desc}")
            if kind == "add_constraint" and type(d[1]).__name__ == "UniqueConstraint":
                c = d[1]
                names = [col.name for col in c.columns]
                tname = c.table.name
                if conn.dialect.name == "postgresql" and tname in set(
                    r[0]
                    for r in conn.execute(
                        text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                    ).fetchall()
                ):
                    quoted = ", ".join(f'"{n}"' for n in names)
                    notnull = " AND ".join(f'"{n}" IS NOT NULL' for n in names)
                    n = conn.execute(
                        text(
                            f'SELECT count(*) FROM (SELECT 1 FROM "{tname}" WHERE {notnull} GROUP BY {quoted} HAVING count(*) > 1) x'
                        )
                    ).scalar()
                    print(f"DUP  {c.name}: groups of rows that would violate it (counts only): {n}")
    print("SUMMARY " + json.dumps(kinds, sort_keys=True))
    return 0


def cmd_schema_diff(admin, db: str) -> int:
    T.step(f"schema-diff {db}  (read-only: live database vs HEAD models; names, types and counts only)")
    rc, out, dt = sh(
        [python_exe(), str(Path(__file__).resolve()), "child-schema-diff"], child_env(admin, db), timeout=300
    )
    lines = [ln for ln in out.splitlines() if ln.startswith(("DIFF ", "DUP ", "SUMMARY "))]
    if rc != 0 or not lines:
        raise EnvProblem(f"schema-diff failed (rc={rc}): {err_line(out)}")
    for ln in lines:
        T.say("  " + ln)
    T.say(
        f"  ({dt:.1f}s) This is a comparison, not a plan: it lists what differs. add_* = missing from the database; remove_* = extra in the database."
    )
    T.say(
        "  A NOT NULL column without a server default cannot simply be added to a table that has rows; each such line needs a decision."
    )
    return EXIT_OK


def cmd_apply_sql(admin, db: str, sql_file: Path) -> int:
    """Apply one of the repository's own SQL files to a rehearsal COPY (never to anything else)."""
    T.step(f"apply-sql {sql_file.name} to the rehearsal copy {db}")
    assert_rehearsal_db_name(db)
    if db not in load_state()["databases"]:
        raise Refused(f"refusing: {db} was not created by this harness (not in its state file).")
    f = sql_file.resolve()
    if BACKEND.resolve() not in f.parents or f.suffix != ".sql" or not f.is_file():
        raise Refused(
            "refusing: only an existing .sql file inside the repository's backend/ directory may be applied."
        )
    cmd, env = pg_client_cmd(admin, "psql", db, ["-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", "-"])
    rc, out, dt = sh(cmd, env, stdin_path=f, timeout=600)
    shown = [ln.strip()[:200] for ln in out.splitlines() if ln.strip()][:12]
    T.expect(f"psql applied {f.name} (rc={rc}, {dt:.1f}s)", rc == 0, "" if rc == 0 else err_line(out))
    for ln in shown:
        T.say("    " + ln)
    T.say("  Next: `probe` and `schema-diff` on the same copy show what is still different.")
    return EXIT_OK if T.ok else EXIT_FAIL


def cmd_counts(admin, db: str) -> int:
    T.say(json.dumps(table_counts(admin, db), indent=1))
    return EXIT_OK


def _revs(out: str) -> list[str]:
    return re.findall(r"Running (?:upgrade|downgrade) \S* ?-> (\w+)", out)


def cmd_adopt(admin, db: str, stamp: str, cont: bool, chain: bool, state_dir: Path) -> int:
    rev = BASELINE if stamp == "baseline" else "head"
    T.step(f"A. before: counts, revision, backup (and proof the backup restores) for {db}")
    before = table_counts(admin, db)
    if not before:
        raise EnvProblem(
            "this database has no tables: the restore did not work, so there is nothing to adopt. Nothing was changed."
        )
    T.say("  counts: " + json.dumps({k: v for k, v in before.items() if v}))
    T.say(f"  alembic_version: {alembic_version(admin, db) or 'ABSENT'}")
    backup = state_dir / f"backup_{db}.dump"
    cmd, env = pg_client_cmd(admin, "pg_dump", db, ["-Fc", "--no-owner", "--no-privileges"])
    rc, _o, dt = sh(cmd, env, stdout_path=backup)
    T.expect(
        "backup of the restored copy (pg_dump -Fc)",
        rc == 0 and backup.stat().st_size > 0,
        f"{dt:.1f}s, {backup.stat().st_size} bytes",
    )
    proof = make_db_name("backupproof")
    create_db(admin, proof)
    cmd, env = pg_client_cmd(admin, "pg_restore", proof, ["--no-owner", "--no-privileges"])
    rc, _o, dt = sh(cmd, env, stdin_path=backup)
    T.expect(
        "the backup restores and its row counts equal the source",
        table_counts(admin, proof) == before,
        f"{dt:.1f}s",
    )
    drop_db(admin, proof)
    backup.unlink(missing_ok=True)

    T.step("B. verify_baseline_schema.py (documented gate: exit 1 = STOP, do not stamp)")
    rc, problems, dt = verify_baseline(admin, db)
    T.say(f"  exit {rc} in {dt:.1f}s; {len(problems)} problem(s)")
    for p in problems:
        T.say(f"    - {p}")
    if rc != 0 and not cont:
        T.say("  STOP (documented behaviour): verify failed, so nothing was stamped and nothing was changed.")
        T.say(
            "  Run `probe` to see which revision the objects match. Re-run adopt with --continue-after-verify-failure ONLY on a copy,"
        )
        T.say(
            "  to learn what the undocumented path would do; that is a rehearsal device, not the procedure."
        )
        return EXIT_FAIL
    if rc != 0:
        T.say(
            "  CONTINUING because --continue-after-verify-failure was given (rehearsal device, NOT the documented procedure)."
        )

    T.step(f"C. alembic stamp {rev}")
    rc, out, _ = alembic(admin, db, "stamp", rev)
    if not T.expect(f"stamp {rev}", rc == 0, "" if rc == 0 else err_line(out)):
        return EXIT_FAIL
    T.say(f"  alembic_version: {alembic_version(admin, db)}")

    T.step("D. alembic upgrade head")
    rc, out, dt = alembic(admin, db, "upgrade", "head")
    applied = re.findall(r"Running upgrade (\w+) -> (\w+)", out)
    T.say(
        "  revisions applied: "
        + (", ".join(f"{a}->{b}" for a, b in applied) or "none (already at head)")
        + f"  ({dt:.1f}s)"
    )
    if not T.expect("upgrade head exit 0", rc == 0, err_line(out) if rc else f"{dt:.1f}s"):
        T.say("  The failed upgrade ran in one transaction; state after the failure:")
        T.say(f"  alembic_version: {alembic_version(admin, db)}")
        return EXIT_FAIL
    T.expect(
        "alembic current is the head revision",
        (alembic_version(admin, db) or "") == HEAD,
        str(alembic_version(admin, db)),
    )
    rc, out, _ = alembic(admin, db, "check")
    T.expect(
        "alembic check: No new upgrade operations detected",
        rc == 0 and "No new upgrade operations detected" in out,
        "" if rc == 0 else err_line(out),
    )
    after = table_counts(admin, db)
    changed = {k: (before.get(k), v) for k, v in after.items() if k in before and before[k] != v}
    T.expect("row counts of every pre-existing table unchanged by upgrade", not changed, str(changed))
    T.say("  new tables: " + (", ".join(sorted(set(after) - set(before))) or "none"))

    T.step("E. round trip: downgrade -1, upgrade head, schema identical?")
    snap_a = schema_snapshot(admin, db)
    rc, out, _ = alembic(admin, db, "downgrade", "-1")
    T.expect("downgrade -1 exit 0", rc == 0, "" if rc == 0 else err_line(out))
    T.expect(
        "alembic_version is the previous revision",
        alembic_version(admin, db) == "d4b8f2a6c113",
        str(alembic_version(admin, db)),
    )
    snap_mid = schema_snapshot(admin, db)
    T.expect("the downgrade changed the schema", snap_mid != snap_a)
    rc, out, _ = alembic(admin, db, "upgrade", "head")
    T.expect("upgrade head again exit 0", rc == 0, "" if rc == 0 else err_line(out))
    snap_b = schema_snapshot(admin, db)
    diff = [
        d
        for d in difflib.unified_diff(snap_a, snap_b, "before", "after", lineterm="", n=0)
        if not d.startswith(("---", "+++", "@@"))
    ]
    T.expect(
        "schema after the round trip equals the schema before it (pg_dump --schema-only, normalised)",
        not diff,
        f"{len(diff)} differing line(s)",
    )
    for d in diff[:20]:
        T.say("    " + d[:200])
    rc, out, _ = alembic(admin, db, "check")
    T.expect(
        "alembic check clean after the round trip", rc == 0 and "No new upgrade operations detected" in out
    )

    if chain:
        T.step(
            "F. downgrade through every revision to the baseline (never below it: that would drop all tables), then back to head"
        )
        cur = HEAD
        while cur != BASELINE:
            drops = DOWNGRADE_DROPS[cur]
            lost = {t: table_counts(admin, db).get(t, 0) for t in drops["tables"]}
            rc, out, _ = alembic(admin, db, "downgrade", "-1")
            now = alembic_version(admin, db)
            T.say(
                f"  downgrade from {cur}: rc={rc} -> {now}; drops tables {drops['tables'] or '-'} (rows destroyed: {lost or 0}); drops columns {drops['columns'] or '-'}"
            )
            if rc != 0:
                T.expect(f"downgrade from {cur}", False, err_line(out))
                break
            cur = now
        rc, out, _ = alembic(admin, db, "upgrade", "head")
        T.expect("upgrade head after the chain exit 0", rc == 0, "" if rc == 0 else err_line(out))
        T.say(
            "  Data warning: every downgrade of a revision that created a table destroys that table's rows; column drops destroy their values."
        )
    T.step("SUMMARY")
    T.say(f"  expectations: {len(T.results)}, failed: {sum(1 for _n, o, _g in T.results if not o)}")
    return EXIT_OK if T.ok else EXIT_FAIL


def free_port() -> int:
    with socket.socket() as s:
        s.bind((GUARD_HOST, 0))
        return s.getsockname()[1]


def http_get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read(300).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(300).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


def run_app_start(admin, db: str, label: str = "app-start") -> dict:
    """Start the real app (uvicorn) against db with the job loop OFF; returns a dict of what happened."""
    before_t = catalog(admin, db)[0]
    before_c = table_counts(admin, db)
    port = free_port()
    env = child_env(admin, db)
    log = state_path().parent / f"{label}_{db}.log"
    with open(log, "wb") as lf:
        proc = subprocess.Popen(
            [
                python_exe(),
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                GUARD_HOST,
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=BACKEND,
            env=env,
            stdout=lf,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    res: dict = {"port": port, "live": None, "ready": None, "health": None}
    try:
        t0 = time.monotonic()
        while time.monotonic() - t0 < 90:
            if proc.poll() is not None:
                break
            code, _ = http_get(f"http://{GUARD_HOST}:{port}/live")
            if code == 200:
                break
            time.sleep(0.5)
        res["startup_s"] = round(time.monotonic() - t0, 1)
        res["live"] = http_get(f"http://{GUARD_HOST}:{port}/live")[0]
        res["ready"] = http_get(f"http://{GUARD_HOST}:{port}/ready")[0]
        res["health"] = http_get(f"http://{GUARD_HOST}:{port}/health")[0]
        res["exited_early"] = proc.poll() is not None
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    after_t = catalog(admin, db)[0]
    after_c = table_counts(admin, db)
    res["tables_created"] = sorted(after_t - before_t)
    res["tables_dropped"] = sorted(before_t - after_t)
    res["count_changes"] = {k: (before_c.get(k), v) for k, v in after_c.items() if before_c.get(k) != v}
    res["log_tail"] = tail(log.read_text(errors="replace"), 4) if log.exists() else ""
    return res


def cmd_app_start(admin, db: str) -> int:
    T.step(f"app-start on {db}: the real app, job loop OFF, no LLM/SMTP/Stripe/Mailer keys")
    r = run_app_start(admin, db)
    T.say(f"  startup {r.get('startup_s')}s; /live={r['live']} /ready={r['ready']} /health={r['health']}")
    T.expect("/live answers 200", r["live"] == 200, r["log_tail"] if r["live"] != 200 else "")
    T.expect(
        "/ready answers 200 (database reachable; it does NOT check the schema revision)", r["ready"] == 200
    )
    T.say(
        f"  /health={r['health']} is informational: it needs Redis and answers 503 without it (docs/DEPLOYMENT.md)"
    )
    T.expect(
        "startup created no tables (create_all had nothing to add)",
        not r["tables_created"],
        ", ".join(r["tables_created"]),
    )
    T.expect(
        "startup changed no row counts (job loop off; plans already seeded)",
        not r["count_changes"],
        str(r["count_changes"]),
    )
    return EXIT_OK if T.ok else EXIT_FAIL


def cmd_wrong_order(admin, source: str, state_dir: Path) -> int:
    T.step("WRONG ORDER: the new app is started (create_all) BEFORE Alembic, e.g. by a platform auto-deploy")
    assert_rehearsal_db_name(source)
    res = {}
    for variant in ("unstamped", "stamped-baseline"):
        name = make_db_name(f"wrong_{variant.split('-')[0]}")
        create_db(admin, name, template=source)
        T.step(f"variant {variant}: {name}")
        if variant == "stamped-baseline":
            rc, out, _ = alembic(admin, name, "stamp", BASELINE)
            T.say(f"  stamp {BASELINE}: rc={rc}")
        before = classify_revisions(*catalog(admin, name))
        T.say(f"  before: {before['verdict']} {before['at_revision'] or ''}")
        r = run_app_start(admin, name, label="wrong")
        T.say(
            f"  app start: /live={r['live']} /ready={r['ready']}; create_all created tables: {r['tables_created'] or 'none'}"
        )
        after = classify_revisions(*catalog(admin, name))
        T.say(f"  after:  {after['verdict']}")
        for x in after["revisions"]:
            if x["status"] != "present":
                T.say(f"    {x['rev']} {x['status']}: {', '.join(x['missing'])}")
        rc_v, problems, _ = verify_baseline(admin, name)
        T.say(f"  verify_baseline_schema.py now exits {rc_v} ({len(problems)} problem(s))")
        rc, out, _ = alembic(admin, name, "upgrade", "head")
        T.say(f"  alembic upgrade head: rc={rc}" + (f"; {err_line(out)}" if rc else ""))
        T.say(f"  alembic_version afterwards: {alembic_version(admin, name) or 'ABSENT'}")
        res[variant] = {
            "created": r["tables_created"],
            "upgrade_rc": rc,
            "verify_rc": rc_v,
            "after": after["verdict"],
        }
    T.say(
        "\n  Reading guide: create_all only ADDS missing tables, never columns; a later Alembic revision that creates one of those"
    )
    T.say(
        "  tables then fails 'already exists' (one transaction: nothing is applied, the version stays). The database is then neither"
    )
    T.say("  the baseline nor head: restore from the pre-migration backup rather than editing it by hand.")
    (state_dir / "rehearsal_lb_wrong_order.json").write_text(json.dumps(res, indent=2))
    return EXIT_OK


def cmd_cleanup(admin) -> int:
    st = load_state()
    for n in list(st["databases"]):
        drop_db(admin, n)
        print(f"dropped {n}")
    st["databases"] = []
    state_path().write_text(json.dumps(st, indent=2))
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect-dump")
    p.add_argument("--dump", required=True)
    p = sub.add_parser("restore")
    p.add_argument("--dump", required=True)
    p.add_argument("--label", default="dump")
    for n in ("probe", "counts", "app-start"):
        p = sub.add_parser(n)
        p.add_argument("--db", required=True)
    p = sub.add_parser("adopt")
    p.add_argument("--db", required=True)
    p.add_argument("--stamp", choices=["baseline", "head"], default="baseline")
    p.add_argument("--continue-after-verify-failure", action="store_true")
    p.add_argument("--full-downgrade-chain", action="store_true")
    p = sub.add_parser("schema-diff")
    p.add_argument("--db", required=True)
    p = sub.add_parser("apply-sql")
    p.add_argument("--db", required=True)
    p.add_argument("--file", required=True)
    sub.add_parser("child-schema-diff")
    p = sub.add_parser("wrong-order")
    p.add_argument("--source", required=True)
    sub.add_parser("status")
    sub.add_parser("cleanup")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "child-schema-diff":
            return child_schema_diff()
        admin = parse_admin_url(os.environ.get("REHEARSAL_ADMIN_URL", ""))
        refuse_if_dotenv()
        sd = state_path().parent
        if a.cmd == "status":
            print(json.dumps(load_state(), indent=2))
            return EXIT_OK
        if a.cmd == "cleanup":
            return cmd_cleanup(admin)
        if a.cmd in ("probe", "counts", "app-start", "adopt", "schema-diff", "apply-sql"):
            assert_rehearsal_db_name(a.db)
        if a.cmd == "inspect-dump":
            return cmd_inspect_dump(admin, Path(a.dump))
        if a.cmd == "restore":
            return cmd_restore(admin, Path(a.dump), a.label)
        if a.cmd == "probe":
            return cmd_probe(admin, a.db)
        if a.cmd == "schema-diff":
            return cmd_schema_diff(admin, a.db)
        if a.cmd == "apply-sql":
            return cmd_apply_sql(admin, a.db, Path(a.file))
        if a.cmd == "counts":
            return cmd_counts(admin, a.db)
        if a.cmd == "app-start":
            return cmd_app_start(admin, a.db)
        if a.cmd == "adopt":
            return cmd_adopt(
                admin, a.db, a.stamp, a.continue_after_verify_failure, a.full_downgrade_chain, sd
            )
        if a.cmd == "wrong-order":
            return cmd_wrong_order(admin, a.source, sd)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except EnvProblem as exc:
        print(f"ENVIRONMENT PROBLEM: {exc}", file=sys.stderr)
        return EXIT_ENV
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
