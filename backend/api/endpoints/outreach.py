"""
Outreach endpoints (P1.4).

Two resources:
  - /outreach-actions            (this organization's OutreachAction records)
  - /organizations/{org_id}/outreach-policy   (this organization's automatic-sending policy)

Follows the same division of responsibility as api/endpoints/email_accounts.py:
this file authenticates, translates OutreachError into HTTP responses, and
does nothing else -- every actual rule (tenancy beyond "is this my org",
state transitions, idempotency, policy evaluation, message snapshotting,
Mailing Agent dispatch) lives in application/services/outreach_service.py.

`/outreach-actions` deliberately does NOT put organization_id in the path
(unlike /organizations/{org_id}/email-accounts) -- it follows the same
flat, current_user-derived-organization convention already used by
POST /leads/single and GET/PATCH /leads/{id} (api/endpoints/leads.py),
since lead_id/email_account_id (both already organization-scoped) are
supplied in the request body/path instead.
"""

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.domain.models.user import User
from core.domain.schemas.outreach_action import OutreachAction as OutreachActionSchema, OutreachActionCreate
from core.domain.schemas.outreach_policy import OutreachPolicy as OutreachPolicySchema, OutreachPolicyUpdate
from core.infrastructure.auth.security import get_current_user
from core.infrastructure.database import get_db
from core.infrastructure.database.crud import (
    get_or_create_outreach_policy,
    get_outreach_action,
    list_outreach_actions,
    update_outreach_policy,
)
from core.infrastructure.logging import get_logger
from application.services import outreach_service
from application.services.outreach_service import OutreachError, OutreachErrorCode

logger = get_logger(__name__)

router = APIRouter()
policy_router = APIRouter(prefix="/organizations")

# OutreachError -> HTTP status. A code not listed here (there isn't one
# today) would fall through to 400, matching FastAPI's own default for
# an unhandled ValueError-shaped client error -- see the except clause
# at each call site below.
_ERROR_STATUS = {
    OutreachErrorCode.LEAD_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    OutreachErrorCode.SENDER_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    OutreachErrorCode.ACTION_NOT_FOUND: status.HTTP_404_NOT_FOUND,
    OutreachErrorCode.SENDER_NOT_VERIFIED: status.HTTP_422_UNPROCESSABLE_ENTITY,
    OutreachErrorCode.SENDER_DISABLED: status.HTTP_422_UNPROCESSABLE_ENTITY,
    OutreachErrorCode.MESSAGE_NOT_READY: status.HTTP_422_UNPROCESSABLE_ENTITY,
    OutreachErrorCode.RECIPIENT_INVALID: status.HTTP_422_UNPROCESSABLE_ENTITY,
    OutreachErrorCode.AUTOMATIC_SENDING_DISABLED: status.HTTP_422_UNPROCESSABLE_ENTITY,
    OutreachErrorCode.INVALID_STATE_TRANSITION: status.HTTP_409_CONFLICT,
    OutreachErrorCode.IDEMPOTENCY_KEY_REUSED: status.HTTP_409_CONFLICT,
}


def _raise_for(exc: OutreachError) -> None:
    raise HTTPException(
        status_code=_ERROR_STATUS.get(exc.error_code, status.HTTP_400_BAD_REQUEST),
        detail={"error_code": exc.error_code, "message": exc.message},
    )


