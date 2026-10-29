"""
A LeadBoost-authorized reply to a specific inbound Mailer message.

Reuses the existing create -> approve -> dispatch lifecycle. The only additions:
an optional ``reply_to_message_reference`` on the action (the opaque reference the
Mailer's conversation view exposes for inbound messages), a deterministic
idempotency identity derived from it, and ``in_reply_to`` on the Mailer request.
No second authorization path: a reply is PENDING_REVIEW until approved, and nothing
reaches the Mailer before dispatch.
"""

import pytest
from fastapi.testclient import TestClient

import main
from core.domain.models.lead import Lead
from core.domain.models.outreach_action import OutreachAction
from core.domain.models.user import User
from core.infrastructure.database import SessionLocal, crud
from core.infrastructure.mailing_agent import client as mc
from tests.application.fake_mailer import FakeMailer
from tests.application.test_outreach_dispatch_l1 import (
    DISPATCH_PATH, _account, _dispatch, _env, _lead,
)


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


def _reply(client, h, lead_id, acc_id, ref, **over):
    body = {"lead_id": lead_id, "email_account_id": acc_id, "mode": "manual", "reply_to_message_reference": ref}
    body.update(over)
    return client.post("/api/v2/outreach-actions", headers=h, json=body)


def _owner(org):
    s = SessionLocal()
    try:
        return s.query(User).filter(User.organization_id == org).first().id
    finally:
        s.close()


def _setup(client, mailer, monkeypatch, tag):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, tag)
    acc = _account(client, h, org, mailer)
    return h, org, tenant, _lead(org, uid), acc["id"]


def _rows(org):
    s = SessionLocal()
    try:
        return s.query(OutreachAction).filter(OutreachAction.organization_id == org).count()
    finally:
        s.close()


