"""
P1.4: tests for OutreachAction, OrganizationOutreachPolicy, and the
Mailing Agent dispatch contract.

Covers, per the P1.4 brief's mandatory test categories:
  - domain/state-machine behavior (valid and invalid transitions)
  - API behavior (create/list/get/approve/cancel/dispatch)
  - organization isolation (a lead/sender/action from org A is never
    readable, usable, or actionable by org B)
  - sender ownership + verified-sender requirement
  - idempotency (implicit replay and explicit client-supplied key)
  - manual vs automatic authorization semantics, including policy limits
  - safe API representations (no credential-shaped field ever appears)
  - the Mailing Agent contract (mocked -- see NEVER SENDS REAL MAIL below)
  - error handling (message not ready, invalid recipient, invalid
    sender, duplicate/retry, stale state, already-approved/cancelled)
  - the atomic DISPATCHING claim actually being exclusive under
    concurrency (TestDispatchConcurrency) -- see that class's docstring

NEVER SENDS REAL MAIL: no test in this file ever calls the real Mailing
Agent over the network. Tests that exercise a failed-because-unconfigured
dispatch rely on MAILING_AGENT_BASE_URL being unset in the test
environment (core/infrastructure/mailing_agent/client.py's own
`is_configured()` guard short-circuits before any network attempt).
Tests that need a *successful* dispatch instead monkeypatch
application.services.outreach_service's imported reference to
`_call_mailing_agent` directly -- the same "mock the transport, not a
network layer" approach test_email_accounts_api.py already uses for
smtp_verifier. See tests/application/test_mailing_agent_client.py for
the client's own transport-security and idempotency-requirement tests.
"""

import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

import main
from application.services import outreach_service
from application.services.outreach_service import OutreachError
from core.domain.models.email_account import EmailAccount, MailerSyncState, VerificationStatus
from core.domain.models.lead import Lead
from core.domain.models.outreach_action import OutreachState
from core.infrastructure.database import SessionLocal, crud
from core.infrastructure.mailing_agent.client import DispatchErrorCode, DispatchResult
from core.infrastructure.security.credential_crypto import encrypt_credential

# Every key that must never appear anywhere in an OutreachAction response
# body -- same list shape as test_email_accounts_api.py's.
_FORBIDDEN_RESPONSE_KEYS = {
    "encrypted_credential", "credential", "password", "app_password",
    "plaintext", "email_credential_encryption_key",
}
SENDER_SECRET = "hunter2-app-password-do-not-leak"


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Sender"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    _complete_company_profile(me["organization_id"])
    return token, me["organization_id"], me["id"]


ORG_OFFER = "We help B2B teams ship reliable developer tooling."


def _complete_company_profile(organization_id):
    """L1: the Mailer-generated path takes its value proposition from
    Organization.description ("What does your team do?"); dispatch fails closed
    without it. A real onboarded organization has filled this in."""
    from core.domain.models.organization import Organization

    s = SessionLocal()
    try:
        s.query(Organization).filter(Organization.id == organization_id).update({"description": ORG_OFFER})
        s.commit()
    finally:
        s.close()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_lead(db_session, organization_id, owner_id, **overrides):
    fields = dict(
        organization_id=organization_id,
        owner_id=owner_id,
        website=f"https://{uuid.uuid4().hex}.example.com",
        company_name="Acme Co",
        industry="Software",
        about_text="Acme Co builds developer tools.",
        email="lead@example.com",
        contact_name="Jamie Lead",
        outreach_message="Hi Jamie, quick note about Acme Co's tooling stack.",
    )
    fields.update(overrides)
    lead = Lead(**fields)
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


def _make_sender(db_session, organization_id, *, verified=True, active=True, **overrides):
    fields = dict(
        organization_id=organization_id,
        provider="smtp",
        email_address=f"sender_{uuid.uuid4().hex[:8]}@example.com",
        display_name="Sales Outreach",
        smtp_host="smtp.example.com",
        smtp_port=587,
        security_mode="starttls",
        is_active=active,
        credential_type="smtp_password",
        encrypted_credential=encrypt_credential(SENDER_SECRET),
        verification_status=VerificationStatus.VERIFIED if verified else VerificationStatus.UNVERIFIED,
        verified_at=datetime.now(timezone.utc) if verified else None,
    )
    if verified and active:
        # L1: a verified, active sender has been reconciled with the Mailer-owned
        # mailbox. (Unverified/inactive ones have no mailbox, as in production.)
        fields.update(mailer_mailbox_ref=f"mbx_{uuid.uuid4().hex[:12]}", mailer_sync_state=MailerSyncState.SYNCED)
    fields.update(overrides)
    account = EmailAccount(**fields)
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    return account


def _create_action(client, token, *, lead_id, email_account_id, mode="manual", idempotency_key=None):
    payload = {"lead_id": lead_id, "email_account_id": email_account_id, "mode": mode}
    if idempotency_key is not None:
        payload["idempotency_key"] = idempotency_key
    return client.post("/api/v2/outreach-actions", headers=_auth(token), json=payload)


def _assert_no_credential_leak(response_json):
    text = json.dumps(response_json)
    assert SENDER_SECRET not in text

    def _walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in _FORBIDDEN_RESPONSE_KEYS, f"forbidden key '{key}' present in response"
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(response_json)


# --------------------------------------------------------------------------
# Creation: happy path, validation, response safety
# --------------------------------------------------------------------------


class TestCreateOutreachAction:
    def test_manual_action_defaults_to_pending_review(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "create_manual@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["state"] == OutreachState.PENDING_REVIEW
        assert body["mode"] == "manual"
        assert body["body"] == lead.outreach_message
        assert body["recipient_email"] == lead.email
        _assert_no_credential_leak(body)

    def test_unverified_sender_rejected(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "unverified_sender@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id, verified=False)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error_code"] == "sender_not_verified"

    def test_disabled_sender_rejected(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "disabled_sender@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id, active=False)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error_code"] == "sender_disabled"

    def test_lead_with_no_message_yet_rejected(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "no_message@example.com")
        lead = _make_lead(db_session, org_id, user_id, outreach_message=None)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error_code"] == "message_not_ready"

    def test_lead_with_no_recipient_email_rejected(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "no_recipient@example.com")
        lead = _make_lead(db_session, org_id, user_id, email=None)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["error_code"] == "recipient_invalid"

    def test_nonexistent_lead_rejected(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "no_such_lead@example.com")
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=999_999, email_account_id=sender.id)
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["error_code"] == "lead_not_found"

    def test_nonexistent_sender_rejected(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "no_such_sender@example.com")
        lead = _make_lead(db_session, org_id, user_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=999_999)
        assert r.status_code == 404, r.text
        assert r.json()["detail"]["error_code"] == "sender_not_found"


