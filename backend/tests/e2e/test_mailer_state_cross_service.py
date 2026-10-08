"""
C9.3 cross-service E2E: browser-facing LeadBoost API -> real LeadBoost service ->
REAL LeadBoost client over REAL HTTP -> the REAL Mailer Agent (a separate process,
its own interpreter, its own database) -> real Mailer endpoint -> safe product response.

Skipped unless a Mailer checkout is available:

    MAILER_REPO_PATH=/path/to/LeadBoost-mail-agent  [MAILER_PYTHON=python3]  pytest tests/e2e -v

Everything that is part of the contract is real: mailbox provisioning, dispatch, the
Mailer's idempotency / tenancy / auth, and the conversation read. Only what the Mailer's
asynchronous worker would do after accepting a dispatch (generate the message, send it,
record the outcome) and what its IMAP poller would do (store a reply) is written straight
into the Mailer's database -- those processes are deliberately not running.

This is also the drift guard for tests/application/fake_mailer.py: the fake's conversation
response must have exactly the real Mailer's shape.
"""

import asyncio
import json
import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from core.domain.models.outreach_action import OutreachAction, OutreachState
from core.infrastructure.database import SessionLocal
from core.infrastructure.mailing_agent.conversation_client import fetch_conversation
from sqlalchemy import update
from tests.application.fake_mailer import FakeMailer
from tests.application.test_outreach_dispatch_l1 import (
    _account,
    _approved,
    _dispatch,
    _lead,
    _login,
    _set_offer,
)
from tests.e2e.mailer_process import KEYS, MailerProcess, MailerUnavailableForTests

REPO = os.environ.get("MAILER_REPO_PATH")
PYTHON = os.environ.get("MAILER_PYTHON", "python3")
# CI sets E2E_REQUIRE_MAILER=1: there, an unavailable Mailer is a FAILURE, never a silent skip.
REQUIRED = os.environ.get("E2E_REQUIRE_MAILER") == "1"

pytestmark = pytest.mark.skipif(
    not REPO and not REQUIRED,
    reason="set MAILER_REPO_PATH to a Mailer Agent checkout to run the cross-service E2E",
)

GENERATED = "Hi Jamie -- the Mailer-GENERATED opening line, nothing like LeadBoost's snapshot."


@pytest.fixture()
def mailer_server():
    """A fresh Mailer process + database per test. The Mailer fails closed when a tenant has more
    than one ACTIVE mailbox, so tests must not accumulate mailboxes under the same tenant."""
    if not REPO:
        pytest.fail("E2E_REQUIRE_MAILER=1 but MAILER_REPO_PATH is not set")
    proc = MailerProcess(REPO, PYTHON)
    try:
        proc.start()
    except MailerUnavailableForTests as exc:
        proc.stop()
        (pytest.fail if REQUIRED else pytest.skip)(str(exc))
    try:
        yield proc
    finally:
        proc.stop()


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


class Tenant:
    def __init__(self, h, org, uid, tenant, key):
        self.h, self.org, self.uid, self.tenant, self.key = h, org, uid, tenant, key


@pytest.fixture()
def env(mailer_server, monkeypatch):
    """LeadBoost -> real Mailer wiring; org -> key mapping is built up as orgs register."""
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", mailer_server.base_url)
    mapping: dict = {}

    def _register(client, tag, mailer_key):
        h, org, uid = _login(client, tag)
        mapping[str(org)] = mailer_key
        monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps(mapping))
        _set_offer(org, "We help B2B teams ship reliable developer tooling.")
        return Tenant(h, org, uid, KEYS[mailer_key], mailer_key)

    return _register


def _dispatched(client, t: Tenant):
    acc = _account(client, t.h, t.org, None)
    aid = _approved(client, t.h, _lead(t.org, t.uid), acc["id"])
    r = _dispatch(client, t.h, aid)
    assert r.status_code == 200 and r.json()["state"] == OutreachState.SUBMITTED, r.text
    s = SessionLocal()
    try:
        key = s.get(OutreachAction, aid).idempotency_key
    finally:
        s.close()
    return aid, key


def _state(client, t, aid):
    return client.get(f"/api/v2/outreach-actions/{aid}/mailer-state", headers=t.h)


def _action_row(aid):
    s = SessionLocal()
    try:
        r = s.get(OutreachAction, aid)
        return {c.name: getattr(r, c.name) for c in OutreachAction.__table__.columns}
    finally:
        s.close()


