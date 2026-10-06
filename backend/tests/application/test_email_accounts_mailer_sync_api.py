"""
L1: the email-account API drives the Mailer mailbox lifecycle.

verify / connection-sensitive PATCH / enable-disable / DELETE / explicit retry,
end to end through the real FastAPI app, the real sync service and the real
Mailer client, against the in-memory Mailer (tests/application/fake_mailer.py).
"""

import json
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import main
from core.domain.models.email_account import EmailAccount, MailerSyncErrorCode, MailerSyncState, VerificationStatus
from core.infrastructure.database import SessionLocal
from core.infrastructure.email.smtp_verifier import VerificationResult
from core.infrastructure.mailing_agent import client as mc
from tests.application.fake_mailer import FakeMailer

SECRET = "Api-level-SMTP-secret-1"
ROTATED = "Api-level-ROTATED-secret-2"
VERIFY = "api.endpoints.email_accounts.verify_smtp_mailbox"


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


def _login(client, tag):
    email = f"l1_{tag}_{uuid.uuid4().hex[:6]}@example.com"
    assert client.post("/api/v2/register", json={"email": email, "password": "TestPass123!", "first_name": "S"}).status_code == 200
    token = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"}).json()["access_token"]
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    return {"Authorization": f"Bearer {token}"}, me["organization_id"]


def _payload(**over):
    p = dict(provider="smtp", email_address=f"s_{uuid.uuid4().hex[:6]}@example.com", display_name="Sales",
             smtp_host="smtp.example.com", smtp_port=587, security_mode="starttls",
             credential_type="smtp_password", credential=SECRET)
    p.update(over)
    return p


def _base(org):
    return f"/api/v2/organizations/{org}/email-accounts"


def _verify(client, h, org, acc_id, ok=True):
    result = VerificationResult(status=VerificationStatus.VERIFIED if ok else VerificationStatus.FAILED,
                                error_code=None if ok else "auth_failed")
    with patch(VERIFY, return_value=result):
        return client.post(f"{_base(org)}/{acc_id}/verify", headers=h)


def _ref(acc_id):
    s = SessionLocal()
    try:
        return s.get(EmailAccount, acc_id).mailer_mailbox_ref
    finally:
        s.close()


def _setup(client, mailer, monkeypatch, tag, **over):
    h, org = _login(client, tag)
    tenant = mailer.map_org(monkeypatch, org)
    acc = client.post(_base(org), headers=h, json=_payload(**over)).json()
    return h, org, tenant, acc


# ------------------------------------------------------------------ verify
def test_creating_an_account_does_not_contact_the_mailer(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "create")
    assert mailer.calls == [] and _ref(acc["id"]) is None
    assert acc["mailer_sync_state"] == MailerSyncState.PENDING and acc["mailer_sync_error_code"] is None


def test_successful_verification_provisions_the_mailbox_and_reports_it(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "verify")
    r = _verify(client, h, org, acc["id"])
    body = r.json()
    assert r.status_code == 200 and body["verification_status"] == VerificationStatus.VERIFIED
    assert (body["mailer_sync_state"], body["mailer_sync_error_code"]) == (MailerSyncState.SYNCED, None)
    assert len(mailer.active_boxes(tenant)) == 1 and _ref(acc["id"]) == mailer.active_boxes(tenant)[0]["public_reference"]


def test_failed_verification_never_provisions(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "vfail")
    r = _verify(client, h, org, acc["id"], ok=False)
    assert r.json()["verification_status"] == VerificationStatus.FAILED
    assert mailer.calls == [] and mailer.mailboxes == {}


def test_mailer_outage_does_not_change_the_verification_outcome_and_is_retryable(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "outage")
    mailer.fail_next(503)
    r = _verify(client, h, org, acc["id"])
    body = r.json()
    assert r.status_code == 200 and body["verification_status"] == VerificationStatus.VERIFIED   # LeadBoost's fact stands
    assert (body["mailer_sync_state"], body["mailer_sync_error_code"]) == (
        MailerSyncState.PENDING, MailerSyncErrorCode.MAILER_UNAVAILABLE)

    retry = client.post(f"{_base(org)}/{acc['id']}/mailer-sync", headers=h)
    assert retry.status_code == 200
    assert (retry.json()["mailer_sync_state"], retry.json()["mailer_sync_error_code"]) == (MailerSyncState.SYNCED, None)
    assert len(mailer.active_boxes(tenant)) == 1