# --------------------------------------------------------------------------
# Idempotency
# --------------------------------------------------------------------------


class TestIdempotency:
    def test_implicit_retry_returns_same_action(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "implicit_retry@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        first = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        second = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

    def test_explicit_idempotency_key_replay(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "explicit_key@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        key = f"client-request-{uuid.uuid4().hex}"

        first = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, idempotency_key=key)
        second = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, idempotency_key=key)
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["idempotency_key"] == key

    def test_different_message_content_produces_different_action(self, client, db_session):
        """A lead reprocessed into a different message must NOT collapse
        onto a previous authorization -- see outreach_service.py's
        derive_idempotency_key."""
        token, org_id, user_id = _register_and_login(client, "different_message@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        first = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)

        lead.outreach_message = "A completely different follow-up message."
        db_session.commit()

        second = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id)
        assert first.json()["id"] != second.json()["id"]

    def test_explicit_key_replay_with_identical_payload_returns_existing_action(self, client, db_session):
        """The 'replay' half of proper idempotency-key semantics: the
        same client-supplied key, sent again for the exact same request,
        must return the original action -- not a new one, and not an
        error."""
        token, org_id, user_id = _register_and_login(client, "idem_replay@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        key = f"client-key-{uuid.uuid4().hex}"

        first = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, idempotency_key=key)
        second = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, idempotency_key=key)
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()["id"] == second.json()["id"]

    def test_explicit_key_reused_with_different_payload_is_rejected(self, client, db_session):
        """The 'conflict' half: proper idempotency-key semantics treat
        the same key attached to a materially different request as a
        client error, not a silent merge of the two, and never a return
        of the wrong action. Standard industry convention (e.g. Stripe's
        Idempotency-Key header) -- see
        outreach_service.py::_idempotency_payload_matches."""
        token, org_id, user_id = _register_and_login(client, "idem_conflict@example.com")
        lead_1 = _make_lead(db_session, org_id, user_id, outreach_message="Message one.")
        lead_2 = _make_lead(db_session, org_id, user_id, outreach_message="Message two.")
        sender = _make_sender(db_session, org_id)
        key = f"client-key-{uuid.uuid4().hex}"

        first = _create_action(client, token, lead_id=lead_1.id, email_account_id=sender.id, idempotency_key=key)
        assert first.status_code == 201

        second = _create_action(client, token, lead_id=lead_2.id, email_account_id=sender.id, idempotency_key=key)
        assert second.status_code == 409
        assert second.json()["detail"]["error_code"] == "idempotency_key_reused"

        # The conflicting request must never silently return lead_1's
        # action under lead_2's request -- confirm no second action was
        # created for lead_2 at all.
        lead_2_actions = client.get(
            "/api/v2/outreach-actions", headers=_auth(token), params={"lead_id": lead_2.id}
        ).json()
        assert lead_2_actions == []

    def test_auto_derived_key_includes_mode_so_manual_and_automatic_never_collide(self, client, db_session):
        """Regression test: derive_idempotency_key's digest must include
        `mode`, not just lead/sender/subject/body. Without it, a manual
        and an automatic request for the exact same lead, sender and
        message derive the identical auto-key -- and since
        _idempotency_payload_matches DOES compare mode, the second
        request would then be incorrectly rejected as
        IDEMPOTENCY_KEY_REUSED purely because its mode differs, even
        though switching a request between manual and automatic for the
        same underlying message is not a conflicting replay of anything."""
        token, org_id, user_id = _register_and_login(client, "idem_mode@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"automatic_sending_enabled": True, "require_approval_for_automatic": False},
        )
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        manual = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="manual")
        assert manual.status_code == 201
        assert manual.json()["mode"] == "manual"

        automatic = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="automatic")
        assert automatic.status_code == 201, automatic.text
        assert automatic.json()["mode"] == "automatic"

        # Two genuinely distinct actions, not a false idempotency collision.
        assert manual.json()["id"] != automatic.json()["id"]
        assert manual.json()["idempotency_key"] != automatic.json()["idempotency_key"]

    def test_derived_key_reflects_full_logical_request_including_recipient(self, client, db_session):
        """Regression test for the canonical-JSON key derivation
        (derive_idempotency_key): the auto-derived key is a canonical
        serialization of lead_id, email_account_id, mode, recipient_email,
        recipient_name, subject and body -- exactly the fields
        _idempotency_payload_matches compares -- not just message
        content. Covers, in one flow:
          1. the exact same logical request still replays onto the same
             action;
          2. changing only the recipient (contact name) produces a
             genuinely different derived key and a new, distinct action;
          3. mode remains part of the key, so an automatic request for
             the same (now-changed) recipient and message is still its
             own distinct action, not a collision with either manual
             action above (complementing
             test_auto_derived_key_includes_mode_so_manual_and_automatic_never_collide,
             which isolates the mode dimension on its own)."""
        token, org_id, user_id = _register_and_login(client, "idem_recipient@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        # 1. Same exact logical request replays.
        first = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="manual")
        replay = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="manual")
        assert first.status_code == 201 and replay.status_code == 201
        assert first.json()["id"] == replay.json()["id"]
        assert first.json()["idempotency_key"] == replay.json()["idempotency_key"]

        # 2. Changed recipient (contact name) -> different derived key,
        # new action -- the exact case a colon-delimited key that only
        # covered (lead_id, email_account_id, mode, subject, body) would
        # have missed entirely, since neither subject nor body changes.
        lead.contact_name = "Alex Newcontact"
        db_session.commit()
        recipient_changed = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="manual")
        assert recipient_changed.status_code == 201
        assert recipient_changed.json()["id"] != first.json()["id"]
        assert recipient_changed.json()["idempotency_key"] != first.json()["idempotency_key"]
        assert recipient_changed.json()["recipient_name"] == "Alex Newcontact"

        # 3. Mode still remains part of the key -- an automatic request
        # for the same (now-changed) recipient and message is its own,
        # distinct action too, not a collision with either manual
        # action above.
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"automatic_sending_enabled": True, "require_approval_for_automatic": False},
        )
        automatic = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="automatic")
        assert automatic.status_code == 201, automatic.text
        assert automatic.json()["id"] not in (first.json()["id"], recipient_changed.json()["id"])
        assert automatic.json()["idempotency_key"] not in (
            first.json()["idempotency_key"],
            recipient_changed.json()["idempotency_key"],
        )


