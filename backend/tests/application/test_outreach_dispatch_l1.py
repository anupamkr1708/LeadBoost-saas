"""
L1: outreach dispatch through the REAL integrated path.

create EmailAccount -> verify (provisions the Mailer mailbox) -> authorize an
OutreachAction -> dispatch, with the real LeadBoost client talking to an
in-memory Mailer (tests/application/fake_mailer.py) so the actual HTTP requests
are inspected.

Proves: dispatch is credential-free and calls only the generated-outreach route;
the single-eligible-sender rule is an explicit 409 raised BEFORE any Mailer
call; unsynchronized / unconfigured senders fail closed; retries are
idempotent; tenants are isolated; and the documented distributed
inconsistency window cannot let a NEW dispatch through.
"""

import json
import uuid
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from core.domain.models.email_account import MailerSyncErrorCode, MailerSyncState, VerificationStatus
from core.domain.models.lead import Lead
from core.domain.models.organization import Organization
from core.domain.models.outreach_action import OutreachAction, OutreachState
from core.infrastructure.database import SessionLocal
from core.infrastructure.email.smtp_verifier import VerificationResult
from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent.client import DispatchErrorCode
from tests.application.fake_mailer import FakeMailer

SECRET = "Dispatch-level-SMTP-secret-must-never-be-in-a-dispatch"
OFFER = "We help B2B teams ship reliable developer tooling."
VERIFY = "api.endpoints.email_accounts.verify_smtp_mailbox"
DISPATCH_PATH = "/integrations/leadboost/outreach-requests"


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


def _login(client, tag):
    email = f"d_{tag}_{uuid.uuid4().hex[:6]}@example.com"
    assert client.post("/api/v2/register", json={"email": email, "password": "TestPass123!", "first_name": "S"}).status_code == 200
    token = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"}).json()["access_token"]
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"Authorization": f"Bearer {token}"}, me["organization_id"], me["id"]


def _set_offer(org_id, text=OFFER):
    s = SessionLocal()
    try:
        s.query(Organization).filter(Organization.id == org_id).update({"description": text})
        s.commit()
    finally:
        s.close()


def _lead(org_id, owner_id):
    s = SessionLocal()
    try:
        lead = Lead(organization_id=org_id, owner_id=owner_id, website=f"https://{uuid.uuid4().hex}.example.com",
                    company_name="Acme Co", industry="Software", about_text="Acme  builds\n developer tools.",
                    email="lead@example.com", contact_name="Jamie Lead", contact_title="CTO",
                    founded_year=2015, employees="11-50", revenue_band="$1M-$10M",
                    outreach_message="Hi Jamie, quick note about Acme's tooling.")
        s.add(lead); s.commit(); s.refresh(lead)
        return lead.id
    finally:
        s.close()


def _account(client, h, org, mailer, *, verify=True, **over):
    payload = dict(provider="smtp", email_address=f"s_{uuid.uuid4().hex[:6]}@example.com", display_name="Sales",
                   smtp_host="smtp.example.com", smtp_port=587, security_mode="starttls",
                   credential_type="smtp_password", credential=SECRET)
    payload.update(over)
    acc = client.post(f"/api/v2/organizations/{org}/email-accounts", headers=h, json=payload).json()
    if verify:
        with patch(VERIFY, return_value=VerificationResult(status=VerificationStatus.VERIFIED)):
            client.post(f"/api/v2/organizations/{org}/email-accounts/{acc['id']}/verify", headers=h)
    return acc


def _approved(client, h, lead_id, acc_id):
    r = client.post("/api/v2/outreach-actions", headers=h, json={"lead_id": lead_id, "email_account_id": acc_id, "mode": "manual"})
    assert r.status_code in (200, 201), r.text
    aid = r.json()["id"]
    assert client.post(f"/api/v2/outreach-actions/{aid}/approve", headers=h).status_code == 200
    return aid


def _dispatch(client, h, aid):
    return client.post(f"/api/v2/outreach-actions/{aid}/dispatch", headers=h)


def _env(client, mailer, monkeypatch, tag, *, offer=OFFER, map_key=True):
    h, org, uid = _login(client, tag)
    tenant = mailer.map_org(monkeypatch, org) if map_key else None
    if offer is not None:
        _set_offer(org, offer)
    return h, org, uid, tenant


def _dispatch_calls(mailer):
    return mailer.calls_to("POST", DISPATCH_PATH)


