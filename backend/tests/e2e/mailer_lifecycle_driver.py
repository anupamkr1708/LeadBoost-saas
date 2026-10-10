"""
Mailer-side driver for the continuous LeadBoost -> Mailer -> SMTP -> IMAP -> LeadBoost E2E.

THIS FILE IS EXECUTED BY THE MAILER'S OWN INTERPRETER (MAILER_PYTHON, cwd = the Mailer checkout),
never imported by LeadBoost: the two services pin mutually incompatible dependencies.

It exists because the Mailer's asynchronous work -- generation, SMTP delivery, IMAP polling -- is
done by its worker, and the test must exercise that real implementation instead of writing its
outcome into the database. What it does and, just as important, does NOT do:

  * It calls the Mailer's REAL production cycle functions, one cycle per invocation, against the same
    disposable PostgreSQL database the Mailer API process uses:
        mailer_agent.mail.outreach_generation_worker.run_generation_cycle
        mailer_agent.mail.external_dispatch_worker.run_external_dispatch_cycle
        mailer_agent.mail.mailbox_inbound.poll_all_mailboxes
  * It does NOT start worker.py / APScheduler (a long-running scheduler would make the test timing
    dependent). The scheduler only decides WHEN these same functions run; the claim -> generate ->
    Txn B -> SMTP -> Txn C -> IMAP poll -> correlation code is the production code, unmodified.
  * It writes NOTHING into the Mailer's tables to simulate an outcome. The only SQL it runs is a
    SELECT-only `query` (assertions), and `reset-db` on a guarded disposable database (below).
  * The only fake is the LLM: the existing tests/fake_llm_provider.py, so generation is
    deterministic and no production LLM (Groq) is ever called.

Disposable infrastructure (`infra`): a throw-away Dovecot (the Mailer's own tests/test_m3_imap_e2e.py
fixture code, reused as-is) and a STARTTLS-required, AUTH-required aiosmtpd sink, on 127.0.0.1 with a
generated self-signed certificate and generated credentials. Nothing is sent anywhere else: `dispatch`
refuses to run unless every ACTIVE mailbox points at the sink on loopback.

Each one-shot command prints exactly one JSON document on the LAST line of stdout. Mailer logs go to
stderr, which the test captures to check that no credential or message text is logged.
"""

from __future__ import annotations

import base64
import imaplib
import json
import logging
import os
import socket
import ssl
import sys
import uuid
from pathlib import Path

sys.path.insert(0, os.getcwd())  # the Mailer checkout: `mailer_agent` and its `tests` package

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
EXIT_UNAVAILABLE, EXIT_GUARD, EXIT_REFUSED = 3, 4, 5


def _out(doc) -> None:
    sys.stdout.write(json.dumps(doc, default=str) + "\n")
    sys.stdout.flush()