# --------------------------------------------------------------------------
# Organization isolation
# --------------------------------------------------------------------------


class TestOrganizationIsolation:
    def test_cannot_create_using_other_orgs_lead(self, client, db_session):
        _, org_a, user_a = _register_and_login(client, "iso_a1@example.com")
        token_b, org_b, user_b = _register_and_login(client, "iso_b1@example.com")
        lead_a = _make_lead(db_session, org_a, user_a)
        sender_b = _make_sender(db_session, org_b)

        r = _create_action(client, token_b, lead_id=lead_a.id, email_account_id=sender_b.id)
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "lead_not_found"

    def test_cannot_create_using_other_orgs_sender(self, client, db_session):
        token_a, org_a, user_a = _register_and_login(client, "iso_a2@example.com")
        _, org_b, _ = _register_and_login(client, "iso_b2@example.com")
        lead_a = _make_lead(db_session, org_a, user_a)
        sender_b = _make_sender(db_session, org_b)

        r = _create_action(client, token_a, lead_id=lead_a.id, email_account_id=sender_b.id)
        assert r.status_code == 404
        assert r.json()["detail"]["error_code"] == "sender_not_found"

    def _create_org_a_action(self, client, db_session, suffix):
        token_a, org_a, user_a = _register_and_login(client, f"iso_a{suffix}@example.com")
        token_b, org_b, user_b = _register_and_login(client, f"iso_b{suffix}@example.com")
        lead_a = _make_lead(db_session, org_a, user_a)
        sender_a = _make_sender(db_session, org_a)
        action = _create_action(client, token_a, lead_id=lead_a.id, email_account_id=sender_a.id).json()
        return token_b, action["id"]

    def test_cannot_read_other_orgs_action(self, client, db_session):
        token_b, action_id = self._create_org_a_action(client, db_session, "3")
        r = client.get(f"/api/v2/outreach-actions/{action_id}", headers=_auth(token_b))
        assert r.status_code == 404

    def test_cannot_approve_other_orgs_action(self, client, db_session):
        token_b, action_id = self._create_org_a_action(client, db_session, "4")
        r = client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token_b))
        assert r.status_code == 404

    def test_cannot_cancel_other_orgs_action(self, client, db_session):
        token_b, action_id = self._create_org_a_action(client, db_session, "5")
        r = client.post(f"/api/v2/outreach-actions/{action_id}/cancel", headers=_auth(token_b))
        assert r.status_code == 404

    def test_cannot_dispatch_other_orgs_action(self, client, db_session):
        token_b, action_id = self._create_org_a_action(client, db_session, "6")
        r = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token_b))
        assert r.status_code == 404

    def test_list_is_scoped_to_own_organization(self, client, db_session):
        token_b, _action_id = self._create_org_a_action(client, db_session, "7")
        r = client.get("/api/v2/outreach-actions", headers=_auth(token_b))
        assert r.status_code == 200
        assert r.json() == []


# --------------------------------------------------------------------------
# State machine
# --------------------------------------------------------------------------


class TestStateMachine:
    def _new_action(self, client, db_session, suffix):
        token, org_id, user_id = _register_and_login(client, f"state_{suffix}@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        action = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id).json()
        return token, action["id"]

    def test_approve_from_pending_review_succeeds(self, client, db_session):
        token, action_id = self._new_action(client, db_session, "1")
        r = client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["state"] == OutreachState.APPROVED
        assert r.json()["approved_at"] is not None

    def test_approve_twice_fails_409(self, client, db_session):
        token, action_id = self._new_action(client, db_session, "2")
        client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))
        r = client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))
        assert r.status_code == 409
        assert r.json()["detail"]["error_code"] == "invalid_state_transition"

    def test_cancel_from_pending_review_succeeds(self, client, db_session):
        token, action_id = self._new_action(client, db_session, "3")
        r = client.post(f"/api/v2/outreach-actions/{action_id}/cancel", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["state"] == OutreachState.CANCELLED

    def test_cancel_from_approved_succeeds(self, client, db_session):
        token, action_id = self._new_action(client, db_session, "4")
        client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))
        r = client.post(f"/api/v2/outreach-actions/{action_id}/cancel", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["state"] == OutreachState.CANCELLED

    def test_dispatch_requires_approved_state(self, client, db_session):
        token, action_id = self._new_action(client, db_session, "5")
        r = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        assert r.status_code == 409
        assert r.json()["detail"]["error_code"] == "invalid_state_transition"

    def test_cancel_after_submitted_fails_409(self, client, db_session):
        token, action_id = self._new_action(client, db_session, "6")
        client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))
        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=True, mailing_agent_reference="ref-1")),
        ):
            dispatched = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        assert dispatched.json()["state"] == OutreachState.SUBMITTED

        r = client.post(f"/api/v2/outreach-actions/{action_id}/cancel", headers=_auth(token))
        assert r.status_code == 409


# --------------------------------------------------------------------------
# Dispatch / Mailing Agent contract
# --------------------------------------------------------------------------


