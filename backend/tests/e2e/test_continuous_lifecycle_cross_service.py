"""
C12 continuation: ONE continuous LeadBoost -> Mailer -> SMTP -> IMAP -> LeadBoost lifecycle.

    LeadBoost customer API (real app, real DB)
      -> POST email-account + POST .../verify        LeadBoost's own real SMTP STARTTLS+AUTH login to the sink,
                                                     then the REAL LeadBoost client provisions the Mailer mailbox
      -> operator PATCH of disposable IMAP settings  the approved STAGING-ONLY step, over real HTTP, org key
      -> POST outreach-actions/{id}/dispatch         real LeadBoost client -> separate Mailer API process (HTTP)
      -> Mailer: durable QUEUED ExternalDispatch     PostgreSQL (disposable `mailer_agent_test`)
      -> run_generation_cycle                        the Mailer's REAL production function (LLM = existing fake)
      -> run_external_dispatch_cycle                 REAL production function -> real smtplib STARTTLS + AUTH -> sink
      -> reply APPENDed into a disposable Dovecot    with In-Reply-To / References = the Message-ID on the wire
      -> poll_all_mailboxes                          REAL M3 poll over real IMAP, correlation, mailbox binding
      -> GET /api/v2/outreach-actions/{id}/mailer-state
                                                     real LeadBoost client -> real C9.3 endpoint -> product response

Nothing the Mailer's worker would produce is written into its database by the test: generated, sent and
inbound state all come from the Mailer's own code. SQL is used for READS only (through the Mailer's
interpreter; see mailer_lifecycle_driver.py, which also documents why the cycles are run one at a time
instead of starting the long-running scheduler).

Needs: a Mailer checkout, `dovecot` + root, and a disposable PostgreSQL named `mailer_agent_test`
(POSTGRES_TEST_URL + C12_ALLOW_DESTRUCTIVE_TEST_DB=1, the same guard as the Mailer's own C12 test):

    sudo -E env "PATH=$PATH" MAILER_REPO_PATH=... MAILER_PYTHON=... POSTGRES_TEST_URL=... \\
        C12_ALLOW_DESTRUCTIVE_TEST_DB=1 pytest tests/e2e/test_continuous_lifecycle_cross_service.py

Unavailable prerequisites SKIP locally and FAIL in CI (E2E_REQUIRE_CONTINUOUS=1): a skip is not a pass.
"""

import base64
import json
import logging
import os
import subprocess
import uuid
from email import message_from_bytes
from pathlib import Path

import httpx
import pytest
from sqlalchemy.engine import make_url

from core.domain.models.outreach_action import OutreachState
from tests.application.test_outreach_dispatch_l1 import _approved, _dispatch, _lead
from tests.e2e.mailer_process import MailerProcess, MailerUnavailableForTests

# Reused as-is: the app client, the org -> Mailer-key wiring and the read helpers of the existing E2E.
from tests.e2e import test_mailer_state_cross_service as _existing

client, env = _existing.client, _existing.env  # pytest discovers fixtures by module attribute
_action_row, _state = _existing._action_row, _existing._state

REPO = os.environ.get("MAILER_REPO_PATH")
PYTHON = os.environ.get("MAILER_PYTHON", "python3")
REQUIRED = os.environ.get("E2E_REQUIRE_CONTINUOUS") == "1"
PG_URL = os.environ.get("POSTGRES_TEST_URL")
DRIVER = Path(__file__).with_name("mailer_lifecycle_driver.py")

pytestmark = pytest.mark.skipif(
    not REPO and not REQUIRED,
    reason="set MAILER_REPO_PATH (+ dovecot, root, POSTGRES_TEST_URL) to run the continuous lifecycle E2E",
)

# The offer text the shared `env` fixture sets on every organization (it is what LeadBoost forwards as the
# value proposition, and what the Mailer's generation grounds on).
OFFER = "We help B2B teams ship reliable developer tooling."
SENDER = "sales@continuous.example.org"
REPLY_TOKEN = f"CONT-REPLY-{uuid.uuid4().hex}"
REPLY_BODY = f"Yes, happy to talk. <script>alert('cont')</script> {REPLY_TOKEN}"
REPLY_SUBJECT = f"Re: Quick question {REPLY_TOKEN}"
SUBJECT = "Quick question"


def _unavailable(reason: str):
    if REQUIRED:
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


