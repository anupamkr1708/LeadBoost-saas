"""
Email Account endpoints (P1.3).

Organization-scoped sender mailboxes -- secure connection metadata +
verification. Does not send email; see core/infrastructure/email/
smtp_verifier.py's docstring and this file's `verify_email_account`.

Follows the exact router/crud/schema convention and organization-ownership
check already used by every other organization-scoped resource in this
codebase (see api/endpoints/organizations.py's qualification-settings
endpoints, added in P1.2) -- `current_user.organization_id != org_id`
raises 403 before any database access, and every CRUD lookup additionally
filters `WHERE organization_id = ...` in the SQL itself (see
core/infrastructure/database/crud.py's get_email_account), so tenancy is
enforced at both the endpoint and the query layer, not just one of them.

SECURITY: every response in this file is `response_model=EmailAccountSchema`
(core/domain/schemas/email_account.py), which has no credential-shaped
field at all -- there is no `.exclude()`/masking logic anywhere below
because there is nothing sensitive on the type FastAPI is declared to
serialize. The one place plaintext credential material exists in this
file's process is `verify_email_account`'s local `plaintext` variable,
scoped as narrowly as possible (decrypted immediately before the
verification call, never assigned anywhere else, goes out of scope the
moment the function returns).
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, List
import time

from application.services.mailer_mailbox_sync import sync_email_account, sync_fingerprint
from core.infrastructure.database import get_db
from core.infrastructure.auth.security import get_current_user
from core.domain.models.user import User
from core.domain.models.email_account import VerificationStatus
from core.domain.schemas.email_account import (
    EmailAccount as EmailAccountSchema,
    EmailAccountCreate,
    EmailAccountUpdate,
    EmailAccountVerifyResult,
)
from core.infrastructure.database.crud import (
    create_email_account,
    get_email_account,
    get_email_accounts_by_organization,
    update_email_account,
    disable_email_account,
    record_email_account_verification,
)
from core.infrastructure.security.credential_crypto import decrypt_credential, CredentialEncryptionError
from core.infrastructure.email.smtp_verifier import verify_smtp_mailbox
from core.infrastructure.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/organizations")


def _require_own_organization(current_user: User, org_id: int) -> None:
    """The exact same ownership check as every other organization-scoped
    endpoint in this codebase (organizations.py's qualification-settings
    endpoints, added in P1.2). Factored out here only because this file
    has six call sites for it, not because the rule itself is new."""
    if current_user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this organization",
        )


def _get_owned_account_or_404(db: Session, org_id: int, account_id: int):
    """Organization-scoped lookup + 404. Never distinguishes "doesn't
    exist" from "exists but belongs to another organization" -- both
    cases return 404, since get_email_account's query itself is
    WHERE organization_id = org_id (see crud.py), so a cross-tenant id
    simply never matches."""
    account = get_email_account(db, org_id, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email account not found")
    return account


async def _verify_result(db: Session, org_id: int, updated) -> EmailAccountVerifyResult:
    """L1: every verification outcome can change whether the Mailer mailbox may be
    ACTIVE (verified -> provision/activate; failed/disabled -> disable), so
    reconcile it now and report the integrated-sending readiness alongside the
    verification result. The reconcile never raises and never alters the
    verification outcome that was just recorded."""
    synced = await sync_email_account(db, org_id, updated.id) or updated
    return EmailAccountVerifyResult(
        verification_status=updated.verification_status,
        verification_error_code=updated.verification_error_code,
        verified_at=updated.verified_at,
        mailer_sync_state=synced.mailer_sync_state,
        mailer_sync_error_code=synced.mailer_sync_error_code,
    )


@router.get("/{org_id}/email-accounts", response_model=List[EmailAccountSchema])
async def list_email_accounts(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """List this organization's sender mailboxes. Response never includes
    any credential material -- see this module's docstring."""
    _require_own_organization(current_user, org_id)
    return get_email_accounts_by_organization(db, org_id)