def _die(code: int, message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.exit(code)


def _engine():
    from mailer_agent.db import engine

    # Fail loudly rather than degrade: this is evidence about PostgreSQL, not about SQLite.
    if engine.dialect.name != "postgresql":
        _die(EXIT_GUARD, f"continuous E2E requires PostgreSQL, got {engine.dialect.name}")
    return engine


def _guard_destructive_database() -> None:
    """Same guard as the Mailer's own C12 test: explicit opt-in, loopback host, dedicated database."""
    from sqlalchemy.engine import make_url

    url = make_url(os.environ.get("DATABASE_URL", ""))
    if os.environ.get("C12_ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        _die(EXIT_GUARD, "refusing destructive reset without C12_ALLOW_DESTRUCTIVE_TEST_DB=1")
    if (
        not url.drivername.startswith("postgresql")
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database != "mailer_agent_test"
    ):
        _die(
            EXIT_GUARD,
            "refusing destructive reset: need a loopback PostgreSQL database named mailer_agent_test",
        )


# ----------------------------------------------------------------------------------------------
def cmd_reset_db(_args) -> None:
    _guard_destructive_database()
    from mailer_agent.models import Base

    eng = _engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    _out({"reset": True})


def cmd_query(args) -> None:
    from sqlalchemy import text

    sql = args[0]
    if not sql.lstrip().lower().startswith("select") or ";" in sql:
        _die(EXIT_REFUSED, "query accepts one SELECT statement only")
    with _engine().connect() as c:
        c.execute(text("SET TRANSACTION READ ONLY"))
        rows = [dict(r._mapping) for r in c.execute(text(sql))]
    _out(rows)


def cmd_generate(args) -> None:
    """One real generation cycle; the LLM is the existing deterministic fake, queued by the test."""
    import pytest

    from mailer_agent.mail import outreach_generation_worker as gen
    from tests.fake_llm_provider import install_fake_llm_provider

    _engine()
    fake = install_fake_llm_provider(pytest.MonkeyPatch())
    fake.queue_response(json.loads(args[0]))
    results = gen.run_generation_cycle(worker_id="e2e-generation")
    _out(
        {
            "results": [
                {"outcome": r.outcome, "reason": r.reason, "persisted": r.persisted} for r in results
            ],
            "llm_calls": fake.call_count,
        }
    )


def cmd_dispatch(args) -> None:
    """One real dispatch cycle -> real smtplib over STARTTLS + AUTH to the sink, and nowhere else."""
    from sqlalchemy import text

    from mailer_agent.config import get_settings
    from mailer_agent.mail import external_dispatch_worker as dispatch

    sink_port = int(args[0])
    with _engine().connect() as c:
        boxes = c.execute(text("SELECT smtp_host, smtp_port FROM mailboxes WHERE status = 'active'")).all()
    if not boxes or any(h not in LOOPBACK or int(p) != sink_port for h, p in boxes):
        _die(EXIT_REFUSED, "refusing to send: an active mailbox does not point at the local SMTP sink")
    if not get_settings().live_sending_enabled:
        _die(
            EXIT_REFUSED,
            "LIVE_SENDING_ENABLED must be true in this isolated process (otherwise dispatch FAILs)",
        )
    results = dispatch.run_external_dispatch_cycle(worker_id="e2e-dispatch")
    _out(
        {
            "results": [
                {
                    "outcome": r.outcome,
                    "reason": r.reason,
                    "smtp_attempted": r.smtp_attempted,
                    "persisted": r.persisted,
                }
                for r in results
            ]
        }
    )


def cmd_poll(_args) -> None:
    """One real M3 poll over every eligible mailbox (real IMAP, UID FETCH BODY.PEEK, Seen after commit)."""
    from mailer_agent.mail import mailbox_inbound

    _engine()
    outcomes = mailbox_inbound.poll_all_mailboxes()
    _out(
        [
            {
                "mailbox_id": o.mailbox_id,
                "status": o.status,
                "processed": o.processed,
                "left_unseen": o.left_unseen,
            }
            for o in outcomes
        ]
    )


# --- IMAP helpers: the same protocol operations the Mailer's Dovecot test uses ------------------
def _imap(port: int, user: str, password: str):
    c = imaplib.IMAP4_SSL("localhost", port, timeout=15)  # trusts the throw-away cert via SSL_CERT_FILE
    c.login(user, password)
    return c


def cmd_imap(args) -> None:
    action, port, user, password = args[0], int(args[1]), args[2], args[3]
    c = _imap(port, user, password)
    try:
        if action == "deliver":
            from tests.m3_support import raw_email

            spec = json.loads(args[4])
            raw = raw_email(
                from_addr=spec["from"],
                to_addr=spec["to"],
                subject=spec["subject"],
                body=spec["body"],
                message_id=spec["message_id"],
                in_reply_to=spec["in_reply_to"],
                references=[spec["in_reply_to"]],
            )
            assert c.append("INBOX", None, None, raw)[0] == "OK"
            _out({"delivered": True})
        else:
            c.select("INBOX")
            if action == "forget-seen":
                c.uid("STORE", "1:*", "-FLAGS", "\\Seen")
            _out({"unseen": len(c.uid("SEARCH", None, "UNSEEN")[1][0].split())})
    finally:
        c.logout()


# --- disposable infrastructure ------------------------------------------------------------------
def cmd_infra(args) -> None:
    """Long-running: Dovecot + SMTP sink. Prints one JSON line when ready; stops when stdin closes."""
    capture = Path(args[0])
    try:
        from tests.test_m3_imap_e2e import dovecot as dovecot_fixture
    except BaseException as exc:  # the module skips itself without the dovecot binary / root
        _die(EXIT_UNAVAILABLE, f"disposable Dovecot unavailable: {type(exc).__name__}: {exc}")
    make = getattr(dovecot_fixture, "__pytest_wrapped__", None)
    make = make.obj if make is not None else dovecot_fixture
    fixture = make()  # the reused session fixture is a generator: yield = ready, close = tear down
    dc = next(fixture)
    sink = None
    try:
        from aiosmtpd.controller import Controller
        from aiosmtpd.smtp import AuthResult, LoginPassword

        user, password = f"u{uuid.uuid4().hex[:10]}@e2e.example", f"Pw-{uuid.uuid4().hex}"
        dc.accounts[user] = password
        dc._write_users()
        dc._wait_until_accepted(user)
        smtp_user, smtp_password = f"smtp-{uuid.uuid4().hex[:8]}", f"Sp-{uuid.uuid4().hex}"
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(dc.root / "cert.pem"), str(dc.root / "key.pem"))

        def probe():
            """What the database says WHILE the SMTP server is holding the message mid-flight."""
            from sqlalchemy import text

            with _engine().connect() as c:
                row = c.execute(
                    text(
                        "SELECT d.state, m.message_id_header FROM external_dispatches d "
                        "LEFT JOIN messages m ON m.id = d.message_id ORDER BY d.id DESC LIMIT 1"
                    )
                ).first()
                idle = c.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                        "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
                    )
                ).scalar()
            return {
                "state": row[0] if row else None,
                "persisted_message_id": row[1] if row else None,
                "idle_in_tx": idle,
            }

        class Handler:
            async def handle_DATA(self, server, session, envelope):
                record = {
                    "mail_from": envelope.mail_from,
                    "rcpt_tos": list(envelope.rcpt_tos),
                    "raw_b64": base64.b64encode(envelope.content).decode(),
                    "tls": getattr(session, "ssl", None) is not None,
                    "authenticated": bool(session.authenticated),
                    "during_smtp": probe(),
                }
                with capture.open("a") as fh:
                    fh.write(json.dumps(record) + "\n")
                return "250 OK queued"

        auth_log = capture.with_suffix(".auth")

        def authenticator(server, session, envelope, mechanism, auth_data):
            ok = (
                isinstance(auth_data, LoginPassword)
                and auth_data.login.decode() == smtp_user
                and auth_data.password.decode() == smtp_password
            )
            with auth_log.open("a") as fh:
                fh.write(json.dumps({"mechanism": mechanism, "ok": ok}) + "\n")
            return AuthResult(success=ok)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            smtp_port = s.getsockname()[1]
        sink = Controller(
            Handler(),
            hostname="127.0.0.1",
            port=smtp_port,
            tls_context=ctx,
            require_starttls=True,
            authenticator=authenticator,
            auth_required=True,
            auth_require_tls=True,
        )
        sink.start()
        _out(
            {
                "cert_file": str(dc.root / "cert.pem"),
                "imap_port": dc.port,
                "imap_user": user,
                "imap_password": password,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "smtp_password": smtp_password,
                "auth_log": str(auth_log),
            }
        )
        sys.stdin.read()  # parent closes our stdin (or dies) -> tear everything down
    finally:
        if sink is not None:
            sink.stop()
        fixture.close()


COMMANDS = {
    "reset-db": cmd_reset_db,
    "query": cmd_query,
    "generate": cmd_generate,
    "dispatch": cmd_dispatch,
    "poll": cmd_poll,
    "imap": cmd_imap,
    "infra": cmd_infra,
}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        _die(2, f"usage: {sys.argv[0]} {{{'|'.join(COMMANDS)}}} ...")
    COMMANDS[sys.argv[1]](sys.argv[2:])