@router.post("/outreach-actions", response_model=OutreachActionSchema, status_code=status.HTTP_201_CREATED)
async def create_outreach_action_endpoint(
    payload: OutreachActionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Authorizes/prepares an outreach action for a lead using the
    lead's already-generated message and a verified sender mailbox. See
    application/services/outreach_service.py::create_action.

    Idempotent: retrying the same request (same lead, sender and message
    content, or an explicit client-supplied `idempotency_key`) returns
    the existing action instead of creating a duplicate -- still 201,
    since from the caller's point of view the resource now exists either
    way; check `created_at` if you need to distinguish a fresh create
    from a replay.
    """
    try:
        action, _created = outreach_service.create_action(
            db,
            organization_id=current_user.organization_id,
            lead_id=payload.lead_id,
            email_account_id=payload.email_account_id,
            mode=payload.mode.value,
            idempotency_key=payload.idempotency_key,
        )
        return action
    except OutreachError as exc:
        _raise_for(exc)


@router.get("/outreach-actions", response_model=List[OutreachActionSchema])
async def list_outreach_actions_endpoint(
    lead_id: Optional[int] = None,
    state: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    return list_outreach_actions(db, current_user.organization_id, lead_id=lead_id, state=state)


@router.get("/outreach-actions/{action_id}", response_model=OutreachActionSchema)
async def read_outreach_action_endpoint(
    action_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    action = get_outreach_action(db, current_user.organization_id, action_id)
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Outreach action not found")
    return action


@router.post("/outreach-actions/{action_id}/approve", response_model=OutreachActionSchema)
async def approve_outreach_action_endpoint(
    action_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Manual approval: PENDING_REVIEW -> APPROVED. Re-validates sender
    eligibility at approval time (see outreach_service.approve_action)."""
    try:
        return outreach_service.approve_action(
            db,
            organization_id=current_user.organization_id,
            action_id=action_id,
            approved_by_user_id=current_user.id,
        )
    except OutreachError as exc:
        _raise_for(exc)


@router.post("/outreach-actions/{action_id}/cancel", response_model=OutreachActionSchema)
async def cancel_outreach_action_endpoint(
    action_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """PENDING_REVIEW, APPROVED, or DISPATCH_FAILED -> CANCELLED. Not
    valid once SUBMITTED, and not valid while a dispatch is actually in
    flight (DISPATCHING) -- see OutreachState.CANCELLABLE_FROM."""
    try:
        return outreach_service.cancel_action(
            db, organization_id=current_user.organization_id, action_id=action_id, reason="Cancelled by user."
        )
    except OutreachError as exc:
        _raise_for(exc)


@router.post("/outreach-actions/{action_id}/dispatch", response_model=OutreachActionSchema)
async def dispatch_outreach_action_endpoint(
    action_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Hands an APPROVED (or previously DISPATCH_FAILED, for retry)
    action to the Mailing Agent. Never sends real mail in this call
    itself -- it only makes the one HTTP request described in
    core/infrastructure/mailing_agent/CONTRACT.md. Concurrent calls for
    the same action are safe: exactly one proceeds (see
    outreach_service.dispatch_action's atomic DISPATCHING claim); the
    other receives 409 invalid_state_transition immediately, without a
    second Mailing Agent call ever being made. A non-2xx/unreachable/
    timeout outcome moves the action to DISPATCH_FAILED with a safe
    `last_dispatch_error` code rather than raising -- see that field for
    the reason; retry with this same endpoint once the underlying issue
    (e.g. Mailing Agent not yet deployed) is resolved."""
    try:
        return await outreach_service.dispatch_action(
            db, organization_id=current_user.organization_id, action_id=action_id
        )
    except OutreachError as exc:
        _raise_for(exc)


# --- Organization outreach policy (mirrors qualification-settings in
# api/endpoints/organizations.py) ---


def _require_own_organization(current_user: User, org_id: int) -> None:
    if current_user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this organization",
        )


@policy_router.get("/{org_id}/outreach-policy", response_model=OutreachPolicySchema)
async def read_outreach_policy(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_own_organization(current_user, org_id)
    return get_or_create_outreach_policy(db, org_id)


@policy_router.put("/{org_id}/outreach-policy", response_model=OutreachPolicySchema)
async def update_outreach_policy_endpoint(
    org_id: int,
    payload: OutreachPolicyUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    _require_own_organization(current_user, org_id)

    # Validate the *merged* sending window (existing + this request) --
    # see OutreachPolicyUpdate's docstring for why this can't be a
    # schema-level check.
    current = get_or_create_outreach_policy(db, org_id)
    data = payload.dict(exclude_unset=True)
    start = data.get("sending_window_start_hour_utc", current.sending_window_start_hour_utc)
    end = data.get("sending_window_end_hour_utc", current.sending_window_end_hour_utc)
    if (start is None) != (end is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sending_window_start_hour_utc and sending_window_end_hour_utc must be set together.",
        )
    if start is not None and start == end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="sending_window_start_hour_utc and sending_window_end_hour_utc must differ.",
        )

    return update_outreach_policy(db, org_id, payload)
