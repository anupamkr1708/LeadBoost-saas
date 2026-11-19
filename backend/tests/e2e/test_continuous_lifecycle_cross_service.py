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

C9.5 continues the same live conversation with the exact-content reply approval gate: the reply is generated into
the Mailer's approval HOLD, read back verbatim by LeadBoost, approved by DIGEST, explicitly sent, and the bytes on the
SMTP wire are proven equal to the approved text. Three attack scenarios run against the live system: a foreign tenant
(at LeadBoost and at the real Mailer), a cancelled draft, and content altered after approval.

Needs: a Mailer checkout, `dovecot` + root, and a disposable PostgreSQL named `mailer_agent_test`
(POSTGRES_TEST_URL + C12_ALLOW_DESTRUCTIVE_TEST_DB=1, the same guard as the Mailer's own C12 test):

    sudo -E env "PATH=$PATH" MAILER_REPO_PATH=... MAILER_PYTHON=... POSTGRES_TEST_URL=... \\
        C12_ALLOW_DESTRUCTIVE_TEST_DB=1 pytest tests/e2e/test_continuous_lifecycle_cross_service.py

Unavailable prerequisites SKIP locally and FAIL in CI (E2E_REQUIRE_CONTINUOUS=1): a skip is not a pass.
"""

import base64
import hashlib
import json
import logging
import os
import subprocess
import re
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


def _content_digest(subject: str, body: str) -> str:
    """The managed-reply content digest (version 1) as DOCUMENTED -- canonical JSON of {"body","subject"} with sorted
    keys and compact separators, UTF-8, SHA-256 -- implemented here independently of both services, so the E2E
    proves the real Mailer's digest matches the contract rather than merely agreeing with itself."""
    payload = json.dumps({"body": body, "subject": subject}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _wire_content(record: dict) -> tuple:
    """(subject, body) of a captured SMTP message exactly as a recipient's mail client decodes them.

    The ONLY normalisation applied is the transport's own: SMTP DATA carries CRLF line endings, so CRLF -> LF. There is
    deliberately NO strip(): a leading or trailing space, a final newline, or a Unicode difference must show up as a
    difference. Non-ASCII subjects (RFC 2047) and bodies (base64/quoted-printable UTF-8) are decoded by the standard
    library's modern policy. Any bare CR left after the CRLF conversion is itself a failure."""
    from email import policy

    msg = message_from_bytes(base64.b64decode(record["raw_b64"]), policy=policy.default)
    part = msg.get_body(preferencelist=("plain",))
    assert part is not None, "no text/plain part captured"
    text = part.get_content().replace("\r\n", "\n")
    assert "\r" not in text, "a bare CR survived: the body was not delivered as LF/CRLF text"
    return str(msg["Subject"]), text


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
    first_subject, first_body = _wire_content(wire)
    assert (first_subject, first_body) == (SUBJECT, generated_body)        # exact: no strip(), CRLF->LF only
    assert first_part.get_payload(decode=True).decode().replace("\r\n", "\n") == generated_body
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
    assert set(out_view) == {
        "direction", "subject", "body", "body_truncated", "created_at", "delivery_state", "message_reference",
    }
    assert out_view["message_reference"] is None            # outbound messages carry no reply reference
    inbound_ref = in_view["message_reference"]              # C9.4: the Mailer's OPAQUE reply token, never a row id
    assert re.fullmatch(r"[0-9a-f]{32}", inbound_ref)
    inbound_row = drv.sql("SELECT id, reply_reference FROM messages WHERE direction = 'inbound'")
    assert [r["reply_reference"] for r in inbound_row] == [inbound_ref] and inbound_ref != str(inbound_row[0]["id"])

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

    # ======================================================================================================
    # C9.4 -- the NEW reply workflow, continuing the same real conversation. Everything above stays exactly as
    # it was: inbound handling alone sent nothing. From here ONLY an explicitly created, approved and
    # dispatched LeadBoost reply action may produce the next outbound email.
    # ======================================================================================================
    reply_url = "/api/v2/outreach-actions"

    # ---- 13. policy enforced in the service layer: automatic mode / an explicit key are refused, nothing created.
    def _action_count():
        return len(client.get(reply_url, headers=t.h).json())

    actions_before = _action_count()
    base = {"lead_id": lead_id, "email_account_id": account_id, "reply_to_message_reference": inbound_ref}
    refused_auto = client.post(reply_url, headers=t.h, json={**base, "mode": "automatic"})
    assert refused_auto.status_code == 422 and refused_auto.json()["detail"]["error_code"] == "reply_requires_review"
    refused_key = client.post(reply_url, headers=t.h, json={**base, "mode": "manual", "idempotency_key": "x-1"})
    assert refused_key.status_code == 422
    assert _action_count() == actions_before

    # ---- 14. create the reply action for the inbound message: PENDING_REVIEW, and the Mailer is not involved.
    created_reply = client.post(reply_url, headers=t.h, json={**base, "mode": "manual"})
    assert created_reply.status_code == 201, created_reply.text
    reply_action = created_reply.json()
    reply_id = reply_action["id"]
    assert reply_action["state"] == "pending_review" and reply_action["reply_to_message_reference"] == inbound_ref
    assert reply_action["idempotency_key"].startswith("reply:")
    # the same inbound message again (retry / re-poll) returns the SAME action, never a second one
    replay = client.post(reply_url, headers=t.h, json={**base, "mode": "manual"})
    assert replay.status_code == 201 and replay.json()["id"] == reply_id
    assert _action_count() == actions_before + 1

    # ---- 15. NO second outbound before the draft exists, is approved and is explicitly sent.
    #          (C9.5: this is where the old flow -- approve, then let the Mailer generate -- is gone.)
    assert _dispatch(client, t.h, reply_id).status_code in (400, 409)
    assert drv.sql("SELECT count(*) AS n FROM external_dispatches") == [{"n": 1}]
    assert drv.sql("SELECT count(*) AS n FROM messages WHERE direction = 'outbound'") == [{"n": 1}]
    assert len(_wire_messages(infra)) == 1
    approve_url = f"/api/v2/outreach-actions/{reply_id}/approve"
    draft_url = f"/api/v2/outreach-actions/{reply_id}/reply-draft"
    # A reply cannot be approved without naming the reviewed draft, nor before any draft was requested.
    assert client.post(approve_url, headers=t.h).status_code == 422
    not_started = client.get(draft_url, headers=t.h)
    assert not_started.status_code == 200 and not_started.json()["state"] == "not_started"
    assert client.post(approve_url, headers=t.h, json={"content_sha256": "a" * 64}).status_code == 409
    assert drv.sql("SELECT count(*) AS n FROM external_dispatches") == [{"n": 1}]

    # ---- 16. ask the Mailer to GENERATE the draft. Generation is allowed before approval because it is not a
    #          send: the Mailer accepts the request durably and HOLDS whatever it produces.
    started = client.post(draft_url, headers=t.h)
    assert started.status_code == 200 and started.json()["state"] == "generating", started.text
    reply_idem = _action_row(reply_id)["idempotency_key"]
    reply_where = f"idempotency_key = {_quote(reply_idem)}"
    (rd,) = drv.sql(
        f"SELECT state, message_id, organization_id, mailbox_id, content_approval_required, "
        f"approved_content_sha256, grounding_context->'reply_parent'->>'message_id' AS parent_message_id "
        f"FROM external_dispatches WHERE {reply_where}"
    )
    # The opaque public token and the Mailer's internal parent row ID are intentionally different identifiers.
    expected_parent_id = str(inbound_row[0]["id"])
    assert (rd["state"], rd["message_id"], rd["organization_id"], rd["parent_message_id"]) == (
        "queued", None, "tenant-a", expected_parent_id,
    )
    assert rd["content_approval_required"] is True and rd["approved_content_sha256"] is None
    assert inbound_row[0]["reply_reference"] == inbound_ref and inbound_ref != expected_parent_id
    assert rd["mailbox_id"] == box["id"]
    assert len(_wire_messages(infra)) == 1                    # accepted != sent
    # Repeating the request (a retry, a double-click) replays the same operation: still ONE dispatch.
    assert client.post(draft_url, headers=t.h).status_code == 200
    assert drv.sql("SELECT count(*) AS n FROM external_dispatches") == [{"n": 2}]
    assert client.post(approve_url, headers=t.h, json={"content_sha256": "a" * 64}).status_code == 409  # still generating

    # ---- 17. the Mailer's REAL generation cycle (deterministic fake LLM). It lands in the approval HOLD.
    reply_body = "Hi Jamie,\n\nThanks for getting back to me. What day and time suits you for a short call?\n\nBest,\nSales"
    gen2 = drv.run(
        "generate", json.dumps({"subject": "A subject the model must not control", "body": reply_body, "reasoning": "c9.5"})
    )
    assert gen2["results"] == [{"outcome": "generated", "reason": None, "persisted": True}], gen2
    assert gen2["llm_calls"] == 1 and len(_wire_messages(infra)) == 1
    (g,) = drv.sql(f"SELECT state, message_id, approved_content_sha256 FROM external_dispatches WHERE {reply_where}")
    assert g["state"] == "awaiting_approval", g               # NOT queued: the SMTP worker must not be able to claim it
    assert g["message_id"] is not None and g["approved_content_sha256"] is None
    (rm,) = drv.sql(
        "SELECT message_type, status, subject, body, in_reply_to_header, references_header, message_id_header "
        f"FROM messages WHERE id = {g['message_id']}"
    )
    assert rm["message_type"] == "reply" and rm["status"] == "draft" and rm["message_id_header"] is None
    assert rm["subject"] == REPLY_SUBJECT                     # deterministic Re: subject, never the model's
    assert rm["body"] == reply_body
    assert rm["in_reply_to_header"] == inbound_message_id     # the immediate parent: the INBOUND message
    assert rm["references_header"] == f"{wire_message_id} {inbound_message_id}"

    # The Mailer's REAL SMTP worker runs with live sending ON and finds nothing it may claim.
    held = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert [r for r in held["results"] if r["outcome"] == "sent"] == [], held
    assert len(_wire_messages(infra)) == 1                    # NO SMTP before approval
    assert drv.sql(f"SELECT state FROM external_dispatches WHERE {reply_where}") == [{"state": "awaiting_approval"}]

    # ---- 18. LeadBoost shows the EXACT persisted subject and body, read server-to-server.
    shown = client.get(draft_url, headers=t.h)
    assert shown.status_code == 200 and shown.headers["cache-control"] == "no-store"
    draft = shown.json()
    assert draft["state"] == "awaiting_approval" and draft["draft_version"] == 1
    assert (draft["subject"], draft["body"]) == (REPLY_SUBJECT, reply_body)
    # The digest is the documented algorithm, recomputed here independently of both services.
    assert draft["content_sha256"] == _content_digest(REPLY_SUBJECT, reply_body)
    # Reads are safe to repeat and mutate nothing on either side.
    snap_dispatch = drv.sql(f"SELECT id, state, updated_at, approved_content_sha256 FROM external_dispatches WHERE {reply_where}")
    snap_action = _action_row(reply_id)
    for _ in range(3):
        assert client.get(draft_url, headers=t.h).json() == draft
    assert drv.sql(f"SELECT id, state, updated_at, approved_content_sha256 FROM external_dispatches WHERE {reply_where}") == snap_dispatch
    assert _action_row(reply_id) == snap_action
    # Nothing internal or secret reaches the browser through the draft endpoint.
    for forbidden in (
        key_a, "tenant-a", mailer_server.base_url, str(mailer_server.port), mailbox_ref, reply_idem, SENDER,
        wire_message_id, inbound_message_id, inbound_message_id.strip("<>"), "x-api-key", "/integrations",
        "in_reply_to", "references", "grounding", "mailbox", str(g["message_id"]) + ",", expected_parent_id + ",",
    ):
        assert forbidden.lower() not in shown.text.lower(), forbidden

    # ---- 19. a foreign tenant can neither read, approve, release nor cancel it -- at LeadBoost AND at the
    #          REAL Mailer (which has a second, genuinely separate tenant).
    other = env(client, "cont_b", "e2e-key-b")
    for method, url, kw in (
        ("get", draft_url, {}),
        ("post", draft_url, {}),
        ("post", approve_url, {"json": {"content_sha256": draft["content_sha256"]}}),
        ("post", f"/api/v2/outreach-actions/{reply_id}/dispatch", {}),
        ("post", f"/api/v2/outreach-actions/{reply_id}/cancel", {}),
    ):
        assert getattr(client, method)(url, headers=other.h, **kw).status_code == 404, url
    foreign = {"X-API-Key": "e2e-key-b"}
    mailer_draft = f"{mailer_server.base_url}/integrations/leadboost/reply-drafts/{reply_idem}"
    for r in (
        httpx.get(mailer_draft, headers=foreign),
        httpx.post(mailer_draft + "/release", headers=foreign, json={"content_sha256": draft["content_sha256"]}),
        httpx.post(mailer_draft + "/cancel", headers=foreign),
    ):
        assert r.status_code == 404, r.text
        assert reply_body not in r.text and REPLY_SUBJECT not in r.text
    assert httpx.get(mailer_draft, headers={"X-API-Key": "not-a-key"}).status_code in (401, 403)
    assert drv.sql(f"SELECT id, state, updated_at, approved_content_sha256 FROM external_dispatches WHERE {reply_where}") == snap_dispatch
    assert _action_row(reply_id) == snap_action

    # ---- 20. the user approves THE EXACT TEXT THEY SAW. A digest of anything else is refused.
    stale = client.post(approve_url, headers=t.h, json={"content_sha256": _content_digest(REPLY_SUBJECT, reply_body + " ")})
    assert stale.status_code == 409 and stale.json()["detail"]["error_code"] == "reply_draft_stale"
    assert _action_row(reply_id) == snap_action
    approved = client.post(approve_url, headers=t.h, json={"content_sha256": draft["content_sha256"]})
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "approved" and approved.json()["approved_content_sha256"] == draft["content_sha256"]
    row = _action_row(reply_id)
    assert row["approved_content_sha256"] == draft["content_sha256"] and row["approved_draft_version"] == 1
    assert row["approved_by_user_id"] == t.uid and row["approved_at"] is not None
    # Approval records a decision. It releases and sends NOTHING.
    assert drv.sql(f"SELECT state, approved_content_sha256, approved_at FROM external_dispatches WHERE {reply_where}") == [
        {"state": "awaiting_approval", "approved_content_sha256": None, "approved_at": None}
    ]
    still_held = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert [r for r in still_held["results"] if r["outcome"] == "sent"] == [], still_held
    assert len(_wire_messages(infra)) == 1                    # NO SMTP merely because approval was recorded
    assert client.post(approve_url, headers=t.h, json={"content_sha256": draft["content_sha256"]}).status_code == 409

    # ---- 21. the user explicitly selects SEND. LeadBoost RELEASES the same draft; it does not regenerate.
    dispatched_reply = _dispatch(client, t.h, reply_id)
    assert dispatched_reply.status_code == 200 and dispatched_reply.json()["state"] == OutreachState.SUBMITTED, dispatched_reply.text
    (rel,) = drv.sql(
        f"SELECT state, message_id, approved_content_sha256, approved_at FROM external_dispatches WHERE {reply_where}"
    )
    assert rel["state"] == "queued" and rel["message_id"] == g["message_id"]          # the SAME message
    assert rel["approved_content_sha256"] == draft["content_sha256"] and rel["approved_at"] is not None
    assert drv.sql("SELECT count(*) AS n FROM external_dispatches") == [{"n": 2}]    # no new dispatch
    assert drv.sql("SELECT count(*) AS n FROM messages WHERE direction = 'outbound'") == [{"n": 2}]  # no new message
    assert len(_wire_messages(infra)) == 1                    # released != sent
    assert _dispatch(client, t.h, reply_id).status_code == 409   # a second click releases nothing again
    assert drv.sql(f"SELECT state, approved_content_sha256 FROM external_dispatches WHERE {reply_where}") == [
        {"state": "queued", "approved_content_sha256": draft["content_sha256"]}
    ]

    # ---- 22. the Mailer's REAL dispatch cycle -> real SMTP into the sink: exactly one more message, and its
    #          bytes are the approved bytes. No LLM runs.
    sent2 = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert [r["outcome"] for r in sent2["results"]] == ["sent"], sent2
    wire_all = _wire_messages(infra)
    assert len(wire_all) == 2
    reply_wire = message_from_bytes(base64.b64decode(wire_all[1]["raw_b64"]))
    assert wire_all[1]["tls"] and wire_all[1]["authenticated"]
    assert wire_all[1]["mail_from"] == SENDER and wire_all[1]["rcpt_tos"] == ["lead@example.com"]
    assert reply_wire["In-Reply-To"] == inbound_message_id
    assert reply_wire["References"].split() == [wire_message_id, inbound_message_id]
    assert reply_wire["Subject"] == draft["subject"] == REPLY_SUBJECT
    reply_message_id = reply_wire["Message-ID"]
    assert reply_message_id and reply_message_id not in (wire_message_id, inbound_message_id)
    # The COMPLETE approved subject and body, byte for byte (no strip(); only the transport's CRLF -> LF).
    sent_subject, sent_body = _wire_content(wire_all[1])
    assert sent_subject == draft["subject"] == REPLY_SUBJECT
    assert sent_body == draft["body"] == reply_body
    assert (sent_subject, sent_body) == (rm["subject"], rm["body"])           # and the Mailer's stored message
    # The very text that went over SMTP fingerprints to the digest the user approved.
    assert _content_digest(sent_subject, sent_body) == draft["content_sha256"]
    (rs,) = drv.sql(
        f"SELECT d.state, m.message_id_header FROM external_dispatches d JOIN messages m ON m.id = d.message_id "
        f"WHERE d.{reply_where}"
    )
    assert rs["state"] == "sent" and rs["message_id_header"] == reply_message_id       # a real send, recorded as one

    # ---- 23. LeadBoost reads back the real thread and the real delivery state; the first action is untouched.
    final_draft = client.get(draft_url, headers=t.h).json()
    assert final_draft["state"] == "sent"
    assert (final_draft["subject"], final_draft["body"]) == (draft["subject"], draft["body"])
    thread = _state(client, t, reply_id).json()
    assert thread["availability"] == "available" and thread["mailer"]["delivery_state"] == "sent"
    assert [x["direction"] for x in thread["mailer"]["messages"]] == ["outbound", "inbound", "outbound"]
    assert thread["mailer"]["messages"][-1]["body"] == reply_body
    assert _action_row(action_id) == action_after_dispatch

    # ---- 24. retries create nothing new: re-dispatch, re-approve, re-create, re-start, another cycle, another poll.
    assert _dispatch(client, t.h, reply_id).status_code == 409
    assert client.post(approve_url, headers=t.h, json={"content_sha256": draft["content_sha256"]}).status_code == 409
    assert client.post(draft_url, headers=t.h).status_code in (200, 409)
    again_reply = client.post(reply_url, headers=t.h, json={**base, "mode": "manual"})
    assert again_reply.json()["id"] == reply_id and _action_count() == actions_before + 1
    drv.run("generate", json.dumps({"subject": "x", "body": "unused", "reasoning": "none queued"}))
    idle = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert idle["results"] == [] or all(r["outcome"] != "sent" for r in idle["results"]), idle
    drv.run("imap", "forget-seen", *imap)
    drv.run("poll", extra_env={"AUTO_REPLY_ENABLED": "true", "GROQ_API_KEY": ""})
    assert len(_wire_messages(infra)) == 2
    assert drv.sql("SELECT count(*) AS n FROM messages WHERE direction = 'outbound'") == [{"n": 2}]
    assert drv.sql("SELECT count(*) AS n FROM messages WHERE direction = 'inbound'") == [{"n": 1}]
    assert drv.sql("SELECT count(*) AS n FROM external_dispatches") == [{"n": 2}]
    assert drv.sql(f"SELECT next_action_at FROM contacts WHERE id = {contact_id}") == [{"next_action_at": None}]

    # ======================================================================================================
    # Attack scenarios on the same live system. Each needs its own inbound message, so the recipient "replies
    # again" (really, via IMAP) and the Mailer's real poll stores it.
    # ======================================================================================================
    def _new_inbound(tag, subject=None):
        mid = f"<cont-{tag}-{uuid.uuid4().hex}@prospect.example>"
        drv.run("imap", "deliver", *imap, json.dumps({
            "from": "lead@example.com", "to": SENDER, "subject": subject or f"Re: Quick question {tag}",
            "body": f"Please also tell me about {tag}.", "message_id": mid, "in_reply_to": reply_message_id,
        }))
        poll_ = drv.run("poll", extra_env={"AUTO_REPLY_ENABLED": "true", "GROQ_API_KEY": ""})
        assert any(p["processed"] >= 1 for p in poll_), poll_
        msgs = _state(client, t, reply_id).json()["mailer"]["messages"]
        (ref,) = [m["message_reference"] for m in msgs if m["direction"] == "inbound" and tag in m["body"]]
        assert re.fullmatch(r"[0-9a-f]{32}", ref)
        return mid, ref

    def _reply_to(ref):
        r = client.post(reply_url, headers=t.h, json={**base, "reply_to_message_reference": ref, "mode": "manual"})
        assert r.status_code == 201, r.text
        aid = r.json()["id"]
        assert client.post(f"/api/v2/outreach-actions/{aid}/reply-draft", headers=t.h).json()["state"] == "generating"
        return aid, _action_row(aid)["idempotency_key"]

    def _generate(text_):
        out = drv.run("generate", json.dumps({"subject": "ignored", "body": text_, "reasoning": "c9.5 scenario"}))
        assert out["results"] == [{"outcome": "generated", "reason": None, "persisted": True}], out

    # ---- 25. EXACT BYTES, positive control: a Unicode, multi-line draft with interior whitespace reaches the SMTP
    #          wire COMPLETELY and EXACTLY. (The generation pipeline itself strips a model's leading/trailing
    #          whitespace BEFORE the draft is persisted and shown, so the approved text has none to preserve; what must
    #          hold is that nothing is changed AFTER approval. The tamper scenarios below cover boundary whitespace.)
    unicode_subject = "Re: R\u00e9servation \u2603 \u2014 caf\u00e9"
    mid_d, ref_d = _new_inbound("scenario-exact", subject=unicode_subject)
    id_d, idem_d = _reply_to(ref_d)
    body_d = (
        "Hi Jamie,\n\nGr\u00fc\u00dfe aus Z\u00fcrich \u2603 \u2014 \u4f60\u597d \U0001f642\n\n"
        "\tIndented with a tab\nTrailing spaces on this line   \n\n\nTwo blank lines above.\n\nBest,\nSales"
    )
    _generate("\n  " + body_d + "  \n")                      # the model's boundary whitespace is stripped by the pipeline
    where_d = f"idempotency_key = {_quote(idem_d)}"
    (stored_d,) = drv.sql(f"SELECT m.subject, m.body FROM external_dispatches d JOIN messages m ON m.id = d.message_id WHERE d.{where_d}")
    assert stored_d["body"] == body_d, "the pipeline strips ONLY boundary whitespace; everything else is kept verbatim"
    url_d = f"/api/v2/outreach-actions/{id_d}/reply-draft"
    draft_d = client.get(url_d, headers=t.h).json()
    assert (draft_d["subject"], draft_d["body"]) == (stored_d["subject"], stored_d["body"])
    assert draft_d["subject"].startswith("Re: R\u00e9servation \u2603")
    assert draft_d["content_sha256"] == _content_digest(draft_d["subject"], draft_d["body"])
    assert client.post(f"/api/v2/outreach-actions/{id_d}/approve", headers=t.h,
                       json={"content_sha256": draft_d["content_sha256"]}).status_code == 200
    assert _dispatch(client, t.h, id_d).json()["state"] == OutreachState.SUBMITTED
    exact = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert [r["outcome"] for r in exact["results"]] == ["sent"], exact
    wire_all = _wire_messages(infra)
    assert len(wire_all) == 3
    d_subject, d_body = _wire_content(wire_all[2])
    assert d_subject == draft_d["subject"] == stored_d["subject"]               # non-ASCII subject, decoded, exact
    assert d_body == draft_d["body"] == stored_d["body"] == body_d              # every byte: tab, 3 trailing spaces, blank lines
    assert _content_digest(d_subject, d_body) == draft_d["content_sha256"]
    sent_total = 3

    # ---- 26. CANCELLED: a held draft that is cancelled can never be approved, released or sent.
    mid_b, ref_b = _new_inbound("scenario-cancel")
    id_b, idem_b = _reply_to(ref_b)
    body_b = "Hi Jamie,\n\nHappy to explain more. Which day works for a short call?\n\nBest,\nSales"
    _generate(body_b)
    url_b = f"/api/v2/outreach-actions/{id_b}/reply-draft"
    draft_b = client.get(url_b, headers=t.h).json()
    assert draft_b["state"] == "awaiting_approval" and draft_b["body"] == body_b
    cancelled = client.post(f"/api/v2/outreach-actions/{id_b}/cancel", headers=t.h)
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    where_b = f"idempotency_key = {_quote(idem_b)}"
    assert drv.sql(f"SELECT state, approved_content_sha256 FROM external_dispatches WHERE {where_b}") == [
        {"state": "cancelled", "approved_content_sha256": None}
    ]
    assert client.get(url_b, headers=t.h).json()["state"] == "cancelled"
    assert client.post(f"/api/v2/outreach-actions/{id_b}/approve", headers=t.h,
                       json={"content_sha256": draft_b["content_sha256"]}).status_code == 409
    assert _dispatch(client, t.h, id_b).status_code == 409
    direct = httpx.post(f"{mailer_server.base_url}/integrations/leadboost/reply-drafts/{idem_b}/release",
                        headers=mailer_headers, json={"content_sha256": draft_b["content_sha256"]})
    assert direct.status_code == 409 and direct.json()["detail"] == "draft_cancelled"   # even a correct digest, straight at the Mailer
    nothing = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
    assert [r for r in nothing["results"] if r["outcome"] == "sent"] == [], nothing
    assert len(_wire_messages(infra)) == sent_total          # a cancelled draft never reaches SMTP

    # ---- 27. CHANGED AFTER APPROVAL: content that differs from what was approved never reaches SMTP, even though
    #          every earlier gate passed and the draft was released. Each variant is a DIFFERENT way the stored text
    #          could differ, including ones a lax comparison (strip(), case/whitespace folding, or SMTP's own CRLF
    #          normalisation) would let through. Each runs the full cycle on its own inbound message.
    body_c = "Hi Jamie,\n\nGlad to help. Would a short call this week suit you?\n\nBest,\nSales"
    variants = {
        "trailing-space": body_c + " ",
        "final-newline": body_c + "\n",
        "leading-space": " " + body_c,
        "cyrillic-homoglyph": body_c.replace("a", "\u0430", 1),       # U+0430 CYRILLIC SMALL LETTER A
        "crlf-line-endings": body_c.replace("\n", "\r\n"),
        "replaced-content": "Hi Jamie,\n\nSend your bank details to attacker@example.net.\n\nBest,\nSales",
    }
    # SMTP carries CRLF regardless, so on the wire the CRLF variant is indistinguishable from the approved text:
    # only the digest over the STORED text can tell them apart.
    assert variants["crlf-line-endings"].replace("\r\n", "\n") == body_c and variants["crlf-line-endings"] != body_c
    for name, tampered in variants.items():
        mid_c, ref_c = _new_inbound(f"scenario-tamper-{name}")
        id_c, idem_c = _reply_to(ref_c)
        _generate(body_c)
        url_c = f"/api/v2/outreach-actions/{id_c}/reply-draft"
        draft_c = client.get(url_c, headers=t.h).json()
        assert draft_c["body"] == body_c, name
        assert client.post(f"/api/v2/outreach-actions/{id_c}/approve", headers=t.h,
                           json={"content_sha256": draft_c["content_sha256"]}).status_code == 200
        assert _dispatch(client, t.h, id_c).json()["state"] == OutreachState.SUBMITTED   # released; Mailer state: queued
        where_c = f"idempotency_key = {_quote(idem_c)}"
        (c0,) = drv.sql(f"SELECT state, message_id FROM external_dispatches WHERE {where_c}")
        assert c0["state"] == "queued", name
        assert drv.run("tamper-message", c0["message_id"], tampered) == {"tampered": 1}, name
        blocked = drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})
        assert [(r["outcome"], r.get("smtp_attempted")) for r in blocked["results"]] == [("failed", False)], (name, blocked)
        assert len(_wire_messages(infra)) == sent_total, f"{name}: the altered text was sent"
        (c1,) = drv.sql(f"SELECT state, error_message FROM external_dispatches WHERE {where_c}")
        assert c1["state"] == "failed" and c1["error_message"].startswith("approval_content_mismatch"), (name, c1)
        assert "attacker" not in c1["error_message"] and "bank" not in c1["error_message"]   # the reason carries no content
        assert drv.run("dispatch", infra["smtp_port"], extra_env={"LIVE_SENDING_ENABLED": "true"})["results"] == [], name  # not retried
        assert len(_wire_messages(infra)) == sent_total, name
        assert client.get(url_c, headers=t.h).json()["state"] == "failed", name

    # Totals across the whole run: replies were created for D, B and each tamper variant; exactly TWO replies were
    # sent (the main one and the Unicode one); every other dispatch exists but never reached SMTP.
    assert drv.sql("SELECT count(*) AS n FROM external_dispatches") == [{"n": 2 + 1 + 1 + len(variants)}]
    assert [json.loads(l)["rcpt_tos"] for l in Path(infra["capture"]).read_text().splitlines() if l.strip()] == [
        ["lead@example.com"]
    ] * sent_total

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
