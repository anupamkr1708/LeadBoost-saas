"""
L1: reconcile a LeadBoost EmailAccount with its Mailer-owned Mailbox.

LeadBoost is the control plane, the Mailer owns the send-time credential. The
two are separate deployments, so there is NO transaction spanning "LeadBoost
row changed" and "Mailer mailbox changed" and this module does not pretend
otherwise. Instead it converges, explicitly and durably:

  * every LeadBoost change that matters sets `mailer_sync_state='pending'` in
    the same commit as the change (core/infrastructure/database/crud.py);
  * `sync_email_account` then drives the Mailer toward the DESIRED state and
    only a successful, still-current reconcile sets 'synced';
  * any failure leaves 'pending' + a safe `mailer_sync_error_code`, retryable
    via POST .../mailer-sync or the next verify. No queue, no outbox.

DESIRED STATE (a pure function of the LeadBoost row):
    mailbox ACTIVE   iff  is_active AND verification_status == VERIFIED
    otherwise        mailbox DISABLED (or never created)

Operations (all organization-scoped; the Mailer maps the per-org API key to its
tenant -- see core/infrastructure/mailing_agent/client.py):
    wants ACTIVE, no ref  -> POST /mailboxes (credential, once). On 409 (a
                             lost response, a crash before the ref was saved,
                             or a concurrent caller) -> GET /mailboxes, adopt
                             by e-mail, then activate. (organization, email)
                             is UNIQUE on both sides, so this converges on
                             exactly one mailbox.
    wants ACTIVE, ref     -> PATCH {status:active + FULL current connection
                             config + credential} -- one atomic update.
    wants DISABLED, ref   -> PATCH {status:disabled}. No credential.
    wants DISABLED, none  -> nothing to do; the Mailer is not contacted.
    ref gone (Mailer 404) -> forget it and re-provision / nothing to disable.
                             A ref that belongs to another Mailer tenant is
                             indistinguishable from a missing one, so it can
                             never be used across tenants.

An UNVERIFIED credential is never sent: rotation disables the mailbox first,
and the new config + credential go over only in the activation that follows a
successful re-verification.

FAIL CLOSED, never degrade: `security_mode='tls'` (implicit TLS) cannot be
represented by the Mailer's STARTTLS-only sender, so such an account is never
provisioned or activated (`unsupported_security_mode`); a missing or unreadable
credential likewise. If a mailbox already exists in those cases it is disabled.

CONCURRENCY: the account row is locked (`SELECT ... FOR UPDATE` on PostgreSQL)
for the whole reconcile, so a concurrent LeadBoost change waits instead of
racing the in-flight Mailer call, and two reconciles of one account serialize.
After EVERY Mailer step the row is re-read; if it changed meanwhile (the only
way on SQLite, which has no row locks) another pass runs, at most MAX_PASSES,
then `sync_unstable`. The lock is held across at most a few Mailer calls, each
bounded by MAILING_AGENT_TIMEOUT_SECONDS.

KNOWN INCONSISTENCY WINDOW (not hidden, not solved with a distributed
transaction): if LeadBoost disables an account but the Mailer disable call
fails, the Mailer mailbox can stay ACTIVE while the row is 'pending'. LeadBoost
is the only caller that asks the Mailer to send, and dispatch requires
`is_active AND VERIFIED AND mailer_sync_state='synced'`, so NO NEW dispatch is
possible during the window. Requests the Mailer had already accepted could
still be sent through the not-yet-disabled mailbox until the retry converges.
A mailbox created whose ref LeadBoost then lost (crash) is adopted by e-mail on
the next reconcile.

SECURITY: the credential is decrypted only immediately before create/activate
and deleted right after; it, request bodies and Mailer response bodies are never
logged or stored; only the closed MailerSyncErrorCode vocabulary is persisted.
"""

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.domain.models.email_account import (
    EmailAccount,
    MailerSyncErrorCode,
    MailerSyncState,
    SecurityMode,
    VerificationStatus,
)
from core.infrastructure.database import crud
from core.infrastructure.logging import get_logger
from core.infrastructure.mailing_agent import mailbox_client
from core.infrastructure.mailing_agent.client import DispatchErrorCode, MailerResponse
from core.infrastructure.security.credential_crypto import CredentialEncryptionError, decrypt_credential