class TestDispatch:
    def _approved_action(self, client, db_session, suffix):
        token, org_id, user_id = _register_and_login(client, f"dispatch_{suffix}@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        action = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id).json()
        client.post(f"/api/v2/outreach-actions/{action['id']}/approve", headers=_auth(token))
        return token, action["id"]

    def test_dispatch_without_mailing_agent_configured_fails_safely(self, client, db_session, monkeypatch):
        """No MAILING_AGENT_BASE_URL is set anywhere in this test -- the
        real Mailing Agent is not deployed yet. This must degrade to a
        safe DISPATCH_FAILED, never raise, and never attempt a network
        call (client.is_configured() short-circuits first)."""
        monkeypatch.delenv("MAILING_AGENT_BASE_URL", raising=False)
        token, action_id = self._approved_action(client, db_session, "1")

        r = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == OutreachState.DISPATCH_FAILED
        assert body["last_dispatch_error"] == DispatchErrorCode.NOT_CONFIGURED

    def test_dispatch_success_moves_to_submitted(self, client, db_session):
        token, action_id = self._approved_action(client, db_session, "2")
        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=True, mailing_agent_reference="agent-ref-42")),
        ):
            r = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == OutreachState.SUBMITTED
        assert body["mailing_agent_reference"] == "agent-ref-42"
        assert body["submitted_at"] is not None
        _assert_no_credential_leak(body)

    def test_dispatch_rejected_moves_to_dispatch_failed_and_can_retry(self, client, db_session):
        token, action_id = self._approved_action(client, db_session, "3")
        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)),
        ):
            first = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        assert first.json()["state"] == OutreachState.DISPATCH_FAILED
        assert first.json()["last_dispatch_error"] == DispatchErrorCode.REJECTED

        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=True, mailing_agent_reference="agent-ref-retry")),
        ):
            second = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        assert second.json()["state"] == OutreachState.SUBMITTED
        assert second.json()["dispatch_attempts"] == 2

    def test_dispatch_calls_mailing_agent_with_expected_contract_fields(self, client, db_session):
        token, action_id = self._approved_action(client, db_session, "4")
        mock = AsyncMock(return_value=DispatchResult(accepted=True, mailing_agent_reference="ref"))
        with patch("application.services.outreach_service._call_mailing_agent", new=mock):
            client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))

        assert mock.await_count == 1
        _, kwargs = mock.await_args
        assert kwargs["outreach_action_id"] == action_id
        assert kwargs["idempotency_key"]  # the hard contract requirement -- never empty
        assert kwargs["recipient_email"] == "lead@example.com"
        assert kwargs["recipient_name"] == "Jamie Lead"
        assert kwargs["recipient_company"] == "Acme Co"
        assert kwargs["value_proposition"] == ORG_OFFER
        assert "Industry: Software" in kwargs["recipient_facts"]
        # L1: the OLD contract's credential, SMTP settings and pre-written message
        # are no longer passed at all -- the Mailer owns the send-time credential and
        # generates the message. Not even the ciphertext is handed over.
        assert not (set(kwargs) & {
            "plaintext_credential", "credential_type", "smtp_host", "smtp_port", "security_mode",
            "smtp_username", "sender_email_address", "sender_display_name", "subject", "body",
        })
        assert SENDER_SECRET not in repr(kwargs) and encrypt_credential(SENDER_SECRET) not in repr(kwargs)

    def test_dispatch_response_never_leaks_credential(self, client, db_session):
        token, action_id = self._approved_action(client, db_session, "5")
        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=True, mailing_agent_reference="ref")),
        ):
            r = client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))
        _assert_no_credential_leak(r.json())


# --------------------------------------------------------------------------
# Dispatch concurrency -- proves the atomic DISPATCHING claim fix
# --------------------------------------------------------------------------