# ------------------------------------------------------------------------------------------
def test_customer_sees_the_real_mailers_state_and_conversation_end_to_end(client, mailer_server, env):
    a = env(client, "e2e_a", "e2e-key-a")
    b = env(client, "e2e_b", "e2e-key-b")
    aid, key = _dispatched(client, a)
    _dispatched(client, b)  # org B has its own mailbox + dispatch at the Mailer

    # real dispatch really reached the real Mailer, under tenant A, and nothing is generated yet
    d = mailer_server.dispatch(key)
    assert d["organization_id"] == "tenant-a" and d["state"] == "queued" and d["message_id"] is None
    r0 = _state(client, a, aid).json()
    assert r0["availability"] == "available" and r0["mailer"]["delivery_state"] == "queued"
    assert r0["mailer"]["messages"] == []  # honest: no message exists until generation

    # the Mailer's worker generates + sends; its IMAP poller stores a reply
    own_box = mailer_server.mailbox("tenant-a")
    foreign_box = mailer_server.mailbox("tenant-b")
    mailer_server.worker_generated_and_resolved(key, subject="Quick question", body=GENERATED, state="sent")
    mailer_server.add_inbound(
        key, body="Thanks, tell me more.", mailbox_id=own_box["id"], at="2026-01-01 13:00:00.000000"
    )
    mailer_server.add_inbound(
        key, body="WEBHOOK-INBOUND-SENTINEL", mailbox_id=None, at="2026-01-01 13:30:00.000000"
    )
    mailer_server.add_inbound(
        key, body="FOREIGN-MAILBOX-SENTINEL", mailbox_id=foreign_box["id"], at="2026-01-01 13:40:00.000000"
    )
    before_mailer = mailer_server.dump()
    before_action = _action_row(aid)

    r = _state(client, a, aid)

    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["availability"] == "available" and body["error_code"] is None
    m = body["mailer"]
    assert m["delivery_state"] == "sent" and m["has_more"] is False
    assert [x["direction"] for x in m["messages"]] == ["outbound", "inbound"]
    assert m["messages"][0]["body"] == GENERATED  # the Mailer's actual message, not LeadBoost's snapshot
    assert m["messages"][0]["delivery_state"] == "sent" and m["messages"][1]["delivery_state"] is None
    assert m["messages"][1]["body"] == "Thanks, tell me more."
    # provenance: unprovable-owner and foreign-mailbox inbound never reach the customer
    assert "WEBHOOK-INBOUND-SENTINEL" not in r.text and "FOREIGN-MAILBOX-SENTINEL" not in r.text
    # the browser never learns anything about the Mailer
    for internal in (
        mailer_server.base_url,
        str(mailer_server.port),
        "e2e-key-a",
        "e2e-key-b",
        "tenant-a",
        "tenant-b",
        d["public_reference"],
        own_box["public_reference"],
        own_box["email_address"],
        "x-api-key",
        "/integrations",
        "mailing_agent_reference",
        "mailbox_reference",
    ):
        assert internal.lower() not in r.text.lower(), internal
    assert set(m["messages"][0]) == {
        "direction",
        "subject",
        "body",
        "body_truncated",
        "created_at",
        "delivery_state",
    }
    # read-only, both sides: the Mailer's database and LeadBoost's action are untouched
    assert mailer_server.dump() == before_mailer
    assert _action_row(aid) == before_action
    assert (
        client.get(f"/api/v2/outreach-actions/{aid}", headers=a.h).json()["state"] == OutreachState.SUBMITTED
    )


def test_leadboost_dispatch_failed_but_mailer_sent_shows_both_and_heals_nothing(client, mailer_server, env):
    a = env(client, "e2e_d5", "e2e-key-a")
    aid, key = _dispatched(client, a)
    mailer_server.worker_generated_and_resolved(key, subject="Quick question", body=GENERATED, state="sent")
    s = SessionLocal()  # the lost-response case
    try:
        s.execute(
            update(OutreachAction)
            .where(OutreachAction.id == aid)
            .values(
                state=OutreachState.DISPATCH_FAILED,
                last_dispatch_error="mailing_agent_timeout",
                mailing_agent_reference=None,
            )
        )
        s.commit()
    finally:
        s.close()
    before = _action_row(aid)

    r = _state(client, a, aid).json()

    assert r["availability"] == "available" and r["mailer"]["delivery_state"] == "sent"
    assert _action_row(aid) == before
    assert (
        client.get(f"/api/v2/outreach-actions/{aid}", headers=a.h).json()["state"]
        == OutreachState.DISPATCH_FAILED
    )


def test_mailer_unknown_state_is_shown_as_unknown_not_failed(client, mailer_server, env):
    a = env(client, "e2e_unk", "e2e-key-a")
    aid, key = _dispatched(client, a)
    mailer_server.worker_generated_and_resolved(key, subject="s", body=GENERATED, state="unknown")
    r = _state(client, a, aid).json()
    assert (
        r["mailer"]["delivery_state"] == "unknown"
        and r["mailer"]["messages"][0]["delivery_state"] == "unknown"
    )