logger = get_logger(__name__)

MAX_PASSES = 3

_TRANSPORT_CODES = {
    DispatchErrorCode.NOT_CONFIGURED: MailerSyncErrorCode.NOT_CONFIGURED,
    DispatchErrorCode.AUTH_NOT_CONFIGURED: MailerSyncErrorCode.ORG_KEY_NOT_CONFIGURED,
    DispatchErrorCode.INSECURE_TRANSPORT: MailerSyncErrorCode.INSECURE_TRANSPORT,
    DispatchErrorCode.TIMEOUT: MailerSyncErrorCode.TIMEOUT,
    DispatchErrorCode.UNREACHABLE: MailerSyncErrorCode.UNREACHABLE,
}


@dataclass(frozen=True)
class _Outcome:
    ref: Optional[str]
    error_code: Optional[str] = None


def sync_fingerprint(account: EmailAccount) -> Tuple[Any, ...]:
    """Everything the desired Mailer state is derived from. Excludes the ref and
    the sync bookkeeping columns, which this module itself writes."""
    return (
        account.is_active,
        account.verification_status,
        account.email_address,
        account.smtp_host,
        account.smtp_port,
        account.security_mode,
        account.username,
        account.encrypted_credential,
    )


def _wants_active(account: EmailAccount) -> bool:
    return bool(account.is_active) and account.verification_status == VerificationStatus.VERIFIED


def _failure_code(resp: MailerResponse) -> Optional[str]:
    """None for a usable 2xx; otherwise the safe sync code. 404 (stale ref) and
    409 (duplicate on create) are interpreted by the callers BEFORE this."""
    if resp.error_code:
        return _TRANSPORT_CODES.get(resp.error_code, MailerSyncErrorCode.UNREACHABLE)
    if resp.status_code is not None and 200 <= resp.status_code < 300:
        return None
    if resp.status_code is not None and resp.status_code >= 500:
        return MailerSyncErrorCode.MAILER_UNAVAILABLE
    return MailerSyncErrorCode.REJECTED


def _reference(data: Any) -> Optional[str]:
    ref = data.get("public_reference") if isinstance(data, dict) else None
    return ref if isinstance(ref, str) and ref else None


async def _ensure_disabled(organization_id: int, ref: Optional[str]) -> _Outcome:
    if ref is None:
        return _Outcome(None)  # never provisioned -> nothing exists to disable; Mailer not contacted
    resp = await mailbox_client.disable_mailbox(organization_id, ref)
    if resp.status_code == 404:
        return _Outcome(None)  # gone (or not ours): nothing of ours is sendable
    code = _failure_code(resp)
    return _Outcome(ref, code)


async def _adopt_by_email(organization_id: int, email: str) -> Tuple[Optional[str], Optional[str]]:
    resp = await mailbox_client.list_mailboxes(organization_id)
    code = _failure_code(resp)
    if code:
        return None, code
    if not isinstance(resp.data, list):
        return None, MailerSyncErrorCode.INVALID_RESPONSE
    for item in resp.data:
        if isinstance(item, dict) and str(item.get("email_address", "")).strip().lower() == email:
            ref = _reference(item)
            return (ref, None) if ref else (None, MailerSyncErrorCode.INVALID_RESPONSE)
    return None, MailerSyncErrorCode.REJECTED  # a 409 we cannot explain: do not guess


async def _provision_active(
    account: EmailAccount, ref: Optional[str], *, secret: str, allow_reprovision: bool = True
) -> _Outcome:
    org = account.organization_id
    email = account.email_address.strip().lower()
    conn = dict(
        smtp_host=account.smtp_host,
        smtp_port=account.smtp_port,
        smtp_username=account.username or account.email_address,
        smtp_password=secret,
    )

    if ref is None:
        resp = await mailbox_client.create_mailbox(org, email_address=email, **conn)
        if resp.status_code == 409:
            ref, code = await _adopt_by_email(org, email)
            if code:
                return _Outcome(None, code)
            # adopted: fall through to activation with the FULL current config
        else:
            code = _failure_code(resp)
            if code:
                return _Outcome(None, code)
            created = _reference(resp.data)
            if created is None:
                # The mailbox may exist; the next reconcile adopts it by e-mail.
                return _Outcome(None, MailerSyncErrorCode.INVALID_RESPONSE)
            return _Outcome(created)  # created ACTIVE with the current config

    resp = await mailbox_client.activate_mailbox(org, ref, **conn)
    if resp.status_code == 404:
        if allow_reprovision:
            return await _provision_active(account, None, secret=secret, allow_reprovision=False)
        return _Outcome(None, MailerSyncErrorCode.REJECTED)
    return _Outcome(ref, _failure_code(resp))