class TestDispatchConcurrency:
    """Proves the reviewer-identified race is actually fixed: two
    concurrent dispatch attempts for the same action can never both
    reach the Mailing Agent.

    Two levels of proof:

    1. A direct, sequential unit test of crud.claim_outreach_action_for_dispatch
       itself -- the exact SQL operation the fix relies on. Calling it
       twice against a row that only had one eligible source state can
       only succeed once; no concurrency needed to prove this, because
       the property being tested is the atomicity of a single UPDATE
       statement, not a timing behavior.

    2. An end-to-end proof using two independent OutreachAction dispatch
       calls interleaved via asyncio.gather. This relies on (and
       demonstrates) a real property of Python's cooperative
       single-threaded event loop, not on wall-clock timing: a coroutine
       runs synchronously, without yielding to any other coroutine,
       until it hits an `await` that actually suspends. dispatch_action
       has no such suspension point until its call to the Mailing Agent
       client -- so the first call to actually start running is
       guaranteed to complete its claim UPDATE and commit *before* the
       second call gets a chance to execute anything at all. This makes
       the test deterministic (no sleep-based flakiness) while still
       genuinely exercising two "requests" racing for the same action,
       exactly as two concurrent HTTP requests (each with their own DB
       session, like the two independent sessions used here) would.
    """

    def _approved_action(self, db_session, client, suffix):
        token, org_id, user_id = _register_and_login(client, f"concurrency_{suffix}@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        action = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id).json()
        client.post(f"/api/v2/outreach-actions/{action['id']}/approve", headers=_auth(token))
        return token, org_id, action["id"]

    def test_claim_is_single_use_from_approved(self, client, db_session):
        _token, org_id, action_id = self._approved_action(db_session, client, "claim1")

        first = crud.claim_outreach_action_for_dispatch(db_session, organization_id=org_id, action_id=action_id)
        db_session.commit()
        second = crud.claim_outreach_action_for_dispatch(db_session, organization_id=org_id, action_id=action_id)
        db_session.commit()

        assert first == 1
        assert second == 0

    def test_two_real_threads_racing_the_same_dispatch_claim_exactly_one_wins(self, client, db_session):
        """Complements the sequential proof above and the asyncio-based
        end-to-end proof further down with genuine OS-level thread
        concurrency on the claim primitive itself -- two independent
        SessionLocal sessions, a threading.Barrier to align their attempts,
        both calling crud.claim_outreach_action_for_dispatch directly for
        the same APPROVED row. Exactly one must come back 1; the other 0."""
        _token, org_id, action_id = self._approved_action(db_session, client, "claim_threads")

        barrier = threading.Barrier(2)
        results = {}

        def _claim(name):
            session = SessionLocal()
            try:
                barrier.wait()
                claimed = crud.claim_outreach_action_for_dispatch(
                    session, organization_id=org_id, action_id=action_id
                )
                session.commit()
                results[name] = claimed
            finally:
                session.close()

        t1 = threading.Thread(target=_claim, args=("one",))
        t2 = threading.Thread(target=_claim, args=("two",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert sorted(results.values()) == [0, 1], f"exactly one claim should win, got: {results}"

    def test_claim_is_single_use_from_dispatch_failed(self, client, db_session):
        _token, org_id, action_id = self._approved_action(db_session, client, "claim2")
        # Move it to dispatch_failed first via a real (mocked) failed attempt.
        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)),
        ):
            client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(_token))

        first = crud.claim_outreach_action_for_dispatch(db_session, organization_id=org_id, action_id=action_id)
        db_session.commit()
        second = crud.claim_outreach_action_for_dispatch(db_session, organization_id=org_id, action_id=action_id)
        db_session.commit()

        assert first == 1
        assert second == 0

    async def test_concurrent_dispatch_of_approved_action_only_one_reaches_mailing_agent(self, client, db_session):
        token, org_id, action_id = self._approved_action(db_session, client, "race1")

        call_count = 0

        async def _slow_accept(**kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)  # yield once, deliberately -- see class docstring
            return DispatchResult(accepted=True, mailing_agent_reference="winner-ref")

        session_a = SessionLocal()
        session_b = SessionLocal()
        try:
            with patch("application.services.outreach_service._call_mailing_agent", new=_slow_accept):
                outcomes = await asyncio.gather(
                    outreach_service.dispatch_action(session_a, organization_id=org_id, action_id=action_id),
                    outreach_service.dispatch_action(session_b, organization_id=org_id, action_id=action_id),
                    return_exceptions=True,
                )
        finally:
            session_a.close()
            session_b.close()

        assert call_count == 1, "the Mailing Agent must be contacted exactly once, never twice"

        successes = [o for o in outcomes if not isinstance(o, Exception)]
        errors = [o for o in outcomes if isinstance(o, OutreachError)]
        assert len(successes) == 1
        assert successes[0].state == OutreachState.SUBMITTED
        assert len(errors) == 1
        assert errors[0].error_code == "invalid_state_transition"

        final = client.get(f"/api/v2/outreach-actions/{action_id}", headers=_auth(token)).json()
        assert final["state"] == OutreachState.SUBMITTED
        assert final["dispatch_attempts"] == 1, "a losing request must never increment the attempt counter"

    async def test_concurrent_retry_of_dispatch_failed_action_only_one_reaches_mailing_agent(self, client, db_session):
        token, org_id, action_id = self._approved_action(db_session, client, "race2")
        with patch(
            "application.services.outreach_service._call_mailing_agent",
            new=AsyncMock(return_value=DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)),
        ):
            client.post(f"/api/v2/outreach-actions/{action_id}/dispatch", headers=_auth(token))

        call_count = 0

        async def _slow_accept(**kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)
            return DispatchResult(accepted=True, mailing_agent_reference="retry-winner-ref")

        session_a = SessionLocal()
        session_b = SessionLocal()
        try:
            with patch("application.services.outreach_service._call_mailing_agent", new=_slow_accept):
                outcomes = await asyncio.gather(
                    outreach_service.dispatch_action(session_a, organization_id=org_id, action_id=action_id),
                    outreach_service.dispatch_action(session_b, organization_id=org_id, action_id=action_id),
                    return_exceptions=True,
                )
        finally:
            session_a.close()
            session_b.close()

        assert call_count == 1
        successes = [o for o in outcomes if not isinstance(o, Exception)]
        assert len(successes) == 1
        assert successes[0].state == OutreachState.SUBMITTED

        final = client.get(f"/api/v2/outreach-actions/{action_id}", headers=_auth(token)).json()
        assert final["state"] == OutreachState.SUBMITTED
        assert final["dispatch_attempts"] == 2  # the first (failed) attempt + the one winning retry

    def test_cannot_cancel_while_dispatching(self, client, db_session):
        """DISPATCHING is deliberately excluded from
        OutreachState.CANCELLABLE_FROM -- see that class's docstring for
        why. Proven the same way as the races above: hold the action in
        DISPATCHING via a suspended mocked Mailing Agent call, and
        attempt to cancel it from a second, independent session while
        it's still there."""
        token, org_id, action_id = self._approved_action(db_session, client, "cancel_race")

        async def _run():
            resume = asyncio.Event()

            async def _hang_until_resumed(**kwargs):
                await resume.wait()
                return DispatchResult(accepted=True, mailing_agent_reference="ref")

            session_a = SessionLocal()
            try:
                with patch("application.services.outreach_service._call_mailing_agent", new=_hang_until_resumed):
                    dispatch_task = asyncio.create_task(
                        outreach_service.dispatch_action(session_a, organization_id=org_id, action_id=action_id)
                    )
                    await asyncio.sleep(0)  # let dispatch_action reach its claim + the hanging call

                    with pytest.raises(OutreachError) as exc_info:
                        outreach_service.cancel_action(db_session, organization_id=org_id, action_id=action_id)
                    assert exc_info.value.error_code == "invalid_state_transition"

                    resume.set()
                    await dispatch_task
            finally:
                session_a.close()

        asyncio.run(_run())

        final = client.get(f"/api/v2/outreach-actions/{action_id}", headers=_auth(token)).json()
        assert final["state"] == OutreachState.SUBMITTED


# --------------------------------------------------------------------------
# Approve/cancel concurrency -- proves the atomic approval/cancellation
# claim fix (approve_action/cancel_action no longer read-then-write)
# --------------------------------------------------------------------------