# --------------------------------------------------------------- the golden path
def test_integrated_dispatch_is_credential_free_and_uses_only_the_generated_route(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "golden")
    acc = _account(client, h, org, mailer)
    lead_id = _lead(org, uid)
    aid = _approved(client, h, lead_id, acc["id"])
    mailer.calls.clear()

    r = _dispatch(client, h, aid)

    body = r.json()
    assert r.status_code == 200 and body["state"] == OutreachState.SUBMITTED
    assert body["mailing_agent_reference"] == list(mailer.dispatches.values())[0]["reference"]

    # exactly ONE request, to the M2 route, with the org's own key and nothing else
    assert [(c.method, c.path) for c in mailer.calls] == [("POST", DISPATCH_PATH)]
    call = mailer.calls[0]
    assert call.api_key == f"key-org-{org}" and call.tenant == tenant and "authorization" not in call.headers
    sent = call.body
    assert set(sent) == {"external_action_id", "idempotency_key", "correlation_id", "recipient", "context"} - (
        {"correlation_id"} if "correlation_id" not in sent else set())
    assert sent["external_action_id"] == str(aid)
    assert sent["recipient"] == {"email": "lead@example.com", "name": "Jamie Lead", "title": "CTO", "company": "Acme Co"}
    assert sent["context"] == {
        "value_proposition": OFFER,
        "recipient_facts": ["Industry: Software", "About: Acme builds developer tools.", "Founded: 2015",
                            "Employee band: 11-50, est. revenue $1M-$10M"],
    }
    # no credential, SMTP setting, sender, message or tenant on the wire -- as text, at any depth
    wire = call.raw.decode()
    for banned in (SECRET, "smtp", "password", "credential", "subject", "sender", "organization", "mailbox", "tenant"):
        assert banned not in wire.lower(), banned
    # the old contract is never used
    assert not mailer.calls_to("POST", "/outreach-actions") and not mailer.calls_to("POST", "/integrations/leadboost/outreach-actions")
    # LeadBoost's authorization/audit record keeps its legacy snapshot (the DELIVERED text is Mailer's Message)
    s = SessionLocal()
    try:
        oa = s.get(OutreachAction, aid)
        assert oa.body == "Hi Jamie, quick note about Acme's tooling." and oa.state == OutreachState.SUBMITTED
    finally:
        s.close()


def test_credential_crosses_only_in_provisioning_never_in_dispatch(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "only")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    assert _dispatch(client, h, aid).status_code == 200

    carriers = mailer.credential_bearing_calls()
    assert carriers and all(
        (c.method == "POST" and c.path == "/mailboxes") or (c.method == "PATCH" and c.body.get("status") == "active")
        for c in carriers)
    assert not any(SECRET in c.raw.decode() for c in _dispatch_calls(mailer))
    assert not any(SECRET in c.raw.decode() for c in mailer.calls if c not in carriers)


# ------------------------------------------------------------------ idempotency
def test_retry_after_a_timeout_reuses_the_idempotency_key_and_creates_one_dispatch(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "retry")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    mailer.timeout_next()

    first = _dispatch(client, h, aid).json()
    assert first["state"] == OutreachState.DISPATCH_FAILED and first["last_dispatch_error"] == DispatchErrorCode.TIMEOUT
    second = _dispatch(client, h, aid).json()

    assert second["state"] == OutreachState.SUBMITTED and second["dispatch_attempts"] == 2
    keys = {c.body["idempotency_key"] for c in _dispatch_calls(mailer)}
    assert len(keys) == 1 and len(mailer.dispatches) == 1


def test_lost_response_after_mailer_accepted_is_replayed_not_duplicated(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "lost")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    lost = []

    def drop_response(call):
        if call.path == DISPATCH_PATH and not lost:
            lost.append(1)
            raise httpx.ReadTimeout("accepted by the Mailer, answer lost")

    mailer.after_call = drop_response
    assert _dispatch(client, h, aid).json()["state"] == OutreachState.DISPATCH_FAILED
    assert len(mailer.dispatches) == 1                                   # the Mailer DID accept it
    final = _dispatch(client, h, aid).json()

    assert final["state"] == OutreachState.SUBMITTED
    assert len(mailer.dispatches) == 1                                   # replay, never a second dispatch
    assert final["mailing_agent_reference"] == list(mailer.dispatches.values())[0]["reference"]


def test_mailer_conflict_is_recorded_as_a_safe_code(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "conflict")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    mailer.mailboxes["extra"] = dict(public_reference="extra", tenant=tenant, email_address="x@x.test", status="active",
                                     smtp_host="h", smtp_port=587, smtp_use_tls=True, smtp_username="u", smtp_password="p")
    body = _dispatch(client, h, aid).json()
    assert body["state"] == OutreachState.DISPATCH_FAILED and body["last_dispatch_error"] == DispatchErrorCode.CONFLICT
    assert mailer.dispatches == {}