def test_implicit_tls_account_verifies_but_is_reported_unsupported_for_integrated_sending(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "tls", security_mode="tls", smtp_port=465)
    body = _verify(client, h, org, acc["id"]).json()
    assert body["verification_status"] == VerificationStatus.VERIFIED
    assert (body["mailer_sync_state"], body["mailer_sync_error_code"]) == (
        MailerSyncState.PENDING, MailerSyncErrorCode.UNSUPPORTED_SECURITY_MODE)
    assert mailer.calls == []


# ------------------------------------------------------------------ update
def test_cosmetic_edit_does_not_touch_the_mailer(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "cosmetic")
    _verify(client, h, org, acc["id"])
    mailer.calls.clear()
    r = client.patch(f"{_base(org)}/{acc['id']}", headers=h, json={"display_name": "Renamed"})
    assert r.status_code == 200 and r.json()["mailer_sync_state"] == MailerSyncState.SYNCED
    assert mailer.calls == []


def test_connection_change_unverifies_and_disables_the_mailbox_without_pushing_unverified_config(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "conn")
    _verify(client, h, org, acc["id"])
    ref = _ref(acc["id"])
    mailer.calls.clear()

    r = client.patch(f"{_base(org)}/{acc['id']}", headers=h, json={"smtp_host": "smtp.new.example.com"})

    assert r.json()["verification_status"] == VerificationStatus.UNVERIFIED
    assert [(c.method, c.body) for c in mailer.calls] == [("PATCH", {"status": "disabled"})]
    box = mailer.mailboxes[ref]
    assert box["status"] == "disabled" and box["smtp_host"] == "smtp.example.com"    # old, verified config; not the new one
    assert r.json()["mailer_sync_state"] == MailerSyncState.SYNCED


def test_credential_rotation_disables_until_reverification_then_activates_atomically(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "rotate")
    _verify(client, h, org, acc["id"])
    ref = _ref(acc["id"])
    mailer.calls.clear()

    client.patch(f"{_base(org)}/{acc['id']}", headers=h,
                 json={"credential": ROTATED, "smtp_host": "smtp.rotated.example.com", "smtp_port": 2525})

    assert mailer.mailboxes[ref]["status"] == "disabled"               # cannot send in the meantime
    assert mailer.active_boxes(tenant) == []
    assert ROTATED not in json.dumps([c.body for c in mailer.calls])   # the UNVERIFIED credential was not sent
    assert mailer.mailboxes[ref]["smtp_password"] == SECRET            # Mailer still holds the old, verified one

    mailer.calls.clear()
    r = _verify(client, h, org, acc["id"])
    assert r.json()["mailer_sync_state"] == MailerSyncState.SYNCED
    assert len(mailer.calls) == 1 and mailer.calls[0].method == "PATCH"
    assert mailer.calls[0].body == {
        "status": "active", "smtp_host": "smtp.rotated.example.com", "smtp_port": 2525, "smtp_use_tls": True,
        "smtp_username": acc["email_address"], "smtp_password": ROTATED}
    assert mailer.mailboxes[ref]["status"] == "active" and mailer.mailboxes[ref]["smtp_password"] == ROTATED
    assert len(mailer.tenant_boxes(tenant)) == 1


def test_toggling_is_active_disables_and_reenables(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "toggle")
    _verify(client, h, org, acc["id"])
    ref = _ref(acc["id"])

    client.patch(f"{_base(org)}/{acc['id']}", headers=h, json={"is_active": False})
    assert mailer.mailboxes[ref]["status"] == "disabled"

    r = client.patch(f"{_base(org)}/{acc['id']}", headers=h, json={"is_active": True})   # still VERIFIED
    assert mailer.mailboxes[ref]["status"] == "active" and r.json()["mailer_sync_state"] == MailerSyncState.SYNCED