class TestApproveCancelConcurrency:
    """Proves the fix for approve_action/cancel_action's own race: both
    now use an atomic conditional UPDATE
    (crud.claim_outreach_action_for_approval /
    claim_outreach_action_for_cancellation) instead of the previous
    "read state in Python, check it, mutate the object, commit", so a
    concurrent approve and cancel on the same row can never both
    succeed, and a cancel can never land on a row a dispatch attempt has
    already (atomically) claimed into DISPATCHING.

    Unlike dispatch_action, approve_action/cancel_action are synchronous
    functions, so real OS threads (not asyncio interleaving) are used
    here to exercise a genuine race: a threading.Barrier makes both
    threads attempt their UPDATE at essentially the same instant, and
    SQLite's own file-level write serialization (backed by Python's
    sqlite3 driver's default 5-second busy-timeout, comfortably longer
    than this test's microsecond-scale contention) resolves which one's
    statement actually executes first -- exactly the same real database
    behavior that would resolve two genuinely concurrent HTTP requests.
    """

    def _pending_action(self, db_session, client, suffix):
        token, org_id, user_id = _register_and_login(client, f"approve_cancel_race_{suffix}@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        action = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id).json()
        return token, org_id, user_id, action["id"]

    def test_concurrent_approve_and_cancel_from_pending_review_exactly_one_wins(self, client, db_session):
        """The real invariant here is asymmetric, not "exactly one of
        {approve, cancel} succeeds": OutreachState.CANCELLABLE_FROM
        legitimately includes APPROVED (a user may cancel an
        already-approved, not-yet-dispatched action), so if approve wins
        the race for the PENDING_REVIEW row first, cancel's claim can --
        correctly -- ALSO succeed immediately afterward, cancelling the
        now-approved action. That is intended product behavior, not a
        race bug, so a strict "exactly one claim returns 1" assertion
        would be wrong.

        The property that actually matters, and that the old
        read-then-write implementation could violate, is: exactly one of
        the two requests may consume the *original* PENDING_REVIEW state,
        and the loser of that specific race must be told so (claimed=0)
        rather than blindly overwriting the winner's result. Concretely:
        if cancel reaches (and consumes) the row while it is still
        PENDING_REVIEW, approve's claim must fail outright (0) -- it must
        never silently re-approve a row that was already cancelled out
        from under it, which is exactly what the previous
        read-state-in-Python-then-write-it-back implementation could do.
        """
        token, org_id, user_id, action_id = self._pending_action(db_session, client, "1")

        barrier = threading.Barrier(2)
        results = {}

        def _approve():
            session = SessionLocal()
            try:
                barrier.wait()
                claimed = crud.claim_outreach_action_for_approval(
                    session,
                    organization_id=org_id,
                    action_id=action_id,
                    approved_by_user_id=user_id,
                    approved_at=datetime.now(timezone.utc),
                )
                session.commit()
                results["approve"] = claimed
            finally:
                session.close()

        def _cancel():
            session = SessionLocal()
            try:
                barrier.wait()
                claimed = crud.claim_outreach_action_for_cancellation(
                    session,
                    organization_id=org_id,
                    action_id=action_id,
                    cancelled_at=datetime.now(timezone.utc),
                )
                session.commit()
                results["cancel"] = claimed
            finally:
                session.close()

        t1 = threading.Thread(target=_approve)
        t2 = threading.Thread(target=_cancel)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Cancel's allowed-source set is a superset of approve's, so in a
        # plain two-way race starting from PENDING_REVIEW, cancel always
        # eventually finds a claimable row (either still PENDING_REVIEW,
        # or APPROVED if approve won first) -- it is approve's outcome
        # that actually reveals who won the race for PENDING_REVIEW.
        assert results["cancel"] == 1, f"cancel should always find a claimable row here, got: {results}"

        final = client.get(f"/api/v2/outreach-actions/{action_id}", headers=_auth(token)).json()
        assert final["state"] == OutreachState.CANCELLED
        assert final["cancelled_at"] is not None

        if results["approve"] == 0:
            # cancel reached (and consumed) the row while it was still
            # PENDING_REVIEW -- approve correctly found nothing left to
            # claim rather than silently re-approving a cancelled row.
            assert final["approved_by_user_id"] is None
            assert final["approved_at"] is None
        else:
            # approve won the race for the original PENDING_REVIEW state
            # first; cancel's claim then validly cancelled the
            # now-APPROVED action -- legitimate sequential behavior, not
            # a race bug (see this test's docstring).
            assert final["approved_by_user_id"] == user_id
            assert final["approved_at"] is not None

    def test_concurrent_dispatch_claim_and_cancel_dispatching_cannot_become_cancelled(self, client, db_session):
        token, org_id, user_id, action_id = self._pending_action(db_session, client, "2")
        client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))

        barrier = threading.Barrier(2)
        results = {}

        def _dispatch_claim():
            session = SessionLocal()
            try:
                barrier.wait()
                claimed = crud.claim_outreach_action_for_dispatch(
                    session, organization_id=org_id, action_id=action_id
                )
                session.commit()
                results["dispatch_claim"] = claimed
            finally:
                session.close()

        def _cancel():
            session = SessionLocal()
            try:
                barrier.wait()
                claimed = crud.claim_outreach_action_for_cancellation(
                    session,
                    organization_id=org_id,
                    action_id=action_id,
                    cancelled_at=datetime.now(timezone.utc),
                )
                session.commit()
                results["cancel"] = claimed
            finally:
                session.close()

        t1 = threading.Thread(target=_dispatch_claim)
        t2 = threading.Thread(target=_cancel)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert sorted(results.values()) == [0, 1], f"exactly one claim should win, got: {results}"

        final_state = client.get(f"/api/v2/outreach-actions/{action_id}", headers=_auth(token)).json()["state"]
        if results["dispatch_claim"] == 1:
            # dispatch won the race -- the row is DISPATCHING (this test
            # only proves the claim, it doesn't complete a full dispatch)
            # and must NEVER become CANCELLED afterward.
            assert final_state == OutreachState.DISPATCHING
        else:
            # cancel won -- dispatch's claim correctly found nothing left
            # to claim, since the row was already CANCELLED by the time
            # its UPDATE ran.
            assert final_state == OutreachState.CANCELLED

    def test_already_dispatching_cannot_be_cancelled(self, client, db_session):
        """Simpler, non-concurrent proof of the same invariant as the
        test above: once a row is (by whatever means) in DISPATCHING,
        cancel_action must refuse it outright -- DISPATCHING is
        deliberately not in OutreachState.CANCELLABLE_FROM (see that
        class's docstring)."""
        token, org_id, user_id, action_id = self._pending_action(db_session, client, "3")
        client.post(f"/api/v2/outreach-actions/{action_id}/approve", headers=_auth(token))

        claimed = crud.claim_outreach_action_for_dispatch(db_session, organization_id=org_id, action_id=action_id)
        db_session.commit()
        assert claimed == 1

        with pytest.raises(OutreachError) as exc_info:
            outreach_service.cancel_action(db_session, organization_id=org_id, action_id=action_id)
        assert exc_info.value.error_code == "invalid_state_transition"

        r = client.post(f"/api/v2/outreach-actions/{action_id}/cancel", headers=_auth(token))
        assert r.status_code == 409
        assert r.json()["detail"]["error_code"] == "invalid_state_transition"


