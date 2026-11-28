"""scripts/rollout_rehearsal.py: safety guard, pure logic, and a drift test for its revision table.

No PostgreSQL is needed: the revision table is proven against real ``alembic upgrade`` runs on SQLite, one
revision at a time, so the harness cannot silently disagree with alembic/versions/.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

BACKEND = Path(__file__).resolve().parents[2]
SCRIPT = BACKEND / "scripts" / "rollout_rehearsal.py"

_spec = importlib.util.spec_from_file_location("rollout_rehearsal", SCRIPT)
rr = importlib.util.module_from_spec(_spec)
sys.modules["rollout_rehearsal"] = rr
_spec.loader.exec_module(rr)


@pytest.mark.parametrize(
    "host,port",
    [
        ("db.example.com", 56432),
        ("127.0.0.1", 5432),
        ("127.0.0.1", 56431),
        ("127.0.0.1", 56434),
        ("localhost", 56432),
        (None, None),
    ],
)
def test_server_other_than_the_throwaway_container_is_refused(host, port):
    with pytest.raises(rr.Refused):
        rr.assert_rehearsal_server(host, port)


@pytest.mark.parametrize(
    "name",
    [
        "leadboost",
        "postgres",
        "c95_rehearsal_",
        "x_c95_rehearsal_y",
        'c95_rehearsal_x"; DROP DATABASE postgres; --',
        "",
        None,
    ],
)
def test_database_names_outside_the_prefix_are_refused(name):
    with pytest.raises(rr.Refused):
        rr.assert_rehearsal_db_name(name)


def test_admin_url_and_generated_names():
    with pytest.raises(rr.Refused):
        rr.parse_admin_url("postgresql://u:p@prod.example.com:5432/postgres")
    admin = rr.parse_admin_url("postgresql://postgres:p%40ss@127.0.0.1:56432/postgres")
    assert admin["password"] == "p@ss"
    name = rr.make_db_name("Prod dump!", datetime(2026, 11, 25, 1, 2, 3, tzinfo=timezone.utc))
    assert name == "c95_rehearsal_lb_prod_dump_20261125t010203z"
    assert rr.db_url(admin, name).endswith("/" + name)
    with pytest.raises(rr.Refused):
        rr.db_url(admin, "leadboost")


def test_children_get_no_secrets_and_the_job_loop_is_off(monkeypatch):
    for k in (
        "GROQ_API_KEY",
        "SERPER_API_KEY",
        "STRIPE_SECRET_KEY",
        "MAILING_AGENT_ORG_API_KEYS",
        "DATABASE_URL",
    ):
        monkeypatch.setenv(k, "real-looking-" + k)
    admin = rr.parse_admin_url("postgresql://postgres:pw@127.0.0.1:56432/postgres")
    env = rr.child_env(admin, "c95_rehearsal_lb_x")
    assert not any(
        k in env
        for k in ("GROQ_API_KEY", "SERPER_API_KEY", "STRIPE_SECRET_KEY", "MAILING_AGENT_ORG_API_KEYS")
    )
    assert env["DATABASE_URL"].endswith("@127.0.0.1:56432/c95_rehearsal_lb_x")
    assert env["JOB_WORKER_ENABLED"] == "false" and env["ENVIRONMENT"] == "development"
    assert env["LIVE_SENDING_ENABLED"] == "false" and env["AUTO_REPLY_ENABLED"] == "false"
    assert len(env["SECRET_KEY"]) >= 32


def test_dotenv_anywhere_above_is_refused(tmp_path):
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    rr.refuse_if_dotenv(deep)  # nothing there: fine
    (tmp_path / ".env").write_text("X=1")
    with pytest.raises(rr.Refused):
        rr.refuse_if_dotenv(deep)


def _run(env_extra: dict, *args: str) -> subprocess.CompletedProcess:
    env = {"PATH": os.environ.get("PATH", "")}
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=60
    )


def test_cli_refuses_a_non_throwaway_server_and_never_echoes_the_password(tmp_path):
    p = _run(
        {
            "REHEARSAL_ADMIN_URL": "postgresql://u:SECRETPW@db.example.com:5432/postgres",
            "REHEARSAL_STATE_DIR": str(tmp_path),
        },
        "cleanup",
    )
    assert p.returncode == 2 and "REFUSED" in p.stderr and "SECRETPW" not in p.stdout + p.stderr


def test_dump_kind_and_toc_parsing():
    assert rr.dump_kind(b"PGDMP\x01\x0e") == "custom"
    assert rr.dump_kind(b"-- PostgreSQL database dump") == "plain"
    assert rr.dump_kind(b"\x1f\x8b\x08") == "gzip"
    assert rr.dump_kind(b"random bytes") == "unknown"
    listing = (
        ";\n; Archive created at 2026-11-25 00:00:00 UTC\n;     dbname: secret_name\n;     Dumped from database version: 16.4\n"
        ";     Dumped by pg_dump version: 16.4\n;     Format: CUSTOM\n;     Compression: gzip\n"
        "218; 1259 16385 TABLE public leads x\n3590; 0 16385 TABLE DATA public leads x\n"
    )
    info = rr.parse_toc_header(listing)
    assert info["table_data"] == 1 and info["tables"] == 1 and info["Dumped from database version"] == "16.4"
    assert "secret_name" not in str(info), "the database name must not be echoed"


def test_schema_snapshot_normalisation_drops_comments_and_restrict_tokens():
    text = "--\n-- header\n\\restrict abc\nCREATE TABLE a (id int);\n\n\\unrestrict abc\n"
    assert rr.normalize_schema_dump(text) == ["CREATE TABLE a (id int);"]


def _tables_cols(upto: int) -> tuple[set[str], dict[str, set[str]]]:
    tables, cols = set(), {}
    for spec in rr.REVISION_SPEC[: upto + 1]:
        tables |= set(spec["tables"])
        for t, cs in spec["columns"].items():
            cols.setdefault(t, set()).update(cs)
    return tables, cols


def test_classify_clean_baseline_mixed_head():
    t, c = _tables_cols(0)
    r = rr.classify_revisions(t, c)
    assert (r["verdict"], r["at_revision"]) == ("clean-at-revision", rr.BASELINE)
    t, c = _tables_cols(len(rr.REVISION_SPEC) - 1)
    r = rr.classify_revisions(t, c)
    assert (r["verdict"], r["at_revision"]) == ("at-head", rr.HEAD)
    # create_all after the baseline: the later tables exist, the later COLUMNS of the old tables do not
    t, c = _tables_cols(len(rr.REVISION_SPEC) - 1)
    c["users"].discard("job_title")
    r = rr.classify_revisions(t, c)
    assert r["verdict"] == "mixed" and r["revisions"][1]["status"] == "partial"
    assert rr.classify_revisions(set(), {})["verdict"] == "not-a-leadboost-schema"


def test_downgrade_table_covers_every_revision_above_the_baseline():
    assert set(rr.DOWNGRADE_DROPS) == {s["rev"] for s in rr.REVISION_SPEC[1:]}


def _upgrade(db_file: Path, target: str) -> None:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DATABASE_URL": f"sqlite:///{db_file}",
        "SECRET_KEY": "k" * 40,
        "ENVIRONMENT": "development",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    p = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", target],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert p.returncode == 0, p.stderr[-800:]


def test_revision_table_matches_what_alembic_really_creates(tmp_path):
    """For every revision R: after `alembic upgrade R`, the objects of revisions <= R exist and those of later ones do not."""
    db_file = tmp_path / "drift.db"
    for i, spec in enumerate(rr.REVISION_SPEC):
        _upgrade(db_file, spec["rev"])
        eng = create_engine(f"sqlite:///{db_file}")
        insp = inspect(eng)
        tables = set(insp.get_table_names())
        cols = {t: {c["name"] for c in insp.get_columns(t)} for t in tables}
        eng.dispose()
        verdict = rr.classify_revisions(tables - {"alembic_version"}, cols)
        statuses = [r["status"] for r in verdict["revisions"]]
        assert statuses[: i + 1] == ["present"] * (i + 1), (spec["rev"], verdict["revisions"])
        assert statuses[i + 1 :] == ["absent"] * (len(statuses) - i - 1), (spec["rev"], verdict["revisions"])
    head = verdict
    assert head["verdict"] == "at-head" and rr.REVISION_SPEC[-1]["rev"] == rr.HEAD


def test_the_harness_head_constant_is_the_single_alembic_head():
    versions = sorted((BACKEND / "alembic" / "versions").glob("*.py"))
    revs = {}
    for f in versions:
        text = f.read_text(encoding="utf-8")
        rev = next(ln.split("'")[1] for ln in text.splitlines() if ln.startswith("revision:"))
        down = next(
            ln.split("'")[1] if "'" in ln else None
            for ln in text.splitlines()
            if ln.startswith("down_revision:")
        )
        revs[rev] = down
    heads = set(revs) - set(d for d in revs.values() if d)
    assert heads == {rr.HEAD} and len(revs) == len(rr.REVISION_SPEC)
    assert [s["rev"] for s in rr.REVISION_SPEC][0] == rr.BASELINE and revs[rr.BASELINE] is None


def test_both_throwaway_servers_are_accepted():
    rr.assert_rehearsal_server("127.0.0.1", 56432)
    rr.assert_rehearsal_server("127.0.0.1", 56433)
    admin = rr.parse_admin_url("postgresql://postgres:pw@127.0.0.1:56433/postgres")
    assert rr.db_url(admin, "c95_rehearsal_lb_x").endswith("@127.0.0.1:56433/c95_rehearsal_lb_x")


def _wint(v: int) -> bytes:
    return bytes([1 if v < 0 else 0]) + abs(v).to_bytes(4, "little")


def _wstr(s: str) -> bytes:
    raw = s.encode()
    return _wint(len(raw)) + raw


def _archive(
    version=(1, 16, 0), server="17.5 (Debian 17.5-1.pgdg120+1)", dumper="17.5 (Debian 17.5-1.pgdg120+1)"
) -> bytes:
    head = b"PGDMP" + bytes(version) + bytes([4, 8, 1])
    head += bytes([2]) if version >= (1, 15, 0) else _wint(-1)
    head += b"".join(_wint(v) for v in (1, 2, 3, 4, 5, 126, 0))
    head += _wstr("my_secret_database_name")
    if version >= (1, 10, 0):
        head += _wstr(server) + _wstr(dumper)
    return head + b"\x00" * 64


def test_archive_header_reader_gets_versions_and_never_the_database_name(tmp_path):
    f = tmp_path / "d.dump"
    f.write_bytes(_archive())
    hdr = rr.read_archive_header(f)
    assert hdr["archive_version"] == "1.16.0" and hdr["server_version"].startswith("17.5")
    assert hdr["pg_dump_version"].startswith("17.5")
    assert "my_secret_database_name" not in str(hdr)
    f.write_bytes(_archive(version=(1, 14, 0), server="15.4", dumper="15.4"))
    assert rr.read_archive_header(f)["server_version"] == "15.4"
    f.write_bytes(b"not an archive")
    with pytest.raises(ValueError):
        rr.read_archive_header(f)


def test_suggested_major_is_the_newer_of_server_and_pg_dump():
    assert rr.suggest_pg_major({"server_version": "16.3", "pg_dump_version": "17.2"}) == 17
    assert rr.suggest_pg_major({"server_version": "17.5 (Debian)", "pg_dump_version": "16.9"}) == 17
    assert rr.suggest_pg_major({}) is None and rr.major_of("garbage") is None


def test_restore_refuses_before_creating_anything_when_the_client_cannot_read_the_archive(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    tool = fake / "pg_restore"
    tool.write_text(
        "#!/bin/sh\necho 'pg_restore: error: unsupported version (1.16) in file header' >&2\nexit 1\n"
    )
    tool.chmod(0o755)
    dump = tmp_path / "d.dump"
    dump.write_bytes(_archive())
    state = tmp_path / "state"
    env = {
        "PATH": f"{fake}:{os.environ.get('PATH', '')}",
        "REHEARSAL_ADMIN_URL": "postgresql://postgres:pw@127.0.0.1:56432/postgres",
        "REHEARSAL_STATE_DIR": str(state),
    }
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "restore", "--dump", str(dump), "--label", "x"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert p.returncode == 3, p.stdout + p.stderr
    assert "needs a PostgreSQL 17" in p.stderr and "No database was created" in p.stderr
    assert not (
        state / "rehearsal_lb_state.json"
    ).exists(), "nothing may be recorded: no database was created"
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "inspect-dump", "--dump", str(dump)],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert p.returncode == 3 and "postgres:17" in p.stdout and "c95-pg17" in p.stdout and "1.16.0" in p.stdout
    assert "my_secret_database_name" not in p.stdout + p.stderr


def test_a_baseline_table_set_missing_baseline_columns_is_older_than_the_baseline():
    t, c = _tables_cols(0)
    c["ai_decision_logs"].discard("source")  # created by the baseline revision itself
    r = rr.classify_revisions(t, c)
    assert r["verdict"] == "older-than-baseline" and r["at_revision"] is None
    assert r["revisions"][0]["status"] == "partial"
    r = rr.classify_revisions(t, c, {"leads": set()})
    assert any("uq_leads_org_website" in m for m in r["revisions"][0]["missing"])


def test_child_targets_are_sqlite_or_a_rehearsal_database_only():
    rr.assert_child_target("sqlite:////tmp/x.db")
    rr.assert_child_target("postgresql://postgres:pw@127.0.0.1:56433/c95_rehearsal_lb_x")
    for bad in (
        "postgresql://postgres:pw@127.0.0.1:56433/leadboost",
        "postgresql://postgres:pw@db.example.com:5432/c95_rehearsal_lb_x",
        "postgresql://postgres:pw@127.0.0.1:5432/c95_rehearsal_lb_x",
    ):
        with pytest.raises(rr.Refused):
            rr.assert_child_target(bad)


def _schema_diff(db_file: Path) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "DATABASE_URL": f"sqlite:///{db_file}",
        "SECRET_KEY": "k" * 40,
        "ENVIRONMENT": "development",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "child-schema-diff"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert p.returncode == 0, p.stderr[-800:]
    return p.stdout


def test_schema_diff_lists_what_is_missing_at_the_baseline_and_nothing_structural_at_head(tmp_path):
    db_file = tmp_path / "diff.db"
    _upgrade(db_file, rr.BASELINE)
    out = _schema_diff(db_file)
    for expected in (
        "add_table        email_accounts",
        "add_table        outreach_actions",
        "add_table        organization_outreach_policies",
        "add_column       organizations.industry",
        "add_column       users.signature",
    ):
        assert expected in out, (expected, out[:600])
    _upgrade(db_file, "head")
    out = _schema_diff(db_file)
    assert not [
        ln
        for ln in out.splitlines()
        if ln.startswith(("DIFF add_table", "DIFF add_column", "DIFF add_constraint"))
    ], out[:800]
    assert out.strip().splitlines()[-1].startswith("SUMMARY")


def test_apply_sql_only_touches_databases_this_harness_created_and_only_repo_sql(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "REHEARSAL_ADMIN_URL": "postgresql://postgres:pw@127.0.0.1:56432/postgres",
        "REHEARSAL_STATE_DIR": str(state),
    }
    name = "c95_rehearsal_lb_mine_20261127t000000z"

    def run(db: str, sql: str):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "apply-sql", "--db", db, "--file", sql],
            cwd=BACKEND,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    p = run(name, "migrations/001_p0_durable_execution.sql")
    assert p.returncode == 2 and "was not created by this harness" in p.stderr
    (state / "rehearsal_lb_state.json").write_text(json.dumps({"databases": [name]}))
    outside = tmp_path / "evil.sql"
    outside.write_text("DROP DATABASE postgres;")
    p = run(name, str(outside))
    assert p.returncode == 2 and "inside the repository's backend/" in p.stderr
    p = run("postgres", "migrations/001_p0_durable_execution.sql")
    assert p.returncode == 2 and "does not match" in p.stderr


def _fake_docker(tmp_path, monkeypatch, published: str, rc: int = 0):
    """A `docker` on PATH that only answers `docker port <container> 5432/tcp`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "docker"
    script.write_text(
        f'#!/bin/sh\nif [ "$1" = port ]; then printf \'%s\\n\' "{published}"; exit {rc}; fi\nexit 9\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")
    rr._CONTAINER_VERIFIED.clear()


@pytest.mark.parametrize(
    "admin_port,published,rc,accepted",
    [
        (56432, "127.0.0.1:56432", 0, True),
        (56433, "127.0.0.1:56433", 0, True),
        (56432, "127.0.0.1:56433", 0, False),  # the OTHER throw-away server
        (56433, "127.0.0.1:56432", 0, False),
        (56432, "", 0, False),
        (56432, "0.0.0.0:56432", 0, False),
        (56432, "127.0.0.1:56432", 1, False),
    ],
)
def test_docker_client_container_must_serve_the_validated_endpoint(
    tmp_path, monkeypatch, admin_port, published, rc, accepted
):
    _fake_docker(tmp_path, monkeypatch, published, rc)
    monkeypatch.setenv("REHEARSAL_PG_CLIENT", "docker:c95-pg17")
    admin = rr.parse_admin_url(f"postgresql://postgres:pw@127.0.0.1:{admin_port}/postgres")
    if accepted:
        cmd, _env = rr.pg_client_cmd(admin, "pg_dump", "c95_rehearsal_lb_x", ["-Fc"])
        assert cmd[:7] == ["docker", "exec", "-i", "-e", "PGPASSWORD", "c95-pg17", "pg_dump"]
    else:
        with pytest.raises(rr.Refused, match="must be the guarded server|cannot ask docker"):
            rr.pg_client_cmd(admin, "pg_dump", "c95_rehearsal_lb_x", ["-Fc"])


def test_docker_client_with_a_non_c95_name_is_refused_before_docker_is_asked(tmp_path, monkeypatch):
    _fake_docker(tmp_path, monkeypatch, "127.0.0.1:56432")
    monkeypatch.setenv("REHEARSAL_PG_CLIENT", "docker:my-production-db")
    admin = rr.parse_admin_url("postgresql://postgres:pw@127.0.0.1:56432/postgres")
    with pytest.raises(rr.Refused, match="named c95-"):
        rr.pg_client_cmd(admin, "pg_dump", "c95_rehearsal_lb_x", [])