def test_reply_action_is_pending_review_and_carries_its_parent(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r1")
    r = _reply(client, h, lead, acc, "4")
    assert r.status_code in (200, 201), r.text
    a = r.json()
    assert a["reply_to_message_reference"] == "4" and a["state"] == "pending_review"
    assert a["idempotency_key"].startswith("reply:") and a["mode"] == "manual"
    assert mailer.calls_to("POST", DISPATCH_PATH) == []              # nothing reaches the Mailer


def test_same_inbound_message_never_creates_a_second_action(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r2")
    first = _reply(client, h, lead, acc, "4").json()
    again = _reply(client, h, lead, acc, "4")
    assert again.status_code in (200, 201) and again.json()["id"] == first["id"]
    assert _rows(org) == 1


def test_a_different_inbound_message_is_a_different_action(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r3")
    a = _reply(client, h, lead, acc, "4").json()
    b = _reply(client, h, lead, acc, "6").json()
    assert a["id"] != b["id"] and a["idempotency_key"] != b["idempotency_key"]


def test_reply_refuses_a_caller_supplied_idempotency_key(client, mailer, monkeypatch):
    """Gap B: an explicit key must never be a way around the one-action-per-inbound identity."""
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4")
    for k in ("k-1", "k-2"):
        r = _reply(client, h, lead, acc, "4", idempotency_key=k)
        assert r.status_code == 422 and r.json()["detail"]["error_code"] == "reply_idempotency_key_not_allowed"
    assert _rows(org) == 0
    assert _reply(client, h, lead, acc, "4").status_code in (200, 201) and _rows(org) == 1


def test_same_inbound_message_from_a_different_sender_is_refused_not_duplicated(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4b")
    first = _reply(client, h, lead, acc, "4")
    assert first.status_code in (200, 201)
    other = _account(client, h, org, mailer)           # a second sender account for the same org
    r = _reply(client, h, lead, other["id"], "4")
    assert r.status_code == 409 and r.json()["detail"]["error_code"] == "idempotency_key_reused"
    assert _rows(org) == 1


def test_same_inbound_message_for_a_different_lead_is_refused_not_duplicated(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4c")
    assert _reply(client, h, lead, acc, "4").status_code in (200, 201)
    uid = _owner(org)
    r = _reply(client, h, _lead(org, uid), acc, "4")
    assert r.status_code == 409 and _rows(org) == 1


def test_a_replay_still_succeeds_after_the_lead_snapshot_changed(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4d")
    first = _reply(client, h, lead, acc, "4").json()
    s = SessionLocal()
    try:
        s.query(Lead).filter(Lead.id == lead).update({"outreach_message": "A completely reprocessed message."})
        s.commit()
    finally:
        s.close()
    again = _reply(client, h, lead, acc, "4")
    assert again.status_code in (200, 201) and again.json()["id"] == first["id"] and _rows(org) == 1


def test_lost_insert_race_returns_the_existing_reply_via_the_database_constraint(client, mailer, monkeypatch):
    """The pre-insert lookup is only an optimisation: if it misses (a concurrent identical request
    committed in between), the UNIQUE(organization_id, idempotency_key) constraint decides."""
    from application.services import outreach_service as svc
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4e")
    first = _reply(client, h, lead, acc, "4").json()

    real = svc.crud.get_outreach_action_by_idempotency_key
    calls = {"n": 0}

    def miss_once(db, organization_id, key):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, organization_id, key)

    monkeypatch.setattr(svc.crud, "get_outreach_action_by_idempotency_key", miss_once)
    again = _reply(client, h, lead, acc, "4")
    assert calls["n"] >= 2                                  # the constraint path really ran
    assert again.status_code in (200, 201) and again.json()["id"] == first["id"] and _rows(org) == 1


@pytest.mark.parametrize("policy_enabled", [False, True])
def test_automatic_mode_is_refused_for_replies_even_when_the_org_allows_automatic(
    client, mailer, monkeypatch, policy_enabled
):
    """Gap A: replies stay in review regardless of the request's mode or the org's automatic policy."""
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4f")
    s = SessionLocal()
    try:
        pol = crud.get_or_create_outreach_policy(s, org)
        pol.automatic_sending_enabled = policy_enabled
        pol.require_approval_for_automatic = False      # the most permissive policy: would auto-approve
        s.commit()
    finally:
        s.close()
    r = _reply(client, h, lead, acc, "4", mode="automatic")
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "reply_requires_review"
    assert _rows(org) == 0                                            # no auto-approved (or any) reply action
    assert mailer.calls_to("POST", DISPATCH_PATH) == []
    if policy_enabled:
        # control: under this SAME policy ordinary outreach really is auto-approved, so the refusal
        # above is the reply rule at work, not a restrictive policy.
        ctl = client.post("/api/v2/outreach-actions", headers=h,
                          json={"lead_id": lead, "email_account_id": acc, "mode": "automatic"})
        assert ctl.status_code in (200, 201) and ctl.json()["state"] == "approved"


def test_automatic_mode_for_ordinary_outreach_is_unchanged(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r4g")
    r = client.post("/api/v2/outreach-actions", headers=h,
                    json={"lead_id": lead, "email_account_id": acc, "mode": "automatic"})
    assert r.status_code == 422 and r.json()["detail"]["error_code"] == "automatic_sending_disabled"


@pytest.mark.parametrize("ref", ["", "0", "-1", "abc", "1" * 40, "4 "])
def test_malformed_reference_is_rejected(client, mailer, monkeypatch, ref):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r5")
    assert _reply(client, h, lead, acc, ref).status_code == 422 and _rows(org) == 0


def test_dispatch_sends_in_reply_to_only_after_approval(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r6")
    aid = _reply(client, h, lead, acc, "4").json()["id"]
    assert _dispatch(client, h, aid).status_code in (400, 409)        # not approved: refused
    assert mailer.calls_to("POST", DISPATCH_PATH) == []
    assert client.post(f"/api/v2/outreach-actions/{aid}/approve", headers=h).status_code == 200
    r = _dispatch(client, h, aid)
    assert r.status_code == 200 and r.json()["state"] == "submitted"
    (call,) = mailer.calls_to("POST", DISPATCH_PATH)
    import json
    body = json.loads(call.body) if isinstance(call.body, (str, bytes)) else call.body
    assert body["in_reply_to"] == {"message_reference": "4"}
    assert body["idempotency_key"].startswith("reply:")


def test_plain_action_request_has_no_in_reply_to(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r7")
    aid = client.post("/api/v2/outreach-actions", headers=h,
                      json={"lead_id": lead, "email_account_id": acc, "mode": "manual"}).json()["id"]
    client.post(f"/api/v2/outreach-actions/{aid}/approve", headers=h)
    _dispatch(client, h, aid)
    (call,) = mailer.calls_to("POST", DISPATCH_PATH)
    assert "in_reply_to" not in str(call.body)


def test_mailer_state_exposes_the_inbound_reference_to_reply_to(client, mailer, monkeypatch):
    h, org, tenant, lead, acc = _setup(client, mailer, monkeypatch, "r8")
    aid = client.post("/api/v2/outreach-actions", headers=h,
                      json={"lead_id": lead, "email_account_id": acc, "mode": "manual"}).json()["id"]
    client.post(f"/api/v2/outreach-actions/{aid}/approve", headers=h)
    _dispatch(client, h, aid)
    s = SessionLocal()
    try:
        key = s.get(OutreachAction, aid).idempotency_key
    finally:
        s.close()
    mailer.add_message(tenant, key, direction="outbound", body="Hello")
    mailer.add_message(tenant, key, direction="inbound", body="Interested!", message_reference="4")
    mailer.set_dispatch_state(tenant, key, "sent")
    v = client.get(f"/api/v2/outreach-actions/{aid}/mailer-state", headers=h).json()
    assert v["availability"] == "available", v
    msgs = v["mailer"]["messages"]
    assert [m.get("message_reference") for m in msgs] == [None, "4"]