# --------------------------------------------------------------------------
# Automatic-mode policy
# --------------------------------------------------------------------------


class TestAutomaticPolicy:
    def test_automatic_mode_rejected_when_org_has_not_opted_in(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "auto_optin@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="automatic")
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "automatic_sending_disabled"

    def test_automatic_mode_with_default_policy_still_requires_approval(self, client, db_session):
        """automatic_sending_enabled=True alone is not enough --
        require_approval_for_automatic defaults True (safest default)."""
        token, org_id, user_id = _register_and_login(client, "auto_default@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"automatic_sending_enabled": True},
        )
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="automatic")
        assert r.status_code == 201
        assert r.json()["state"] == OutreachState.PENDING_REVIEW

    def test_automatic_mode_fully_relaxed_auto_approves(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "auto_relaxed@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"automatic_sending_enabled": True, "require_approval_for_automatic": False},
        )
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="automatic")
        assert r.status_code == 201
        assert r.json()["state"] == OutreachState.APPROVED

    def test_automatic_mode_respects_daily_limit(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "auto_limit@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={
                "automatic_sending_enabled": True,
                "require_approval_for_automatic": False,
                "daily_send_limit": 1,
            },
        )
        sender = _make_sender(db_session, org_id)

        lead_1 = _make_lead(db_session, org_id, user_id, outreach_message="Message one.")
        first = _create_action(client, token, lead_id=lead_1.id, email_account_id=sender.id, mode="automatic")
        assert first.json()["state"] == OutreachState.APPROVED

        lead_2 = _make_lead(db_session, org_id, user_id, outreach_message="Message two.")
        second = _create_action(client, token, lead_id=lead_2.id, email_account_id=sender.id, mode="automatic")
        assert second.status_code == 201
        assert second.json()["state"] == OutreachState.PENDING_REVIEW
        assert "limit" in second.json()["reason"].lower()

    def test_daily_limit_is_scoped_to_automatic_actions_only(self, client, db_session):
        """Regression test: daily_send_limit/hourly_send_limit are
        documented (see core/domain/models/outreach_policy.py) as gating
        AUTOMATIC-mode authorization only -- a manually-approved action
        must never consume an organization's automatic-sending quota.
        Sets up several manually-approved actions first, then confirms a
        daily_send_limit=1 automatic-mode organization can still
        auto-approve its first automatic action regardless of how many
        manual actions already exist."""
        token, org_id, user_id = _register_and_login(client, "auto_limit_scope@example.com")
        sender = _make_sender(db_session, org_id)

        # Several manually-created and manually-approved actions --
        # these must not count against the automatic daily limit below.
        for i in range(3):
            manual_lead = _make_lead(db_session, org_id, user_id, outreach_message=f"Manual message {i}.")
            manual_action = _create_action(
                client, token, lead_id=manual_lead.id, email_account_id=sender.id, mode="manual"
            ).json()
            client.post(f"/api/v2/outreach-actions/{manual_action['id']}/approve", headers=_auth(token))

        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={
                "automatic_sending_enabled": True,
                "require_approval_for_automatic": False,
                "daily_send_limit": 1,
            },
        )

        auto_lead = _make_lead(db_session, org_id, user_id, outreach_message="Automatic message.")
        r = _create_action(client, token, lead_id=auto_lead.id, email_account_id=sender.id, mode="automatic")
        assert r.status_code == 201, r.text
        assert r.json()["state"] == OutreachState.APPROVED, (
            "the 3 pre-existing manually-approved actions must not have consumed "
            "the automatic daily_send_limit=1 quota"
        )

    def test_paused_policy_forces_manual_review_even_when_relaxed(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "auto_paused@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"automatic_sending_enabled": True, "require_approval_for_automatic": False, "is_paused": True},
        )
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        r = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id, mode="automatic")
        assert r.status_code == 201
        assert r.json()["state"] == OutreachState.PENDING_REVIEW

    def test_paused_policy_blocks_dispatch_of_already_approved_action(self, client, db_session):
        token, org_id, user_id = _register_and_login(client, "auto_paused_dispatch@example.com")
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)
        action = _create_action(client, token, lead_id=lead.id, email_account_id=sender.id).json()
        client.post(f"/api/v2/outreach-actions/{action['id']}/approve", headers=_auth(token))

        client.put(f"/api/v2/organizations/{org_id}/outreach-policy", headers=_auth(token), json={"is_paused": True})

        r = client.post(f"/api/v2/outreach-actions/{action['id']}/dispatch", headers=_auth(token))
        assert r.status_code == 200
        assert r.json()["state"] == OutreachState.DISPATCH_FAILED
        assert "paused" in r.json()["last_dispatch_error"].lower()

    def test_automatic_daily_limit_respected_under_concurrent_creation(self, client, db_session):
        """Regression test for the quota race: crud.lock_outreach_policy
        serializes "count existing actions, then decide, then insert"
        for AUTOMATIC-mode create_action calls, so two concurrent
        requests can no longer both observe the same not-yet-incremented
        count and both pass a daily_send_limit meant to admit only one.
        Uses real OS threads (create_action is synchronous), the same
        threading.Barrier + SQLite-serialization approach as
        TestApproveCancelConcurrency above."""
        token, org_id, user_id = _register_and_login(client, "auto_race@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={
                "automatic_sending_enabled": True,
                "require_approval_for_automatic": False,
                "daily_send_limit": 1,
            },
        )
        sender = _make_sender(db_session, org_id)
        lead_1 = _make_lead(db_session, org_id, user_id, outreach_message="Message one.")
        lead_2 = _make_lead(db_session, org_id, user_id, outreach_message="Message two.")

        barrier = threading.Barrier(2)
        results = {}
        errors = {}

        def _create(name, lead_id):
            session = SessionLocal()
            try:
                barrier.wait()
                action, _created = outreach_service.create_action(
                    session,
                    organization_id=org_id,
                    lead_id=lead_id,
                    email_account_id=sender.id,
                    mode="automatic",
                )
                results[name] = action.state
            except Exception as exc:  # surfaced via the assertion below, not silently swallowed
                errors[name] = exc
            finally:
                session.close()

        t1 = threading.Thread(target=_create, args=("one", lead_1.id))
        t2 = threading.Thread(target=_create, args=("two", lead_2.id))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"unexpected errors from concurrent automatic creation: {errors}"
        states = list(results.values())
        assert states.count(OutreachState.APPROVED) == 1, (
            f"exactly one action should be auto-approved under daily_send_limit=1, got: {results}"
        )
        assert states.count(OutreachState.PENDING_REVIEW) == 1

        all_actions = client.get("/api/v2/outreach-actions", headers=_auth(token)).json()
        approved_count = sum(1 for a in all_actions if a["state"] == OutreachState.APPROVED)
        assert approved_count == 1

    def test_automatic_first_use_policy_race_does_not_crash(self, client, db_session):
        """Regression test for a distinct first-use race:
        get_or_create_outreach_policy itself used to be able to raise an
        unhandled IntegrityError when two concurrent requests both try
        to INSERT the very first policy row for an organization that has
        never had one -- organization_id is UNIQUE on that table, so
        only one INSERT can win. Unlike the daily-limit test above,
        which pre-creates the policy via PUT before starting its
        threads, this test deliberately does NOT -- no policy row exists
        for this organization when the two threads start."""
        token, org_id, user_id = _register_and_login(client, "policy_first_use_race@example.com")
        sender = _make_sender(db_session, org_id)
        lead_1 = _make_lead(db_session, org_id, user_id, outreach_message="Message one.")
        lead_2 = _make_lead(db_session, org_id, user_id, outreach_message="Message two.")

        barrier = threading.Barrier(2)
        results = {}
        errors = {}

        def _create(name, lead_id):
            session = SessionLocal()
            try:
                barrier.wait()
                action, _created = outreach_service.create_action(
                    session,
                    organization_id=org_id,
                    lead_id=lead_id,
                    email_account_id=sender.id,
                    mode="automatic",
                )
                results[name] = action.state
            except OutreachError as exc:
                # A freshly created policy row defaults to
                # automatic_sending_enabled=False, so both requests being
                # rejected this way is the expected, correct outcome --
                # the point of this test is that get_or_create_outreach_policy's
                # own concurrent-first-insert race doesn't crash, not
                # that automatic sending succeeds without being enabled.
                results[name] = exc.error_code
            except Exception as exc:  # an unhandled IntegrityError would land here
                errors[name] = exc
            finally:
                session.close()

        t1 = threading.Thread(target=_create, args=("one", lead_1.id))
        t2 = threading.Thread(target=_create, args=("two", lead_2.id))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"first-use policy creation must not raise under a race, got: {errors}"
        assert list(results.values()) == ["automatic_sending_disabled", "automatic_sending_disabled"]

        # Exactly one policy row exists and is normally readable afterward.
        r = client.get(f"/api/v2/organizations/{org_id}/outreach-policy", headers=_auth(token))
        assert r.status_code == 200

    def test_allowed_false_from_policy_evaluation_is_respected(self, client, db_session):
        """Direct regression test for the bug where create_action
        captured _evaluate_automatic_policy's `allowed` return value but
        never checked it. Mocking _evaluate_automatic_policy directly --
        rather than trying to reconstruct the exact timing of the race
        between the fast unlocked automatic_sending_enabled pre-check
        and the locked re-evaluation -- isolates the one thing that
        actually matters: if policy evaluation says not-allowed,
        create_action must reject, never silently insert a
        PENDING_REVIEW automatic action anyway."""
        token, org_id, user_id = _register_and_login(client, "allowed_respected@example.com")
        client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"automatic_sending_enabled": True},
        )
        lead = _make_lead(db_session, org_id, user_id)
        sender = _make_sender(db_session, org_id)

        with patch(
            "application.services.outreach_service._evaluate_automatic_policy",
            return_value=(False, False, "Automatic sending was disabled after the initial check."),
        ):
            with pytest.raises(OutreachError) as exc_info:
                outreach_service.create_action(
                    db_session,
                    organization_id=org_id,
                    lead_id=lead.id,
                    email_account_id=sender.id,
                    mode="automatic",
                )
        assert exc_info.value.error_code == "automatic_sending_disabled"

        # And no action was left behind by the aborted attempt.
        all_actions = client.get("/api/v2/outreach-actions", headers=_auth(token)).json()
        assert all_actions == []