async def _reconcile_once(account: EmailAccount, ref: Optional[str]) -> _Outcome:
    org = account.organization_id

    if not _wants_active(account):
        return await _ensure_disabled(org, ref)

    # Wants ACTIVE but cannot be represented / sent: fail closed AND make sure
    # nothing previously provisioned stays sendable.
    local_failure: Optional[str] = None
    if account.security_mode != SecurityMode.STARTTLS:
        local_failure = MailerSyncErrorCode.UNSUPPORTED_SECURITY_MODE
    elif not account.encrypted_credential:
        local_failure = MailerSyncErrorCode.NO_CREDENTIAL
    if local_failure is None:
        try:
            secret = decrypt_credential(account.encrypted_credential)
        except CredentialEncryptionError:
            local_failure = MailerSyncErrorCode.CREDENTIAL_UNREADABLE
    if local_failure is not None:
        disabled = await _ensure_disabled(org, ref)
        return _Outcome(disabled.ref, disabled.error_code or local_failure)

    try:
        return await _provision_active(account, ref, secret=secret)
    finally:
        del secret  # never held longer than the Mailer call


def _persist(db: Session, account: EmailAccount, *, ref: Optional[str], state: str, error: Optional[str]) -> EmailAccount:
    try:
        return crud.record_mailer_sync_result(db, account, mailbox_ref=ref, state=state, error_code=error)
    except IntegrityError:
        # The ref is already linked to another LeadBoost account (e.g. two accounts whose
        # e-mail differs only by case map to one Mailer mailbox). Never share a mailbox.
        db.rollback()
        db.refresh(account)
        return crud.record_mailer_sync_result(
            db, account, mailbox_ref=None, state=MailerSyncState.PENDING, error_code=MailerSyncErrorCode.REJECTED
        )


async def _sync(db: Session, organization_id: int, account_id: int) -> Optional[EmailAccount]:
    account = crud.lock_email_account(db, organization_id, account_id)  # org-scoped + row lock
    if account is None:
        return None

    ref = account.mailer_mailbox_ref
    for _ in range(MAX_PASSES):
        before = sync_fingerprint(account)
        outcome = await _reconcile_once(account, ref)
        ref = outcome.ref

        db.refresh(account)  # re-read LeadBoost's state AFTER the Mailer call(s)
        changed = sync_fingerprint(account) != before

        if outcome.error_code:
            logger.warning(
                "Mailer mailbox sync did not converge",
                extra={"organization_id": organization_id, "email_account_id": account_id,
                       "error_code": outcome.error_code},
            )
            return _persist(db, account, ref=ref, state=MailerSyncState.PENDING, error=outcome.error_code)
        if not changed:
            return _persist(db, account, ref=ref, state=MailerSyncState.SYNCED, error=None)

    return _persist(db, account, ref=ref, state=MailerSyncState.PENDING, error=MailerSyncErrorCode.SYNC_UNSTABLE)


async def sync_email_account(db: Session, organization_id: int, account_id: int) -> Optional[EmailAccount]:
    """Reconcile one account. Returns the refreshed account (None if it does not
    exist in this organization). Never raises for a Mailer/transport failure --
    those are recorded as 'pending' + a safe code. An unexpected error is logged
    by type only, the row is left 'pending' (it already was), and the caller's
    primary operation (which committed before this ran) is never failed by it."""
    try:
        return await _sync(db, organization_id, account_id)
    except Exception as exc:  # noqa: BLE001 - boundary: sync must not fail the committed primary operation
        db.rollback()
        logger.error(
            f"Mailer mailbox sync failed unexpectedly ({type(exc).__name__})",
            extra={"organization_id": organization_id, "email_account_id": account_id},
        )
        return crud.get_email_account(db, organization_id, account_id)