# ------------------------------------------------- multiple eligible senders -> 409
def test_multiple_eligible_senders_is_an_explicit_409_before_any_mailer_call(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "multi")
    first = _account(client, h, org, mailer)
    second = _account(client, h, org, mailer)          # both active + verified
    aid = _approved(client, h, _lead(org, uid), first["id"])
    mailer.calls.clear()

    r = _dispatch(client, h, aid)

    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "multiple_active_senders"
    assert "Exactly one mailbox must be active" in r.json()["detail"]["message"]
    assert mailer.calls == []                                           # Mailer never contacted
    s = SessionLocal()
    try:
        oa = s.get(OutreachAction, aid)
        assert (oa.state, oa.dispatch_attempts, oa.last_dispatch_error) == (OutreachState.APPROVED, 0, None)   # untouched
    finally:
        s.close()

    # resolving the ambiguity makes the very same action dispatchable
    assert client.delete(f"/api/v2/organizations/{org}/email-accounts/{second['id']}", headers=h).status_code == 200
    ok = _dispatch(client, h, aid)
    assert ok.status_code == 200 and ok.json()["state"] == OutreachState.SUBMITTED


def test_inactive_unverified_and_historical_accounts_do_not_create_ambiguity(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "hist")
    live = _account(client, h, org, mailer)
    _account(client, h, org, mailer, verify=False)                       # unverified
    old = _account(client, h, org, mailer)
    client.delete(f"/api/v2/organizations/{org}/email-accounts/{old['id']}", headers=h)   # historical
    aid = _approved(client, h, _lead(org, uid), live["id"])
    r = _dispatch(client, h, aid)
    assert r.status_code == 200 and r.json()["state"] == OutreachState.SUBMITTED
    assert len(mailer.active_boxes(tenant)) == 1


def test_another_organizations_accounts_never_count_toward_ambiguity(client, mailer, monkeypatch):
    h_a, org_a, uid_a, _ = _env(client, mailer, monkeypatch, "isoa")
    h_b, org_b, uid_b, _ = _env(client, mailer, monkeypatch, "isob")
    a = _account(client, h_a, org_a, mailer)
    _account(client, h_b, org_b, mailer); _account(client, h_b, org_b, mailer)     # B alone is ambiguous
    aid = _approved(client, h_a, _lead(org_a, uid_a), a["id"])
    assert _dispatch(client, h_a, aid).json()["state"] == OutreachState.SUBMITTED


# ------------------------------------------------------------ fail-closed gates
def test_unsynchronized_sender_fails_closed_without_a_mailer_call_and_recovers_after_sync(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "unsynced")
    mailer.fail_next(503)
    acc = _account(client, h, org, mailer)                              # VERIFIED, but provisioning failed
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    mailer.calls.clear()

    failed = _dispatch(client, h, aid).json()
    assert failed["state"] == OutreachState.DISPATCH_FAILED
    assert "not synchronized with the Mailer" in failed["last_dispatch_error"]
    assert MailerSyncErrorCode.MAILER_UNAVAILABLE in failed["last_dispatch_error"]
    assert mailer.calls == []

    assert client.post(f"/api/v2/organizations/{org}/email-accounts/{acc['id']}/mailer-sync", headers=h).json()["mailer_sync_state"] == MailerSyncState.SYNCED
    assert _dispatch(client, h, aid).json()["state"] == OutreachState.SUBMITTED


def test_implicit_tls_sender_cannot_dispatch_through_the_integrated_path(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "tlsd")
    acc = _account(client, h, org, mailer, security_mode="tls", smtp_port=465)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    body = _dispatch(client, h, aid).json()
    assert body["state"] == OutreachState.DISPATCH_FAILED
    assert MailerSyncErrorCode.UNSUPPORTED_SECURITY_MODE in body["last_dispatch_error"]
    assert mailer.calls == []


@pytest.mark.parametrize("offer", [None, "", "   \n\t "])
def test_missing_value_proposition_fails_closed_and_is_retryable(client, mailer, monkeypatch, offer):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "novp", offer=offer)
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    mailer.calls.clear()
    body = _dispatch(client, h, aid).json()
    assert body["state"] == OutreachState.DISPATCH_FAILED and "value_proposition_not_configured" in body["last_dispatch_error"]
    assert mailer.calls == []
    _set_offer(org)
    assert _dispatch(client, h, aid).json()["state"] == OutreachState.SUBMITTED