# --------------------------------------------------------------------------
# Outreach policy endpoint
# --------------------------------------------------------------------------


class TestOutreachPolicyEndpoint:
    def test_defaults_are_safest_posture(self, client, db_session):
        token, org_id, _user_id = _register_and_login(client, "policy_defaults@example.com")
        r = client.get(f"/api/v2/organizations/{org_id}/outreach-policy", headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert body["automatic_sending_enabled"] is False
        assert body["require_approval_for_automatic"] is True
        assert body["is_paused"] is False

    def test_other_org_cannot_read_or_update_policy(self, client):
        _, org_a, _ = _register_and_login(client, "policy_iso_a@example.com")
        token_b, _org_b, _ = _register_and_login(client, "policy_iso_b@example.com")

        r_get = client.get(f"/api/v2/organizations/{org_a}/outreach-policy", headers=_auth(token_b))
        assert r_get.status_code == 403

        r_put = client.put(
            f"/api/v2/organizations/{org_a}/outreach-policy", headers=_auth(token_b), json={"is_paused": True}
        )
        assert r_put.status_code == 403

    def test_window_must_be_set_together(self, client):
        token, org_id, _user_id = _register_and_login(client, "policy_window@example.com")
        r = client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"sending_window_start_hour_utc": 9},
        )
        assert r.status_code == 422

    def test_window_start_and_end_must_differ(self, client):
        token, org_id, _user_id = _register_and_login(client, "policy_window_eq@example.com")
        r = client.put(
            f"/api/v2/organizations/{org_id}/outreach-policy",
            headers=_auth(token),
            json={"sending_window_start_hour_utc": 9, "sending_window_end_hour_utc": 9},
        )
        assert r.status_code == 422