class Driver:
    """Runs mailer_lifecycle_driver.py in the Mailer's own interpreter, in the Mailer checkout."""

    def __init__(self, base_env):
        self.base_env = base_env
        self.stderr = []  # everything the Mailer's code logged while the cycles ran

    def run(self, *args, extra_env=None, expect_ok=True):
        env_ = {**self.base_env, **(extra_env or {})}
        done = subprocess.run(
            [PYTHON, str(DRIVER), *map(str, args)],
            cwd=REPO,
            env=env_,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.stderr.append(done.stderr)
        if expect_ok:
            assert (
                done.returncode == 0
            ), f"driver {args[0]} failed ({done.returncode}):\n{done.stderr[-3000:]}"
            return json.loads(done.stdout.strip().splitlines()[-1])
        return done

    def sql(self, statement):
        return self.run("query", statement)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@pytest.fixture()
def postgres_url():
    if not PG_URL:
        _unavailable(
            "POSTGRES_TEST_URL is not set (the continuous E2E is PostgreSQL-only; no SQLite fallback)"
        )
    url = make_url(PG_URL)
    if os.environ.get("C12_ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        pytest.fail(
            "Refusing destructive PostgreSQL test without C12_ALLOW_DESTRUCTIVE_TEST_DB=1", pytrace=False
        )
    if (
        not url.drivername.startswith("postgresql")
        or url.host not in {"localhost", "127.0.0.1", "::1"}
        or url.database != "mailer_agent_test"
    ):
        pytest.fail("Requires a loopback PostgreSQL database named mailer_agent_test", pytrace=False)
    return PG_URL


@pytest.fixture()
def mailer_server(postgres_url):
    """The real Mailer API as a separate process, on the disposable PostgreSQL (schema reset first)."""
    if not REPO:
        pytest.fail("E2E_REQUIRE_CONTINUOUS=1 but MAILER_REPO_PATH is not set")
    base = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    base.update(DATABASE_URL=postgres_url, C12_ALLOW_DESTRUCTIVE_TEST_DB="1")
    reset = subprocess.run(
        [PYTHON, str(DRIVER), "reset-db"], cwd=REPO, env=base, capture_output=True, text=True, timeout=120
    )
    if reset.returncode != 0:
        pytest.fail(f"could not reset the disposable database:\n{reset.stderr[-2000:]}", pytrace=False)
    proc = MailerProcess(REPO, PYTHON, database_url=postgres_url)
    try:
        proc.start()
    except MailerUnavailableForTests as exc:
        proc.stop()
        _unavailable(str(exc))
    try:
        yield proc
    finally:
        proc.stop()


@pytest.fixture()
def infra(mailer_server):
    """Disposable Dovecot (IMAP) + STARTTLS/AUTH SMTP sink, started by the Mailer's interpreter."""
    capture = Path(mailer_server.tmp.name) / "smtp-capture.jsonl"
    proc = subprocess.Popen(
        [PYTHON, str(DRIVER), "infra", str(capture)],
        cwd=REPO,
        env={**mailer_server.process_env},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = proc.stdout.readline()
        if not line:
            proc.wait(timeout=30)
            _unavailable(
                f"disposable Dovecot/SMTP unavailable (needs dovecot + root): {proc.stderr.read()[-1500:]}"
            )
        info = json.loads(line)
        info["capture"] = capture
        yield info
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=30)
        except Exception:
            proc.kill()


def _wire_messages(infra):
    path = infra["capture"]
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_continuous_leadboost_mailer_smtp_imap_leadboost_lifecycle(
    client, mailer_server, infra, env, monkeypatch, caplog
):
    caplog.set_level(logging.DEBUG)
    # LeadBoost's own SMTP verification (ssl.create_default_context) and the Mailer cycles trust the
    # throw-away certificate through SSL_CERT_FILE -- no code change, nothing disabled.
    monkeypatch.setenv("SSL_CERT_FILE", infra["cert_file"])
    base_env = {
        **mailer_server.process_env,
        "SSL_CERT_FILE": infra["cert_file"],
        "C12_ALLOW_DESTRUCTIVE_TEST_DB": "1",
    }
    drv = Driver(base_env)
    key_a = "e2e-key-a"
    mailer_headers = {"X-API-Key": key_a}
    t = env(client, "cont_a", key_a)

    # ---- 1. customer connects a sender. LeadBoost verifies it for REAL (STARTTLS + AUTH to the sink), then
    #         provisions the Mailer mailbox with the real client. Nothing patched.
    created = client.post(
        f"/api/v2/organizations/{t.org}/email-accounts",
        headers=t.h,
        json=dict(
            provider="smtp",
            email_address=SENDER,
            display_name="Sales",
            smtp_host="127.0.0.1",
            smtp_port=infra["smtp_port"],
            security_mode="starttls",
            username=infra["smtp_user"],
            credential_type="smtp_password",
            credential=infra["smtp_password"],
        ),
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    verified = client.post(f"/api/v2/organizations/{t.org}/email-accounts/{account_id}/verify", headers=t.h)
    assert verified.status_code == 200, verified.text
    assert verified.json()["verification_status"] == "verified", verified.text
    assert verified.json()["mailer_sync_state"] == "synced", verified.text
    auth_log = [json.loads(x) for x in Path(infra["auth_log"]).read_text().splitlines()]
    assert auth_log and all(a["ok"] for a in auth_log)  # LeadBoost and the Mailer both authenticated for real
    assert _wire_messages(infra) == []  # verification never sends mail

    # ---- 2. approved STAGING-ONLY operator step: disposable IMAP config onto the Mailer-owned mailbox,
    #         over real HTTP with the organization's own key (LeadBoost has no IMAP provisioning).
    listed = httpx.get(f"{mailer_server.base_url}/mailboxes", headers=mailer_headers)
    assert listed.status_code == 200, listed.text
    boxes = [b for b in listed.json() if b["email_address"] == SENDER]
    assert len(boxes) == 1 and boxes[0]["status"] == "active" and boxes[0]["smtp_host"] == "127.0.0.1"
    mailbox_ref = boxes[0]["public_reference"]
    imap_patch = httpx.patch(
        f"{mailer_server.base_url}/mailboxes/{mailbox_ref}",
        headers=mailer_headers,
        json={
            "imap_host": "localhost",
            "imap_port": infra["imap_port"],
            "imap_username": infra["imap_user"],
            "imap_password": infra["imap_password"],
        },
    )
    assert imap_patch.status_code == 200, imap_patch.text
    assert imap_patch.json()["imap_host"] == "localhost"
    assert infra["imap_password"] not in imap_patch.text and infra["smtp_password"] not in imap_patch.text

    # ---- 3. customer approves and dispatches. Real LeadBoost client -> separate Mailer process over HTTP.
    lead_id = _lead(t.org, t.uid)
    action_id = _approved(client, t.h, lead_id, account_id)
    dispatched = _dispatch(client, t.h, action_id)
    assert (
        dispatched.status_code == 200 and dispatched.json()["state"] == OutreachState.SUBMITTED
    ), dispatched.text
    idem = _action_row(action_id)["idempotency_key"]
    where = f"idempotency_key = {_quote(idem)}"

    # ---- 4. the Mailer durably accepted it: QUEUED, org- and mailbox-bound, nothing generated or sent.
    (d,) = drv.sql(
        f"SELECT state, message_id, organization_id, mailbox_id FROM external_dispatches WHERE {where}"
    )
    assert (d["state"], d["message_id"], d["organization_id"]) == ("queued", None, "tenant-a")
    (box,) = drv.sql("SELECT id, organization_id, status FROM mailboxes")
    assert d["mailbox_id"] == box["id"] and box["organization_id"] == "tenant-a"
    before_generation = _state(client, t, action_id).json()
    assert before_generation["availability"] == "available"
    assert before_generation["mailer"]["delivery_state"] == "queued"
    assert before_generation["mailer"]["messages"] == [] and _wire_messages(infra) == []

    # ---- 5. the Mailer's REAL generation cycle (deterministic fake LLM): a Message appears, nothing is sent.
    generated_body = f"Hi Jamie,\n\n{OFFER}\n\nWorth a quick chat?\n\nBest,\nSales"
    gen = drv.run(
        "generate",
        json.dumps({"subject": SUBJECT, "body": generated_body, "reasoning": "continuous e2e"}),
    )
    assert gen["results"] == [{"outcome": "generated", "reason": None, "persisted": True}], gen
    assert gen["llm_calls"] == 1
    assert _wire_messages(infra) == []
    (d,) = drv.sql(f"SELECT state, message_id FROM external_dispatches WHERE {where}")
    assert d["state"] == "queued" and d["message_id"] is not None
    (m,) = drv.sql(f"SELECT direction, status, message_id_header FROM messages WHERE id = {d['message_id']}")
    assert (
        m["direction"] == "outbound" and m["message_id_header"] is None
    )  # the Message-ID is minted at send time

    # ---- 6. the Mailer's REAL dispatch cycle -> real SMTP. LIVE_SENDING_ENABLED is true ONLY in this isolated
    #         process, and the driver refuses to send unless the mailbox points at the local sink.
    sent = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert [r["outcome"] for r in sent["results"]] == ["sent"], sent
    wire_all = _wire_messages(infra)
    assert len(wire_all) == 1
    wire = wire_all[0]
    wire_msg = message_from_bytes(base64.b64decode(wire["raw_b64"]))
    wire_message_id = wire_msg["Message-ID"]
    assert wire["tls"] and wire["authenticated"]
    assert wire["mail_from"] == SENDER and wire["rcpt_tos"] == ["lead@example.com"]
    assert SENDER in wire_msg["From"] and wire_msg["Subject"] == SUBJECT
    first_part = wire_msg.get_payload()[0] if wire_msg.is_multipart() else wire_msg
    wire_body = first_part.get_payload(decode=True).decode().replace("\r\n", "\n").strip()
    assert wire_body == generated_body.strip()
    # C6-C8, observed for real while SMTP held the message: Message-ID already committed and equal to the wire
    # header, row SENDING, and no database session idle in a transaction.
    assert wire["during_smtp"]["state"] == "sending"
    assert wire["during_smtp"]["persisted_message_id"] == wire_message_id
    assert wire["during_smtp"]["idle_in_tx"] == 0
    (d,) = drv.sql(
        f"SELECT d.state, d.contact_id, m.message_id_header FROM external_dispatches d "
        f"JOIN messages m ON m.id = d.message_id WHERE d.{where}"
    )
    assert d["state"] == "sent" and d["message_id_header"] == wire_message_id
    contact_id = d["contact_id"]

    after_send = _state(client, t, action_id)
    assert after_send.json()["mailer"]["delivery_state"] == "sent"
    assert [x["direction"] for x in after_send.json()["mailer"]["messages"]] == ["outbound"]
    assert after_send.json()["mailer"]["messages"][0]["body"].strip() == generated_body.strip()
    action_after_dispatch = _action_row(action_id)  # LeadBoost never heals/rewrites it from Mailer state

    # ---- 7. the recipient replies, into the disposable IMAP mailbox, referencing the REAL outbound Message-ID.
    imap = (infra["imap_port"], infra["imap_user"], infra["imap_password"])
    inbound_message_id = f"<cont-reply-{uuid.uuid4().hex}@prospect.example>"
    drv.run(
        "imap",
        "deliver",
        *imap,
        json.dumps(
            {
                "from": "lead@example.com",
                "to": SENDER,
                "subject": REPLY_SUBJECT,
                "body": REPLY_BODY,
                "message_id": inbound_message_id,
                "in_reply_to": wire_message_id,
            }
        ),
    )
    assert drv.run("imap", "unseen", *imap) == {"unseen": 1}

    # ---- 8. the Mailer's REAL M3 poll. Auto-reply is ON (and no LLM is available): a LeadBoost-managed
    #         contact's reply must be stored, never handled by Mailer-native automation.
    poll = drv.run("poll", extra_env={"AUTO_REPLY_ENABLED": "true", "GROQ_API_KEY": ""})
    assert [(p["status"], p["processed"], p["left_unseen"]) for p in poll] == [("ok", 1, 0)], poll
    assert drv.run("imap", "unseen", *imap) == {"unseen": 0}  # marked Seen only after the commit
    inbound = drv.sql(
        "SELECT i.mailbox_id, i.in_reply_to_header, i.body, i.contact_id, mb.organization_id "
        "FROM messages i JOIN mailboxes mb ON mb.id = i.mailbox_id WHERE i.direction = 'inbound'"
    )
    assert len(inbound) == 1
    assert inbound[0]["mailbox_id"] == box["id"] and inbound[0]["organization_id"] == "tenant-a"
    assert inbound[0]["in_reply_to_header"] == wire_message_id and inbound[0]["contact_id"] == contact_id
    assert inbound[0]["body"].strip() == REPLY_BODY
    assert len(_wire_messages(infra)) == 1  # inbound handling sent nothing, even with auto-reply on
    assert drv.sql("SELECT count(*) AS n FROM messages WHERE direction = 'outbound'") == [{"n": 1}]
    # The discriminating integration-guard assertion: the native path would reschedule a follow-up.
    assert drv.sql(f"SELECT next_action_at FROM contacts WHERE id = {contact_id}") == [
        {"next_action_at": None}
    ]

    # ---- 9. the customer-facing result, through the real LeadBoost client and the real C9.3 endpoint.
    final = _state(client, t, action_id)
    assert final.status_code == 200 and final.headers["cache-control"] == "no-store"
    view = final.json()
    assert view["availability"] == "available" and view["error_code"] is None
    mailer = view["mailer"]
    assert mailer["delivery_state"] == "sent" and mailer["has_more"] is False
    assert [(x["direction"], x["delivery_state"]) for x in mailer["messages"]] == [
        ("outbound", "sent"),
        ("inbound", None),
    ]
    out_view, in_view = mailer["messages"]
    assert out_view["body"].strip() == generated_body.strip() and out_view["subject"] == SUBJECT
    assert in_view["body"] == REPLY_BODY and in_view["subject"] == REPLY_SUBJECT  # verbatim data
    assert set(out_view) == {"direction", "subject", "body", "body_truncated", "created_at", "delivery_state"}

    # ---- 10. nothing internal or secret reaches the browser.
    for forbidden in (
        infra["smtp_password"],
        infra["imap_password"],
        infra["smtp_user"],
        infra["imap_user"],
        SENDER,
        wire_message_id,
        wire_message_id.strip("<>"),
        inbound_message_id,
        inbound_message_id.strip("<>"),
        mailer_server.base_url,
        str(mailer_server.port),
        key_a,
        "tenant-a",
        mailbox_ref,
        idem,
        "x-api-key",
        "/integrations",
        "mailing_agent_reference",
        "mailbox_reference",
        "in-reply-to",
    ):
        assert forbidden.lower() not in final.text.lower(), forbidden

    # ---- 11. re-poll / re-read neither duplicates the reply nor mutates delivery state or LeadBoost.
    snapshot = drv.sql(f"SELECT id, state, updated_at FROM external_dispatches WHERE {where}")
    drv.run("imap", "forget-seen", *imap)
    assert drv.run("imap", "unseen", *imap) == {"unseen": 1}  # the server offers the same message again
    drv.run("poll", extra_env={"AUTO_REPLY_ENABLED": "true", "GROQ_API_KEY": ""})
    assert drv.run("imap", "unseen", *imap) == {"unseen": 0}
    assert drv.sql("SELECT count(*) AS n FROM messages WHERE direction = 'inbound'") == [{"n": 1}]
    assert drv.sql(f"SELECT id, state, updated_at FROM external_dispatches WHERE {where}") == snapshot
    again = _state(client, t, action_id)
    assert again.json() == view
    assert _state(client, t, action_id).json() == view
    assert len(_wire_messages(infra)) == 1
    assert _action_row(action_id) == action_after_dispatch
    assert (
        client.get(f"/api/v2/outreach-actions/{action_id}", headers=t.h).json()["state"]
        == OutreachState.SUBMITTED
    )

    # ---- 12. logs: no credential, no ciphertext, no attacker-controlled reply text -- Mailer API process,
    #          every Mailer cycle, and LeadBoost's own logs.
    ciphertexts = drv.sql("SELECT smtp_password_enc, imap_password_enc FROM mailboxes")
    secrets = [
        infra["smtp_password"],
        infra["imap_password"],
        mailer_server.process_env["MAILBOX_ENCRYPTION_KEY"],
    ]
    for row in ciphertexts:
        secrets += [v for v in row.values() if v]
    haystacks = {
        "mailer api log": mailer_server.log_tail(10**9),
        "mailer cycles": "\n".join(drv.stderr),
        "leadboost": caplog.text,
    }
    assert haystacks["mailer cycles"].strip() and haystacks["mailer api log"].strip()
    for name, text in haystacks.items():
        for secret in secrets:
            assert secret and secret not in text, f"secret in {name}"
        assert REPLY_TOKEN not in text and "alert('cont')" not in text, f"reply text in {name}"