def test_an_organization_without_its_own_mailer_key_cannot_dispatch_and_never_borrows_anothers(client, mailer, monkeypatch):
    h_a, org_a, uid_a, _ = _env(client, mailer, monkeypatch, "keya")
    a = _account(client, h_a, org_a, mailer)
    h_b, org_b, uid_b, _ = _env(client, mailer, monkeypatch, "keyb", map_key=False)
    b = _account(client, h_b, org_b, mailer)                             # cannot be provisioned: no key
    assert b["mailer_sync_error_code"] is None                          # (state is read below via the API)
    state = client.get(f"/api/v2/organizations/{org_b}/email-accounts/{b['id']}", headers=h_b).json()
    assert state["mailer_sync_state"] == MailerSyncState.PENDING
    assert state["mailer_sync_error_code"] == MailerSyncErrorCode.ORG_KEY_NOT_CONFIGURED
    aid = _approved(client, h_b, _lead(org_b, uid_b), b["id"])
    out = _dispatch(client, h_b, aid).json()
    assert out["state"] == OutreachState.DISPATCH_FAILED
    assert not any(c.api_key == f"key-org-{org_a}" and c.path == DISPATCH_PATH for c in mailer.calls)


def test_each_organization_dispatches_to_its_own_mailer_tenant(client, mailer, monkeypatch):
    h_a, org_a, uid_a, tenant_a = _env(client, mailer, monkeypatch, "ta")
    h_b, org_b, uid_b, tenant_b = _env(client, mailer, monkeypatch, "tb")
    a = _account(client, h_a, org_a, mailer); b = _account(client, h_b, org_b, mailer)
    _dispatch(client, h_a, _approved(client, h_a, _lead(org_a, uid_a), a["id"]))
    _dispatch(client, h_b, _approved(client, h_b, _lead(org_b, uid_b), b["id"]))
    assert {t for (t, _k) in mailer.dispatches} == {tenant_a, tenant_b}
    for (t, _k), rec in mailer.dispatches.items():
        assert mailer.mailboxes[rec["mailbox"]]["tenant"] == t          # each used its OWN tenant's mailbox


# ------------------------------------- the documented distributed inconsistency window
def test_disable_with_mailer_down_blocks_new_dispatch_while_sync_stays_pending(client, mailer, monkeypatch):
    """LeadBoost disables the account but the Mailer disable fails. No cross-service
    atomicity is claimed: the Mailer mailbox is still ACTIVE, the row is 'pending'.
    The LeadBoost dispatch gate must still refuse every NEW dispatch."""
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "window")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    ref = list(mailer.mailboxes)[0]

    mailer.fail_next(503)
    deleted = client.delete(f"/api/v2/organizations/{org}/email-accounts/{acc['id']}", headers=h).json()
    assert (deleted["is_active"], deleted["mailer_sync_state"], deleted["mailer_sync_error_code"]) == (
        False, MailerSyncState.PENDING, MailerSyncErrorCode.MAILER_UNAVAILABLE)
    assert mailer.mailboxes[ref]["status"] == "active"                  # the inconsistency window, made visible
    mailer.calls.clear()

    blocked = _dispatch(client, h, aid).json()
    assert blocked["state"] == OutreachState.DISPATCH_FAILED
    assert _dispatch_calls(mailer) == [] and mailer.dispatches == {}      # nothing reached the Mailer

    assert client.post(f"/api/v2/organizations/{org}/email-accounts/{acc['id']}/mailer-sync", headers=h).json()["mailer_sync_state"] == MailerSyncState.SYNCED
    assert mailer.mailboxes[ref]["status"] == "disabled"                  # converged by the retry


def test_credential_rotation_with_mailer_down_blocks_new_dispatch(client, mailer, monkeypatch):
    h, org, uid, tenant = _env(client, mailer, monkeypatch, "rotdown")
    acc = _account(client, h, org, mailer)
    aid = _approved(client, h, _lead(org, uid), acc["id"])
    ref = list(mailer.mailboxes)[0]
    mailer.fail_next(503)
    r = client.patch(f"/api/v2/organizations/{org}/email-accounts/{acc['id']}", headers=h, json={"credential": "brand-new-secret"})
    assert r.json()["verification_status"] == VerificationStatus.UNVERIFIED and r.json()["mailer_sync_state"] == MailerSyncState.PENDING
    assert mailer.mailboxes[ref]["status"] == "active"                  # Mailer not yet told
    mailer.calls.clear()

    assert _dispatch(client, h, aid).json()["state"] == OutreachState.DISPATCH_FAILED
    assert _dispatch_calls(mailer) == []
