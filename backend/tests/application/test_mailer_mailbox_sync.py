"""
L1: LeadBoost EmailAccount <-> Mailer Mailbox reconciliation
(application/services/mailer_mailbox_sync.py).

Runs the REAL sync service and the REAL Mailer client against an in-memory
Mailer (tests/application/fake_mailer.py) mounted underneath httpx, so the
actual wire requests are asserted. No network, no real SMTP.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import pytest

from application.services.mailer_mailbox_sync import MAX_PASSES, sync_email_account
from core.domain.models.email_account import (
    EmailAccount,
    MailerSyncErrorCode,
    MailerSyncState,
    VerificationStatus,
)
from core.domain.models.organization import Organization
from core.infrastructure.database import SessionLocal, crud
from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent import mailbox_client
from core.infrastructure.security.credential_crypto import encrypt_credential
from tests.application.fake_mailer import FakeMailer

SECRET = "Lb-SMTP-secret-must-only-reach-create-or-activate"
ALL_CODES = {v for k, v in vars(MailerSyncErrorCode).items() if k.isupper()}


@pytest.fixture()
def mailer(monkeypatch):
    monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
    return FakeMailer().install(monkeypatch, mc)


def _org(db):
    org = Organization(name=f"Org {uuid.uuid4().hex[:6]}")
    db.add(org)
    db.commit()
    db.refresh(org)
    return org


def _account(db, org_id, *, verified=True, active=True, security_mode="starttls", email=None,
             credential=SECRET, ref=None, **over):
    fields = dict(
        organization_id=org_id, provider="smtp",
        email_address=email or f"Sender_{uuid.uuid4().hex[:6]}@Example.com",
        display_name="Sales", smtp_host="smtp.example.com", smtp_port=587, security_mode=security_mode,
        username="smtp-user", is_active=active, credential_type="smtp_password",
        encrypted_credential=encrypt_credential(credential) if credential else None,
        verification_status=VerificationStatus.VERIFIED if verified else VerificationStatus.UNVERIFIED,
        verified_at=datetime.now(timezone.utc) if verified else None,
        mailer_mailbox_ref=ref, mailer_sync_state=MailerSyncState.PENDING,
    )
    fields.update(over)
    acc = EmailAccount(**fields)
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


def _setup(db, mailer, monkeypatch, **acct):
    org = _org(db)
    tenant = mailer.map_org(monkeypatch, org.id)
    return org, tenant, _account(db, org.id, **acct)


# ---------------------------------------------------------------- provisioning
async def test_provisioning_success_creates_exactly_one_mailbox_and_stores_the_ref(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)

    out = await sync_email_account(db_session, org.id, acc.id)

    assert len(mailer.calls) == 1
    call = mailer.calls[0]
    assert (call.method, call.path) == ("POST", "/mailboxes")
    assert call.api_key == f"key-org-{org.id}" and call.tenant == tenant
    assert call.body == {
        "email_address": acc.email_address.lower(), "smtp_host": "smtp.example.com", "smtp_port": 587,
        "smtp_use_tls": True, "smtp_username": "smtp-user", "smtp_password": SECRET,
    }
    boxes = mailer.tenant_boxes(tenant)
    assert len(boxes) == 1 and boxes[0]["status"] == "active"
    assert out.mailer_mailbox_ref == boxes[0]["public_reference"]
    assert (out.mailer_sync_state, out.mailer_sync_error_code) == (MailerSyncState.SYNCED, None)


async def test_replay_converges_on_the_same_mailbox_without_creating_another(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    first = await sync_email_account(db_session, org.id, acc.id)
    ref = first.mailer_mailbox_ref
    crud.record_email_account_verification(db_session, first, status=VerificationStatus.VERIFIED, error_code=None)

    second = await sync_email_account(db_session, org.id, acc.id)

    assert [(c.method, c.path) for c in mailer.calls] == [("POST", "/mailboxes"), ("PATCH", f"/mailboxes/{ref}")]
    assert mailer.calls[1].body == {
        "status": "active", "smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_use_tls": True,
        "smtp_username": "smtp-user", "smtp_password": SECRET,
    }   # activation always carries the FULL current configuration
    assert len(mailer.tenant_boxes(tenant)) == 1 and second.mailer_mailbox_ref == ref
    assert second.mailer_sync_state == MailerSyncState.SYNCED


async def test_concurrent_race_still_yields_one_mailbox(db_session, mailer, monkeypatch):
    """Two reconciles of one account run at once (lock-free SQLite: both reach the Mailer).
    The Mailer's (org, email) uniqueness is the backstop: one create wins, the other gets
    409, lists and adopts. (The PostgreSQL row-lock variant is in the PG test module.)"""
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    mailer.latency = 0.03
    s1, s2 = SessionLocal(), SessionLocal()
    try:
        await asyncio.gather(sync_email_account(s1, org.id, acc.id), sync_email_account(s2, org.id, acc.id))
    finally:
        s1.close(); s2.close()

    boxes = mailer.tenant_boxes(tenant)
    assert len(boxes) == 1
    assert sorted(c.status for c in mailer.calls_to("POST", "/mailboxes")) == [201, 409]
    db_session.refresh(acc)
    assert acc.mailer_mailbox_ref == boxes[0]["public_reference"]
    assert (acc.mailer_sync_state, acc.mailer_sync_error_code) == (MailerSyncState.SYNCED, None)


async def test_create_409_lists_and_adopts_an_existing_mailbox_then_applies_current_config(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    mailer.mailboxes["pre"] = dict(
        public_reference="pre", tenant=tenant, email_address=acc.email_address.lower(), status="disabled",
        smtp_host="old.example.com", smtp_port=25, smtp_use_tls=False, smtp_username="old", smtp_password="old-pw")

    out = await sync_email_account(db_session, org.id, acc.id)

    assert [(c.method, c.path) for c in mailer.calls] == [
        ("POST", "/mailboxes"), ("GET", "/mailboxes"), ("PATCH", "/mailboxes/pre")]
    assert mailer.calls[0].status == 409
    box = mailer.mailboxes["pre"]
    assert (box["status"], box["smtp_host"], box["smtp_port"], box["smtp_use_tls"], box["smtp_username"],
            box["smtp_password"]) == ("active", "smtp.example.com", 587, True, "smtp-user", SECRET)
    assert len(mailer.tenant_boxes(tenant)) == 1
    assert out.mailer_mailbox_ref == "pre" and out.mailer_sync_state == MailerSyncState.SYNCED


async def test_lost_create_response_is_recovered_by_adopting_on_retry(db_session, mailer, monkeypatch):
    """Mailer created the mailbox, LeadBoost never saw the answer (the crash window)."""
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    mailer.lose_response_next_post()

    first = await sync_email_account(db_session, org.id, acc.id)
    assert first.mailer_mailbox_ref is None
    assert (first.mailer_sync_state, first.mailer_sync_error_code) == (MailerSyncState.PENDING, MailerSyncErrorCode.TIMEOUT)
    assert len(mailer.tenant_boxes(tenant)) == 1            # it exists on the Mailer

    second = await sync_email_account(db_session, org.id, acc.id)
    assert len(mailer.tenant_boxes(tenant)) == 1            # still exactly one
    assert second.mailer_mailbox_ref == mailer.tenant_boxes(tenant)[0]["public_reference"]
    assert (second.mailer_sync_state, second.mailer_sync_error_code) == (MailerSyncState.SYNCED, None)


async def test_email_case_variants_never_share_one_mailbox_between_two_accounts(db_session, mailer, monkeypatch):
    org = _org(db_session)
    tenant = mailer.map_org(monkeypatch, org.id)
    a = _account(db_session, org.id, email="Dup@Example.com")
    b = _account(db_session, org.id, email="dup@example.com")
    first = await sync_email_account(db_session, org.id, a.id)
    second = await sync_email_account(db_session, org.id, b.id)

    assert len(mailer.tenant_boxes(tenant)) == 1
    assert first.mailer_mailbox_ref
    assert second.mailer_mailbox_ref is None             # unique(ref) refused the shared mailbox
    assert (second.mailer_sync_state, second.mailer_sync_error_code) == (MailerSyncState.PENDING, MailerSyncErrorCode.REJECTED)


# ------------------------------------------------------------ stale / foreign refs
async def test_stale_reference_is_forgotten_and_reprovisioned(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch, ref="deleted-on-mailer")
    out = await sync_email_account(db_session, org.id, acc.id)
    assert [(c.method, c.status) for c in mailer.calls] == [("PATCH", 404), ("POST", 201)]
    assert out.mailer_mailbox_ref not in (None, "deleted-on-mailer")
    assert len(mailer.tenant_boxes(tenant)) == 1 and out.mailer_sync_state == MailerSyncState.SYNCED


async def test_another_tenants_reference_cannot_be_used_or_modified(db_session, mailer, monkeypatch):
    org_a, tenant_a, _ = _setup(db_session, mailer, monkeypatch)
    org_b, tenant_b, acc_b = _setup(db_session, mailer, monkeypatch)
    mailer.mailboxes["a-box"] = dict(
        public_reference="a-box", tenant=tenant_a, email_address="a@a.test", status="active",
        smtp_host="a.example.com", smtp_port=587, smtp_use_tls=True, smtp_username="a", smtp_password="a-pw")
    before = dict(mailer.mailboxes["a-box"])
    acc_b.mailer_mailbox_ref = "a-box"          # corrupt / hostile data on ORG B's row
    db_session.commit()

    out = await sync_email_account(db_session, org_b.id, acc_b.id)

    assert mailer.mailboxes["a-box"] == before                      # tenant A's mailbox untouched
    assert mailer.calls[0].tenant == tenant_b and mailer.calls[0].status == 404
    assert out.mailer_mailbox_ref != "a-box"
    assert all(m["tenant"] == tenant_b for m in mailer.tenant_boxes(tenant_b))
    assert len(mailer.tenant_boxes(tenant_b)) == 1


async def test_cross_organization_sync_is_a_noop_that_never_contacts_the_mailer(db_session, mailer, monkeypatch):
    org_a, _, acc_a = _setup(db_session, mailer, monkeypatch)
    org_b, _, _ = _setup(db_session, mailer, monkeypatch)
    assert await sync_email_account(db_session, org_b.id, acc_a.id) is None
    assert mailer.calls == []
    db_session.refresh(acc_a)
    assert acc_a.mailer_mailbox_ref is None


async def test_each_organization_syncs_into_its_own_mailer_tenant(db_session, mailer, monkeypatch):
    org_a, tenant_a, acc_a = _setup(db_session, mailer, monkeypatch)
    org_b, tenant_b, acc_b = _setup(db_session, mailer, monkeypatch)
    await sync_email_account(db_session, org_a.id, acc_a.id)
    await sync_email_account(db_session, org_b.id, acc_b.id)
    assert tenant_a != tenant_b
    assert len(mailer.tenant_boxes(tenant_a)) == len(mailer.tenant_boxes(tenant_b)) == 1
    assert {c.api_key for c in mailer.calls} == {f"key-org-{org_a.id}", f"key-org-{org_b.id}"}
    assert all("organization" not in json.dumps(c.body) for c in mailer.calls)   # tenant never in a body


# --------------------------------------------------------------------- disable
async def test_disable_patches_status_only_and_keeps_the_reference(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    ref = (await sync_email_account(db_session, org.id, acc.id)).mailer_mailbox_ref
    mailer.calls.clear()

    crud.disable_email_account(db_session, acc)
    assert acc.mailer_sync_state == MailerSyncState.PENDING          # marked in the same commit
    out = await sync_email_account(db_session, org.id, acc.id)

    assert [(c.method, c.path, c.body) for c in mailer.calls] == [("PATCH", f"/mailboxes/{ref}", {"status": "disabled"})]
    assert mailer.mailboxes[ref]["status"] == "disabled" and mailer.active_boxes(tenant) == []
    assert out.mailer_mailbox_ref == ref and out.mailer_sync_state == MailerSyncState.SYNCED


async def test_unverified_account_that_was_never_provisioned_never_touches_the_mailer(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch, verified=False)
    out = await sync_email_account(db_session, org.id, acc.id)
    assert mailer.calls == [] and mailer.mailboxes == {}
    assert out.mailer_mailbox_ref is None and out.mailer_sync_state == MailerSyncState.SYNCED


async def test_losing_verification_disables_the_mailbox_and_sends_no_credential(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    ref = (await sync_email_account(db_session, org.id, acc.id)).mailer_mailbox_ref
    mailer.calls.clear()
    crud.record_email_account_verification(db_session, acc, status=VerificationStatus.FAILED, error_code="auth_failed")
    await sync_email_account(db_session, org.id, acc.id)
    assert mailer.mailboxes[ref]["status"] == "disabled"
    assert mailer.credential_bearing_calls() == []


# ------------------------------------------------------ fail-closed local cases
async def test_implicit_tls_is_never_provisioned_or_activated(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch, security_mode="tls")
    out = await sync_email_account(db_session, org.id, acc.id)
    assert mailer.calls == [] and mailer.mailboxes == {}              # nothing sent, nothing created
    assert out.mailer_mailbox_ref is None
    assert (out.mailer_sync_state, out.mailer_sync_error_code) == (
        MailerSyncState.PENDING, MailerSyncErrorCode.UNSUPPORTED_SECURITY_MODE)


async def test_existing_mailbox_is_disabled_when_the_account_becomes_unrepresentable(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    ref = (await sync_email_account(db_session, org.id, acc.id)).mailer_mailbox_ref
    mailer.calls.clear()
    acc.security_mode = "tls"                      # e.g. changed out-of-band while still VERIFIED
    db_session.commit()

    out = await sync_email_account(db_session, org.id, acc.id)

    assert mailer.mailboxes[ref]["status"] == "disabled"
    assert mailer.credential_bearing_calls() == []
    assert out.mailer_sync_error_code == MailerSyncErrorCode.UNSUPPORTED_SECURITY_MODE
    assert out.mailer_sync_state == MailerSyncState.PENDING


@pytest.mark.parametrize("over, code", [
    (dict(credential=None), MailerSyncErrorCode.NO_CREDENTIAL),
    (dict(encrypted_credential="not-a-fernet-token"), MailerSyncErrorCode.CREDENTIAL_UNREADABLE),
])
async def test_missing_or_unreadable_credential_fails_closed(db_session, mailer, monkeypatch, over, code):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch, **over)
    out = await sync_email_account(db_session, org.id, acc.id)
    assert mailer.calls == [] and out.mailer_sync_error_code == code and out.mailer_sync_state == MailerSyncState.PENDING


# ------------------------------------------------------- Mailer unavailable etc.
@pytest.mark.parametrize("inject, code", [
    (lambda m: m.fail_next(503), MailerSyncErrorCode.MAILER_UNAVAILABLE),
    (lambda m: m.fail_next(403), MailerSyncErrorCode.REJECTED),
    (lambda m: m.timeout_next(), MailerSyncErrorCode.TIMEOUT),
    (lambda m: m.connect_error_next(), MailerSyncErrorCode.UNREACHABLE),
])
async def test_mailer_failures_leave_a_safe_retryable_pending_state_then_recover(db_session, mailer, monkeypatch, inject, code):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    inject(mailer)

    failed = await sync_email_account(db_session, org.id, acc.id)
    assert (failed.mailer_sync_state, failed.mailer_sync_error_code) == (MailerSyncState.PENDING, code)
    assert failed.mailer_sync_error_code in ALL_CODES
    assert failed.verification_status == VerificationStatus.VERIFIED       # LeadBoost's own facts untouched

    healed = await sync_email_account(db_session, org.id, acc.id)           # the retry
    assert (healed.mailer_sync_state, healed.mailer_sync_error_code) == (MailerSyncState.SYNCED, None)
    assert len(mailer.tenant_boxes(tenant)) == 1


async def test_misconfiguration_is_reported_with_safe_codes_and_no_request(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)

    monkeypatch.delenv("MAILING_AGENT_BASE_URL")
    assert (await sync_email_account(db_session, org.id, acc.id)).mailer_sync_error_code == MailerSyncErrorCode.NOT_CONFIGURED

    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://mailer.example.com")    # remote plain http would carry a password
    assert (await sync_email_account(db_session, org.id, acc.id)).mailer_sync_error_code == MailerSyncErrorCode.INSECURE_TRANSPORT

    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailer.test")
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"999999": "someone-elses-key"}))
    assert (await sync_email_account(db_session, org.id, acc.id)).mailer_sync_error_code == MailerSyncErrorCode.ORG_KEY_NOT_CONFIGURED

    assert mailer.calls == []


async def test_unexpected_error_never_propagates_and_never_leaks(db_session, mailer, monkeypatch, caplog):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError(f"internal detail {SECRET}")

    monkeypatch.setattr(mailbox_client, "create_mailbox", boom)
    caplog.set_level(logging.DEBUG)
    out = await sync_email_account(db_session, org.id, acc.id)
    assert out.mailer_sync_state == MailerSyncState.PENDING
    assert "RuntimeError" in caplog.text and SECRET not in caplog.text


# ---------------------------------------------- re-read after each call, pass cap
async def test_a_change_made_while_the_mailer_call_is_in_flight_is_caught_and_converged(db_session, mailer, monkeypatch):
    """First call creates an ACTIVE mailbox; meanwhile LeadBoost disables the account.
    The re-read must notice and the next pass must disable it -- no stale ACTIVE mailbox."""
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    fired = []

    def disable_midflight(call):
        if not fired:
            fired.append(1)
            s = SessionLocal()
            a = s.get(EmailAccount, acc.id)
            a.is_active = False
            s.commit(); s.close()

    mailer.after_call = disable_midflight
    out = await sync_email_account(db_session, org.id, acc.id)

    assert [(c.method) for c in mailer.calls] == ["POST", "PATCH"]
    assert mailer.calls[1].body == {"status": "disabled"}
    assert mailer.active_boxes(tenant) == []
    assert out.mailer_sync_state == MailerSyncState.SYNCED and out.mailer_mailbox_ref


async def test_reconciliation_is_capped_at_three_passes_and_reports_sync_unstable(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)

    def keep_changing(call):
        s = SessionLocal()
        a = s.get(EmailAccount, acc.id)
        a.smtp_port += 1
        s.commit(); s.close()

    mailer.after_call = keep_changing
    out = await sync_email_account(db_session, org.id, acc.id)

    assert MAX_PASSES == 3 and len(mailer.calls) == 3
    assert (out.mailer_sync_state, out.mailer_sync_error_code) == (MailerSyncState.PENDING, MailerSyncErrorCode.SYNC_UNSTABLE)
    assert out.mailer_mailbox_ref                                  # the created mailbox is still tracked


# ------------------------------------------------------------------- secrecy
async def test_credential_travels_only_on_create_and_activation(db_session, mailer, monkeypatch):
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    await sync_email_account(db_session, org.id, acc.id)                       # create
    crud.record_email_account_verification(db_session, acc, status=VerificationStatus.VERIFIED, error_code=None)
    await sync_email_account(db_session, org.id, acc.id)                       # activate
    crud.disable_email_account(db_session, acc)
    await sync_email_account(db_session, org.id, acc.id)                       # disable
    mailer.timeout_next()
    await sync_email_account(db_session, org.id, acc.id)                       # (retry path, failing)
    mailer.keys  # noqa

    assert {(c.method, c.path.split("/")[1], c.body.get("status") if c.body else None)
            for c in mailer.credential_bearing_calls()} <= {("POST", "mailboxes", None), ("PATCH", "mailboxes", "active")}
    for c in mailer.calls:
        if c.body and c.body.get("status") == "disabled":
            assert set(c.body) == {"status"}
        if c.method == "GET":
            assert c.raw == b""


async def test_nothing_secret_is_logged_or_persisted_in_error_state(db_session, mailer, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    org, tenant, acc = _setup(db_session, mailer, monkeypatch)
    for inject in (lambda: mailer.fail_next(503), lambda: mailer.timeout_next(), lambda: None):
        inject()
        out = await sync_email_account(db_session, org.id, acc.id)
        assert out.mailer_sync_error_code is None or out.mailer_sync_error_code in ALL_CODES
        assert SECRET not in (out.mailer_sync_error_code or "")
    assert SECRET not in caplog.text and f"key-org-{org.id}" not in caplog.text
    # the stored ciphertext is LeadBoost's own encrypted copy, never the plaintext
    db_session.refresh(acc)
    assert SECRET not in (acc.encrypted_credential or "")