@router.post("/{org_id}/email-accounts", response_model=EmailAccountSchema, status_code=status.HTTP_201_CREATED)
async def create_email_account_endpoint(
    org_id: int,
    payload: EmailAccountCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Creates a mailbox. `payload.credential`, if supplied, is plaintext
    on the wire (this is the one legitimate place it should ever appear)
    and is encrypted before the row is created -- see
    crud.create_email_account. Never echoed back in the response."""
    _require_own_organization(current_user, org_id)
    try:
        return create_email_account(db, org_id, payload)
    except CredentialEncryptionError as exc:
        # Missing/malformed EMAIL_CREDENTIAL_ENCRYPTION_KEY -- a server
        # configuration problem, not a client error, but still must not
        # leak key material (CredentialEncryptionError's message is
        # written to be safe to surface -- see credential_crypto.py).
        logger.error(f"Email account creation failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email account credentials cannot be processed right now. Please try again shortly.",
        )


@router.get("/{org_id}/email-accounts/{account_id}", response_model=EmailAccountSchema)
async def read_email_account(
    org_id: int,
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_own_organization(current_user, org_id)
    return _get_owned_account_or_404(db, org_id, account_id)


@router.patch("/{org_id}/email-accounts/{account_id}", response_model=EmailAccountSchema)
async def update_email_account_endpoint(
    org_id: int,
    account_id: int,
    payload: EmailAccountUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Partial update. Any field omitted from the request body is left
    untouched -- in particular, omitting `credential` (the common case:
    editing display_name, disabling, etc.) preserves the existing
    encrypted credential unchanged; see crud.update_email_account. Only
    fields that actually change AND are connection-relevant
    (host/port/security_mode/username/credential_type/credential) reset
    verification_status back to unverified."""
    _require_own_organization(current_user, org_id)
    account = _get_owned_account_or_404(db, org_id, account_id)
    before = sync_fingerprint(account)
    try:
        updated = update_email_account(db, account, payload)
    except CredentialEncryptionError as exc:
        logger.error(f"Email account update failed: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email account credentials cannot be processed right now. Please try again shortly.",
        )
    # L1: a connection-sensitive change (which update_email_account has already
    # turned into UNVERIFIED + mailer_sync_state='pending' in one commit) or an
    # enable/disable toggle must reach the Mailer. A cosmetic edit (display_name)
    # leaves the fingerprint unchanged and does not touch the Mailer.
    if sync_fingerprint(updated) != before:
        return await sync_email_account(db, org_id, account_id) or updated
    return updated


@router.delete("/{org_id}/email-accounts/{account_id}", response_model=EmailAccountSchema)
async def delete_email_account_endpoint(
    org_id: int,
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Soft delete (is_active=False) -- see crud.disable_email_account's
    docstring for why a hard delete isn't offered here. Returns the
    now-disabled account rather than 204, so the frontend can confirm the
    new state without a follow-up GET."""
    _require_own_organization(current_user, org_id)
    account = _get_owned_account_or_404(db, org_id, account_id)
    disabled = disable_email_account(db, account)
    # L1: the Mailer mailbox must stop being sendable too. If the Mailer is
    # unreachable the row stays 'pending' (retryable) and the dispatch gate
    # already refuses new dispatches for an inactive account.
    return await sync_email_account(db, org_id, account_id) or disabled


@router.post("/{org_id}/email-accounts/{account_id}/mailer-sync", response_model=EmailAccountSchema)
async def sync_email_account_with_mailer(
    org_id: int,
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """L1: explicit, idempotent retry of the Mailer mailbox reconciliation (the
    recovery path for a 'pending' account after a Mailer outage). Takes no body;
    the desired state is derived from this account's current LeadBoost state and
    the Mailer is told nothing else. Organization-scoped like every other route
    here: another organization's account is a 404."""
    _require_own_organization(current_user, org_id)
    _get_owned_account_or_404(db, org_id, account_id)
    synced = await sync_email_account(db, org_id, account_id)
    if synced is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email account not found")
    return synced


@router.post("/{org_id}/email-accounts/{account_id}/verify", response_model=EmailAccountVerifyResult)
async def verify_email_account(
    org_id: int,
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Attempts to authenticate to this mailbox's configured SMTP server.
    Never sends an email (see smtp_verifier.py). One bounded attempt, no
    retries.

    One of exactly two places a credential is ever decrypted (L1): here, for
    LeadBoost's own SMTP verification, and in application/services/
    mailer_mailbox_sync.py, immediately before a Mailer mailbox create/activate.
    Ordinary outreach dispatch decrypts nothing. `plaintext` lives only for the
    few lines between decrypt and the verification call; it is never logged, never included in the
    response (`EmailAccountVerifyResult` has no field for it), and goes
    out of scope as soon as this function returns.
    """
    _require_own_organization(current_user, org_id)
    account = _get_owned_account_or_404(db, org_id, account_id)

    if not account.is_active:
        # Deterministic rejection, no network call -- see
        # core/domain/models/email_account.py::VerificationStatus.DISABLED
        # and the P1.3 brief's "disabled account -> no verification
        # attempt or deterministic rejection".
        updated = record_email_account_verification(
            db, account, status=VerificationStatus.DISABLED, error_code=None
        )
        return await _verify_result(db, org_id, updated)

    if not account.encrypted_credential:
        updated = record_email_account_verification(
            db, account, status=VerificationStatus.FAILED, error_code="no_credential_configured"
        )
        return await _verify_result(db, org_id, updated)

    try:
        plaintext = decrypt_credential(account.encrypted_credential)
    except CredentialEncryptionError as exc:
        logger.error(f"Email account verification could not decrypt credential: {exc}")
        updated = record_email_account_verification(
            db, account, status=VerificationStatus.FAILED, error_code="credential_unreadable"
        )
        return await _verify_result(db, org_id, updated)

    started = time.monotonic()
    try:
        result = await verify_smtp_mailbox(
            host=account.smtp_host,
            port=account.smtp_port,
            security_mode=account.security_mode,
            username=account.username or account.email_address,
            password=plaintext,
        )
    finally:
        # Belt-and-suspenders: drop the local reference as soon as the
        # verification call returns, rather than leaving it live for the
        # rest of this function's scope.
        del plaintext
    duration_ms = round((time.monotonic() - started) * 1000)

    logger.info(
        "Email account verification attempt",
        extra={
            "organization_id": org_id,
            "email_account_id": account_id,
            "provider": account.provider,
            "result": result.status,
            "error_code": result.error_code,
            "duration_ms": duration_ms,
        },
    )

    updated = record_email_account_verification(db, account, status=result.status, error_code=result.error_code)
    return await _verify_result(db, org_id, updated)