def test_cross_tenant_access_is_impossible_at_both_layers(client, mailer_server, env):
    a = env(client, "e2e_xa", "e2e-key-a")
    b = env(client, "e2e_xb", "e2e-key-b")
    aid, key = _dispatched(client, a)
    mailer_server.worker_generated_and_resolved(key, subject="s", body="ORG-A-PRIVATE-TEXT", state="sent")

    # LeadBoost layer: org B asking for org A's action id
    cross = client.get(f"/api/v2/outreach-actions/{aid}/mailer-state", headers=b.h)
    assert cross.status_code == 404 and "ORG-A-PRIVATE-TEXT" not in cross.text

    # Mailer layer, bypassing LeadBoost's own scoping: org B's credentials with org A's real key
    stolen = asyncio.new_event_loop().run_until_complete(
        fetch_conversation(organization_id=b.org, idempotency_key=key)
    )
    unknown = asyncio.new_event_loop().run_until_complete(
        fetch_conversation(organization_id=b.org, idempotency_key="never-" + uuid.uuid4().hex)
    )
    assert stolen.status == unknown.status == "not_found" and stolen.conversation is None
    # ...and the raw HTTP bodies are identical: no signal that the key exists for someone else
    url = f"{mailer_server.base_url}/integrations/leadboost/outreach-actions/%s/conversation"
    r1 = httpx.get(url % key, headers={"X-API-Key": "e2e-key-b"})
    r2 = httpx.get(url % "never-existed", headers={"X-API-Key": "e2e-key-b"})
    assert (
        (r1.status_code, r1.json())
        == (r2.status_code, r2.json())
        == (404, {"detail": "Outreach action not found"})
    )


def test_mailer_side_failures_over_real_http_are_safe_and_never_a_false_success(
    client, mailer_server, env, monkeypatch
):
    a = env(client, "e2e_fail", "e2e-key-a")
    aid, key = _dispatched(client, a)
    mailer_server.worker_generated_and_resolved(key, subject="s", body=GENERATED, state="sent")

    # the Mailer rejects the key (401)
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({str(a.org): "wrong-key"}))
    assert _state(client, a, aid).json() == {
        "availability": "mailer_unavailable",
        "error_code": "mailing_agent_rejected",
        "mailer": None,
    }

    # no key configured for the org at all
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"999999": "e2e-key-a"}))
    assert _state(client, a, aid).json()["error_code"] == "mailing_agent_auth_not_configured"

    # the Mailer is not reachable
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({str(a.org): "e2e-key-a"}))
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://127.0.0.1:9")
    r = _state(client, a, aid)
    assert r.json() == {
        "availability": "mailer_unavailable",
        "error_code": "mailing_agent_unreachable",
        "mailer": None,
    }
    assert "127.0.0.1" not in r.text


# ------------------------------------------------------------------------------------------
def _shape(value):
    """Structure of a JSON document: keys at every level, and the JSON type of each leaf."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(v) for v in value[:1]]
    return "null" if value is None else type(value).__name__


def test_the_fake_mailer_has_exactly_the_real_mailers_conversation_shape(client, mailer_server, env):
    """Drift guard: if the Mailer's contract changes, the fast fake-based suite can no longer
    silently diverge from it."""
    a = env(client, "e2e_shape", "e2e-key-a")
    aid, key = _dispatched(client, a)
    mailer_server.worker_generated_and_resolved(key, subject="Quick question", body=GENERATED, state="sent")
    mailer_server.add_inbound(key, body="a reply", mailbox_id=mailer_server.mailbox("tenant-a")["id"])
    real = httpx.get(
        f"{mailer_server.base_url}/integrations/leadboost/outreach-actions/{key}/conversation",
        headers={"X-API-Key": "e2e-key-a"},
    ).json()

    fake = FakeMailer()
    rec = dict(
        reference="dsp_x",
        mailbox="mbx_x",
        state="sent",
        messages=[],
        created_at="2026-01-01T12:00:00Z",
        updated_at="2026-01-01T12:00:00Z",
    )
    rec["messages"] = [
        dict(direction="outbound", body="b", subject="s", created_at="2026-01-01T12:00:00Z"),
        dict(direction="inbound", body="r", subject="Re: s", created_at="2026-01-01T13:00:00Z"),
    ]
    faked = fake._conversation_out(rec, 20)

    assert set(real) == set(faked) == {"action", "messages", "has_more"}
    assert set(real["action"]) == set(faked["action"])
    assert len(real["messages"]) == len(faked["messages"]) == 2
    for real_msg, fake_msg in zip(real["messages"], faked["messages"], strict=True):
        assert _shape(real_msg).keys() == _shape(fake_msg).keys()
        assert real_msg["direction"] == fake_msg["direction"]
        # null / non-null pattern must agree field by field (e.g. inbound has no delivery_state)
        assert {k: v is None for k, v in real_msg.items()} == {k: v is None for k, v in fake_msg.items()}
    assert {k: type(v).__name__ for k, v in real["action"].items() if v is not None} == {
        k: type(v).__name__ for k, v in faked["action"].items() if v is not None
    }