def test_delete_disables_the_mailbox(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "delete")
    _verify(client, h, org, acc["id"])
    ref = _ref(acc["id"])
    r = client.delete(f"{_base(org)}/{acc['id']}", headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert mailer.mailboxes[ref]["status"] == "disabled" and r.json()["mailer_sync_state"] == MailerSyncState.SYNCED


def test_delete_while_mailer_is_down_stays_pending_and_recovers_on_retry(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "deldown")
    _verify(client, h, org, acc["id"])
    ref = _ref(acc["id"])
    mailer.timeout_next()
    r = client.delete(f"{_base(org)}/{acc['id']}", headers=h)
    assert r.status_code == 200                                           # LeadBoost's disable stands
    assert (r.json()["is_active"], r.json()["mailer_sync_state"], r.json()["mailer_sync_error_code"]) == (
        False, MailerSyncState.PENDING, MailerSyncErrorCode.TIMEOUT)
    assert mailer.mailboxes[ref]["status"] == "active"                    # the documented inconsistency window

    healed = client.post(f"{_base(org)}/{acc['id']}/mailer-sync", headers=h).json()
    assert healed["mailer_sync_state"] == MailerSyncState.SYNCED and mailer.mailboxes[ref]["status"] == "disabled"


# ----------------------------------------------------- tenancy / not settable
def test_retry_endpoint_is_organization_scoped(client, mailer, monkeypatch):
    h_a, org_a, _, acc_a = _setup(client, mailer, monkeypatch, "ta")
    h_b, org_b, _, _ = _setup(client, mailer, monkeypatch, "tb")
    _verify(client, h_a, org_a, acc_a["id"])
    mailer.calls.clear()

    assert client.post(f"{_base(org_b)}/{acc_a['id']}/mailer-sync", headers=h_b).status_code == 404   # own org, foreign id
    assert client.post(f"{_base(org_a)}/{acc_a['id']}/mailer-sync", headers=h_b).status_code == 403   # foreign org path
    assert client.post(f"{_base(org_a)}/{acc_a['id']}/mailer-sync").status_code in (401, 403)         # no auth
    assert mailer.calls == []


def test_the_mailer_reference_and_sync_state_cannot_be_set_by_a_client(client, mailer, monkeypatch):
    h, org, tenant, _ = _setup(client, mailer, monkeypatch, "noset")
    forged = dict(mailer_mailbox_ref="attacker-ref", mailer_sync_state=MailerSyncState.SYNCED, mailer_sync_error_code="x")
    created = client.post(_base(org), headers=h, json=_payload(**forged)).json()
    assert _ref(created["id"]) is None and created["mailer_sync_state"] == MailerSyncState.PENDING

    r = client.patch(f"{_base(org)}/{created['id']}", headers=h, json=forged)
    assert r.status_code == 200 and _ref(created["id"]) is None
    assert r.json()["mailer_sync_state"] == MailerSyncState.PENDING


def test_no_response_ever_contains_the_reference_or_a_credential(client, mailer, monkeypatch):
    h, org, tenant, acc = _setup(client, mailer, monkeypatch, "leak")
    texts = [_verify(client, h, org, acc["id"]).text]
    ref = _ref(acc["id"])
    texts += [
        client.get(_base(org), headers=h).text,
        client.get(f"{_base(org)}/{acc['id']}", headers=h).text,
        client.patch(f"{_base(org)}/{acc['id']}", headers=h, json={"credential": ROTATED}).text,
        client.post(f"{_base(org)}/{acc['id']}/mailer-sync", headers=h).text,
        client.delete(f"{_base(org)}/{acc['id']}", headers=h).text,
    ]
    blob = "\n".join(texts)
    assert ref and ref not in blob and "mailer_mailbox_ref" not in blob
    assert SECRET not in blob and ROTATED not in blob and "encrypted_credential" not in blob
