"""
Outreach service (P1.4).

The single place that owns the OutreachAction lifecycle: message
snapshotting, idempotency, MANUAL/AUTOMATIC authorization semantics,
state transitions, and handing an APPROVED action off to the separate
Mailing Agent. api/endpoints/outreach.py is a thin HTTP wrapper around
this module -- it does not contain any of this logic itself, matching
the existing api/endpoints/email_accounts.py <-> crud.py division: the
API layer authenticates and translates OutreachError into HTTP responses,
this module decides what is and isn't allowed.

STATE MACHINE (see core/domain/models/outreach_action.py::OutreachState):

    PENDING_REVIEW --approve_action--> APPROVED
    PENDING_REVIEW --cancel_action-->  CANCELLED
    APPROVED       --cancel_action-->  CANCELLED
    APPROVED        --dispatch_action(claim)--> DISPATCHING --success--> SUBMITTED
                                                            \\--failure--> DISPATCH_FAILED
    DISPATCH_FAILED --cancel_action-->  CANCELLED
    DISPATCH_FAILED --dispatch_action(claim, retry)--> DISPATCHING --success--> SUBMITTED
                                                                   \\--failure--> DISPATCH_FAILED

SUBMITTED and CANCELLED are terminal. DISPATCHING is a transient,
machine-only state -- see OutreachState's docstring for why it exists
and why it is deliberately not cancellable.

IDEMPOTENCY (LeadBoost side): `derive_idempotency_key` produces a
deterministic key -- a canonical JSON serialization of (lead_id,
email_account_id, mode, recipient_email, recipient_name, subject, body),
sorted-key and compact so the same logical request always serializes
identically, hashed with SHA-256 -- when the caller doesn't supply one.
Two requests that would authorize the exact same message to the exact
same recipient from the exact same sender, in the exact same mode,
collapse onto the same OutreachAction row -- see create_action's
"replay" return, and the uq_outreach_actions_org_idempotency_key
database constraint, which is the actual race-proof guarantee (this
function's own pre-check is only an optimization to avoid an
unnecessary INSERT attempt; the constraint is what makes concurrent
duplicate *creation* requests safe). The same key is forwarded to the
Mailing Agent on every dispatch attempt for this action -- see
core/infrastructure/mailing_agent/CONTRACT.md for why that cross-service
half of the guarantee is required too (this module's own claim below
only protects against concurrent dispatch from *this* process; it
cannot protect against a legitimate retry after a network-level
ambiguity talking to the Mailing Agent).

DISPATCH CLAIMING (LeadBoost side -- the concurrency fix): dispatch_action
claims an APPROVED or DISPATCH_FAILED row with
crud.claim_outreach_action_for_dispatch -- a single UPDATE ... SET
state = 'dispatching' WHERE state IN ('approved', 'dispatch_failed')
that reads and changes state in the same database statement. This is
NOT a separate "read state, decide, then write state" sequence -- the
WHERE clause and the SET clause are the same atomic operation, so if two
requests race to dispatch the same action, exactly one UPDATE affects a
row (claimed = 1) and the other affects none (claimed = 0) and must stop
immediately without ever contacting the Mailing Agent. This is the same
"atomic claim via conditional UPDATE, not a lock table" idea already
used by Job's claim mechanism (application/execution/job_repository.py),
sized down to what a single row transitioning once actually needs -- a
new lock table or distributed lock would be over-engineering here.

APPROVAL/CANCELLATION CLAIMING (the same fix, applied to the other two
mutating transitions): approve_action and cancel_action use the
identical atomic-UPDATE pattern via crud.claim_outreach_action_for_approval
and crud.claim_outreach_action_for_cancellation, for the same reason --
a plain "read state in Python, check it, mutate the object, commit"
sequence would let a concurrent approve and cancel (or two concurrent
cancels/approvals) on the same row both believe they were acting on its
pre-race state, with the second write silently overwriting the first.
Because DISPATCHING is not in OutreachState.CANCELLABLE_FROM, and all
three claims (dispatch, approve, cancel) race for the same single
`state` column via the same mechanism rather than via separate
read-then-write steps, a cancel request can never land on a row a
dispatch attempt has already (atomically) claimed -- there is no window
between "dispatch read state" and "dispatch wrote state" for a cancel to
land in, because dispatch's read and write are the same statement.

AUTOMATIC-MODE QUOTA SERIALIZATION (the third concurrency fix): the
AUTOMATIC-mode branch of create_action re-fetches the organization's
outreach policy row under crud.lock_outreach_policy -- a write-lock held
until this function's commit -- before counting existing actions and
deciding whether to auto-approve. Without this, two concurrent
AUTOMATIC-mode create requests for the same organization could both
count the same not-yet-incremented total and both pass a
daily_send_limit meant to let only one of them through. The lock is
acquired only after confirming automatic_sending_enabled is true (a
fast, unlocked check), since no quota race is possible on the common
"automatic sending isn't enabled" rejection path. See
crud.lock_outreach_policy's docstring for why this is a real UPDATE
rather than SELECT ... FOR UPDATE (SQLite, used throughout this
project's test suite, has no row-level FOR UPDATE support at all).
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from application.services.infra_adapters import get_recent_ai_decision_logs
from core.domain.models.email_account import EmailAccount, VerificationStatus
from core.domain.models.lead import Lead
from core.domain.models.outreach_action import OutreachAction, OutreachMode, OutreachState
from core.domain.models.outreach_policy import OrganizationOutreachPolicy
from core.infrastructure.database import crud
from core.infrastructure.logging import get_logger
from core.infrastructure.mailing_agent.client import dispatch_outreach_action as _call_mailing_agent
from core.infrastructure.security.credential_crypto import decrypt_credential, CredentialEncryptionError

logger = get_logger(__name__)


class OutreachErrorCode:
    """Closed vocabulary of safe, API-facing failure reasons -- same
    approach as core/infrastructure/email/smtp_verifier.py's
    VerificationErrorCode. api/endpoints/outreach.py maps each of these
    to a specific HTTP status; never a raw exception string."""

    LEAD_NOT_FOUND = "lead_not_found"
    SENDER_NOT_FOUND = "sender_not_found"
    SENDER_NOT_VERIFIED = "sender_not_verified"
    SENDER_DISABLED = "sender_disabled"
    MESSAGE_NOT_READY = "message_not_ready"
    RECIPIENT_INVALID = "recipient_invalid"
    AUTOMATIC_SENDING_DISABLED = "automatic_sending_disabled"
    ACTION_NOT_FOUND = "action_not_found"
    INVALID_STATE_TRANSITION = "invalid_state_transition"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"


class OutreachError(Exception):
    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


@dataclass(frozen=True)
class MessageSnapshot:
    subject: Optional[str]
    body: str
    correlation_id: Optional[str]


def build_message_snapshot(db: Session, lead: Lead) -> MessageSnapshot:
    """Reads the lead's already-generated outreach content -- never
    regenerates it. `body` comes from Lead.outreach_message (written by
    application/workflows/graph_nodes.py's message-generation stage).
    `subject` and `correlation_id` (the producing pipeline run) come from
    the most recent messaging-stage AIDecisionLog row for this lead, when
    one exists -- see application/dto/models.py::MessagingOutput and
    application/memory/db_memory.py, which already reads this exact
    table the same way for a different purpose (business memory).

    Raises OutreachError(MESSAGE_NOT_READY) if there is no outreach
    content yet -- e.g. the lead is still processing, was flagged for
    human_review before message generation ever ran (see
    application/agents/review_agent.py), or AI features are unavailable
    on the organization's plan.
    """
    body = (lead.outreach_message or "").strip()
    if not body:
        raise OutreachError(
            OutreachErrorCode.MESSAGE_NOT_READY,
            "This lead has no generated outreach message yet.",
        )

    subject: Optional[str] = None
    correlation_id: Optional[str] = None
    rows = get_recent_ai_decision_logs(db, lead.id, stage="messaging", limit=1)
    if rows:
        latest = rows[0]
        correlation_id = latest.pipeline_id
        if latest.output_data:
            try:
                subject = json.loads(latest.output_data).get("email_subject") or None
            except (TypeError, ValueError, AttributeError):
                subject = None

    if not subject:
        # A generic, content-neutral default -- not an industry/score
        # heuristic (brief #27/#28 forbid branching message *content* on
        # those; a fallback subject line is neither).
        subject = f"Quick note for {lead.company_name}" if lead.company_name else "Quick note"

    return MessageSnapshot(subject=subject, body=body, correlation_id=correlation_id)


def derive_idempotency_key(
    *,
    lead_id: int,
    email_account_id: int,
    mode: str,
    recipient_email: str,
    recipient_name: Optional[str],
    subject: Optional[str],
    body: str,
) -> str:
    """Deterministic key for the exact same set of fields
    _idempotency_payload_matches compares -- lead, sender, mode,
    recipient, and message content. A retried request authorizing the
    identical logical request collapses onto the same key; changing any
    one of these fields (a different recipient after a lead's contact
    email is corrected, a different message after lead reprocessing, a
    switch between manual and automatic) naturally produces a different
    one, so it can never falsely collide with an unrelated request.

    Serialized as JSON with sort_keys=True and compact separators before
    hashing, not naively joined with ':' -- a plain
    f"{a}:{b}:{c}" would let a ':' *inside* `subject` or `body` (which
    are free-form text, unlike the other fields here) shift which
    logical field a byte belongs to, so two different (subject, body)
    pairs could in principle serialize to the identical string and
    collide on the same derived key. JSON serialization with an explicit
    field name per value has no such ambiguity: each field's boundary is
    structural, not a delimiter character that could also appear inside
    the data.

    `recipient_name` and `subject` are normalized with `or None` before
    serializing, matching _idempotency_payload_matches's own
    normalization of those same two fields -- so an empty string and a
    genuinely absent value are treated identically here too, keeping the
    two functions consistent about what "the same logical request" means.
    """
    canonical = json.dumps(
        {
            "lead_id": lead_id,
            "email_account_id": email_account_id,
            "mode": mode,
            "recipient_email": recipient_email,
            "recipient_name": recipient_name or None,
            "subject": subject or None,
            "body": body,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest_input = canonical.encode("utf-8")
    return "auto:" + hashlib.sha256(digest_input).hexdigest()[:40]


def _get_owned_lead(db: Session, organization_id: int, lead_id: int) -> Lead:
    lead = crud.get_lead(db, lead_id)
    if lead is None or lead.organization_id != organization_id:
        raise OutreachError(OutreachErrorCode.LEAD_NOT_FOUND, "Lead not found.")
    return lead


def _get_eligible_sender(db: Session, organization_id: int, email_account_id: int) -> EmailAccount:
    account = crud.get_email_account(db, organization_id, email_account_id)
    if account is None:
        raise OutreachError(OutreachErrorCode.SENDER_NOT_FOUND, "Sender mailbox not found.")
    if not account.is_active:
        raise OutreachError(OutreachErrorCode.SENDER_DISABLED, "Sender mailbox is disabled.")
    if account.verification_status != VerificationStatus.VERIFIED:
        raise OutreachError(
            OutreachErrorCode.SENDER_NOT_VERIFIED,
            "Sender mailbox must be verified before it can be used for outreach.",
        )
    return account


def _evaluate_automatic_policy(
    db: Session, policy: OrganizationOutreachPolicy, organization_id: int
) -> tuple[bool, bool, Optional[str]]:
    """Returns (allowed_to_create, auto_approve, reason).

    allowed_to_create=False means AUTOMATIC mode may not be used at all
    right now (org hasn't enabled it) -- the caller should reject
    creation outright, not silently fall back to manual, so the caller
    always gets an explicit, actionable reason.

    auto_approve=True means every configured policy check passed and the
    action may be created directly as APPROVED rather than
    PENDING_REVIEW.
    """
    if not policy.automatic_sending_enabled:
        return False, False, "Automatic sending is not enabled for this organization."

    if policy.is_paused:
        return True, False, "Automatic sending is paused for this organization."

    if policy.require_approval_for_automatic:
        return True, False, "This organization requires manual approval for automatic outreach."

    now = datetime.now(timezone.utc)
    active_states = [OutreachState.APPROVED, OutreachState.DISPATCHING, OutreachState.SUBMITTED]

    if policy.daily_send_limit is not None:
        count = crud.count_outreach_actions_since(
            db, organization_id, states=active_states, since=now - timedelta(days=1), mode=OutreachMode.AUTOMATIC
        )
        if count >= policy.daily_send_limit:
            return True, False, "Daily automatic send limit reached."

    if policy.hourly_send_limit is not None:
        count = crud.count_outreach_actions_since(
            db, organization_id, states=active_states, since=now - timedelta(hours=1), mode=OutreachMode.AUTOMATIC
        )
        if count >= policy.hourly_send_limit:
            return True, False, "Hourly automatic send limit reached."

    if policy.sending_window_start_hour_utc is not None and policy.sending_window_end_hour_utc is not None:
        hour = now.hour
        start, end = policy.sending_window_start_hour_utc, policy.sending_window_end_hour_utc
        in_window = (start <= hour < end) if start <= end else (hour >= start or hour < end)
        if not in_window:
            return True, False, "Outside the configured automatic sending window."

    return True, True, None


def _idempotency_payload_matches(
    existing: OutreachAction,
    *,
    lead_id: int,
    email_account_id: int,
    mode: str,
    recipient_email: str,
    recipient_name: Optional[str],
    subject: Optional[str],
    body: str,
) -> bool:
    """True if `existing` (found by idempotency key) represents the same
    logical request as the one currently being made -- see create_action's
    IDEMPOTENCY_KEY_REUSED handling. Proper idempotency-key semantics
    (matching this project's existing Stripe-style precedent nowhere
    else in the codebase, but the standard industry convention) require
    that a *replayed* key return the original result, while the *same*
    key attached to a materially *different* request is a client error,
    not a silent merge of the two. Comparing these seven fields is
    enough: together they fully determine everything create_action
    actually persists onto a fresh row (recipient/subject/body already
    capture the message snapshot -- see build_message_snapshot -- so
    there is no need to separately compare correlation_id, which is
    merely derived from them)."""
    return (
        existing.lead_id == lead_id
        and existing.email_account_id == email_account_id
        and existing.mode == mode
        and existing.recipient_email == recipient_email
        and (existing.recipient_name or None) == (recipient_name or None)
        and (existing.subject or None) == (subject or None)
        and existing.body == body
    )


def create_action(
    db: Session,
    *,
    organization_id: int,
    lead_id: int,
    email_account_id: int,
    mode: str = OutreachMode.MANUAL,
    idempotency_key: Optional[str] = None,
) -> tuple[OutreachAction, bool]:
    """Creates (or, on replay, returns) an OutreachAction. Returns
    (action, created) -- created=False means an action with this
    idempotency key already existed, matched the current request, and
    nothing new was inserted (see module docstring's IDEMPOTENCY
    section). Raises OutreachError(IDEMPOTENCY_KEY_REUSED) instead if an
    existing action is found under this key but represents a materially
    different request (different lead, sender, mode, recipient, or
    message) -- proper idempotency-key semantics treat that as a client
    error, not a silent replay of the wrong result. See
    _idempotency_payload_matches for exactly which fields are compared.
    """
    lead = _get_owned_lead(db, organization_id, lead_id)
    sender = _get_eligible_sender(db, organization_id, email_account_id)

    if not lead.email:
        raise OutreachError(OutreachErrorCode.RECIPIENT_INVALID, "Lead has no recipient email address.")

    snapshot = build_message_snapshot(db, lead)
    key = idempotency_key or derive_idempotency_key(
        lead_id=lead.id,
        email_account_id=sender.id,
        mode=mode,
        recipient_email=lead.email,
        recipient_name=lead.contact_name,
        subject=snapshot.subject,
        body=snapshot.body,
    )

    existing = crud.get_outreach_action_by_idempotency_key(db, organization_id, key)
    if existing is not None:
        if _idempotency_payload_matches(
            existing,
            lead_id=lead.id,
            email_account_id=sender.id,
            mode=mode,
            recipient_email=lead.email,
            recipient_name=lead.contact_name,
            subject=snapshot.subject,
            body=snapshot.body,
        ):
            return existing, False
        raise OutreachError(
            OutreachErrorCode.IDEMPOTENCY_KEY_REUSED,
            "This idempotency_key was already used for a different outreach request.",
        )

    state = OutreachState.PENDING_REVIEW
    reason = None
    if mode == OutreachMode.AUTOMATIC:
        # Fast, unlocked check first: if automatic sending isn't even
        # enabled for this organization, reject outright -- no quota
        # race is possible when no action could ever be auto-approved
        # anyway, so there's no reason to pay for a row lock on this
        # (common, for most organizations) rejection path.
        policy = crud.get_or_create_outreach_policy(db, organization_id)
        if not policy.automatic_sending_enabled:
            raise OutreachError(
                OutreachErrorCode.AUTOMATIC_SENDING_DISABLED,
                "Automatic sending is not enabled for this organization.",
            )

        # From here on we're making a quota-sensitive decision --
        # "count this organization's existing actions, then decide,
        # then insert" -- which races against a concurrent AUTOMATIC
        # create request for the same organization: two such requests
        # could otherwise both count the same not-yet-incremented total
        # and both pass a daily/hourly limit check meant to let only one
        # through. crud.lock_outreach_policy re-fetches the policy row
        # under a write-lock held until this function's commit below (or
        # until an early-exit error path's session close rolls it back),
        # serializing that whole sequence against any other request
        # doing the same for this organization. See that function's
        # docstring for why it's a real UPDATE rather than
        # SELECT ... FOR UPDATE.
        policy = crud.lock_outreach_policy(db, organization_id)
        allowed, auto_approve, policy_reason = _evaluate_automatic_policy(db, policy, organization_id)
        if not allowed:
            # Re-checked under the lock, not just the fast unlocked check
            # above: automatic_sending_enabled (or another allow/deny
            # condition _evaluate_automatic_policy may grow) could have
            # been flipped by a concurrent policy update between the two
            # reads. This *must* actually reject -- silently falling
            # through and inserting a PENDING_REVIEW action anyway would
            # contradict _evaluate_automatic_policy's own documented
            # contract for what allowed=False means.
            raise OutreachError(
                OutreachErrorCode.AUTOMATIC_SENDING_DISABLED,
                policy_reason or "Automatic sending is not enabled for this organization.",
            )
        reason = policy_reason
        if auto_approve:
            state = OutreachState.APPROVED

    try:
        action = crud.create_outreach_action(
            db,
            organization_id=organization_id,
            lead_id=lead.id,
            email_account_id=sender.id,
            mode=mode,
            state=state,
            recipient_email=lead.email,
            recipient_name=lead.contact_name,
            subject=snapshot.subject,
            body=snapshot.body,
            correlation_id=snapshot.correlation_id,
            idempotency_key=key,
            reason=reason,
            approved_at=datetime.now(timezone.utc) if state == OutreachState.APPROVED else None,
        )
        return action, True
    except IntegrityError:
        # Lost a race to a concurrent identical request -- the unique
        # constraint on (organization_id, idempotency_key) is what
        # actually guarantees this is safe; the pre-check above is only
        # an optimization. Same pattern as Lead's uq_leads_org_website
        # race handling.
        db.rollback()
        existing = crud.get_outreach_action_by_idempotency_key(db, organization_id, key)
        if existing is not None:
            if _idempotency_payload_matches(
                existing,
                lead_id=lead.id,
                email_account_id=sender.id,
                mode=mode,
                recipient_email=lead.email,
                recipient_name=lead.contact_name,
                subject=snapshot.subject,
                body=snapshot.body,
            ):
                return existing, False
            raise OutreachError(
                OutreachErrorCode.IDEMPOTENCY_KEY_REUSED,
                "This idempotency_key was already used for a different outreach request.",
            )
        raise


def approve_action(db: Session, *, organization_id: int, action_id: int, approved_by_user_id: int) -> OutreachAction:
    action = crud.get_outreach_action(db, organization_id, action_id)
    if action is None:
        raise OutreachError(OutreachErrorCode.ACTION_NOT_FOUND, "Outreach action not found.")
    if action.state != OutreachState.PENDING_REVIEW:
        # Fast, non-authoritative rejection for a clearly-wrong initial
        # state -- an optimization to avoid an unnecessary UPDATE
        # attempt, not the actual correctness guarantee. That guarantee
        # is the atomic claim below.
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            f"Cannot approve an action in state '{action.state}'.",
        )
    # Re-check sender eligibility at approval time -- it may have changed
    # (disabled, verification invalidated by an unrelated edit) since
    # this action was created.
    _get_eligible_sender(db, organization_id, action.email_account_id)

    # THE atomic claim: a single UPDATE ... WHERE state = 'pending_review'
    # that both checks and changes state in one statement -- see
    # crud.claim_outreach_action_for_approval's docstring for the race
    # this fixes (a concurrent approve/cancel, or two concurrent
    # approvals, silently overwriting each other under the previous
    # read-then-write implementation).
    claimed = crud.claim_outreach_action_for_approval(
        db,
        organization_id=organization_id,
        action_id=action_id,
        approved_by_user_id=approved_by_user_id,
        approved_at=datetime.now(timezone.utc),
    )
    db.commit()
    if not claimed:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            "This action's state changed before approval could be applied.",
        )
    db.refresh(action)
    return action


def cancel_action(db: Session, *, organization_id: int, action_id: int, reason: Optional[str] = None) -> OutreachAction:
    action = crud.get_outreach_action(db, organization_id, action_id)
    if action is None:
        raise OutreachError(OutreachErrorCode.ACTION_NOT_FOUND, "Outreach action not found.")
    if action.state not in OutreachState.CANCELLABLE_FROM:
        # Same "fast, non-authoritative rejection" note as approve_action
        # above -- the atomic claim below is what actually guarantees
        # correctness under concurrency, including against a dispatch
        # attempt that has already (atomically) moved the row to
        # DISPATCHING, which is deliberately not in CANCELLABLE_FROM.
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            f"Cannot cancel an action in state '{action.state}'.",
        )

    claimed = crud.claim_outreach_action_for_cancellation(
        db,
        organization_id=organization_id,
        action_id=action_id,
        cancelled_at=datetime.now(timezone.utc),
        reason=reason,
    )
    db.commit()
    if not claimed:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            "This action's state changed before cancellation could be applied.",
        )
    db.refresh(action)
    return action


async def dispatch_action(db: Session, *, organization_id: int, action_id: int) -> OutreachAction:
    """Hands an APPROVED (or previously DISPATCH_FAILED, for retry)
    action to the Mailing Agent. See module docstring's DISPATCH
    CLAIMING note for the concurrency guarantee this relies on.

    POLICY SEMANTICS (deliberate, documented choice -- not an oversight):
    daily_send_limit / hourly_send_limit / sending_window_*_hour_utc
    gate AUTOMATIC-mode *authorization* only -- they are evaluated once,
    in create_action, to decide whether a new action is auto-approved or
    left PENDING_REVIEW. dispatch_action does NOT re-evaluate them. Once
    an action is APPROVED (whether by policy or by a human), the only
    policy check dispatch_action re-applies is `is_paused` (see below) --
    a rolling-window limit is about how many actions may be *authorized*
    in that window, not a promise that every authorized action will also
    be *sent* within it. Re-checking a rolling quota at dispatch time
    would make this a delayed/queued-sending scheduler (re-evaluate,
    possibly defer, re-evaluate again later) -- exactly the "giant
    scheduler" the P1.4 brief says not to build. `is_paused` is
    different in kind: it is a manual, explicit kill switch an operator
    sets and unsets deliberately, not a rolling count that changes on
    every new action, so re-checking it at both authorization and
    dispatch time is cheap, simple, and doesn't reintroduce scheduling
    semantics.
    """
    action = crud.get_outreach_action(db, organization_id, action_id)
    if action is None:
        raise OutreachError(OutreachErrorCode.ACTION_NOT_FOUND, "Outreach action not found.")
    if action.state not in OutreachState.DISPATCHABLE_FROM:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            f"Cannot dispatch an action in state '{action.state}'.",
        )

    # THE atomic claim. This single UPDATE both checks that the row is
    # still in an eligible source state AND moves it to DISPATCHING, as
    # one database statement -- see crud.claim_outreach_action_for_dispatch
    # and this module's docstring. If two requests race here, exactly one
    # gets claimed=1 (and must proceed) and the other gets claimed=0 (and
    # must stop now, without ever contacting the Mailing Agent). This
    # replaces the earlier (incorrect) draft's separate
    # read-state-then-conditionally-increment-a-counter approach, which
    # did not actually change `state` as part of the claim and so could
    # not prevent two concurrent requests from both proceeding.
    claimed = crud.claim_outreach_action_for_dispatch(db, organization_id=organization_id, action_id=action.id)
    db.commit()
    if not claimed:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            "This action is already being dispatched by another request.",
        )
    db.refresh(action)
    assert action.state == OutreachState.DISPATCHING

    policy = crud.get_or_create_outreach_policy(db, organization_id)
    if policy.is_paused:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = "Outreach sending is paused for this organization."
        db.commit()
        db.refresh(action)
        return action

    try:
        sender = _get_eligible_sender(db, organization_id, action.email_account_id)
    except OutreachError as exc:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = exc.message
        db.commit()
        db.refresh(action)
        return action

    if not sender.encrypted_credential:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = "Sender mailbox has no credential configured."
        db.commit()
        db.refresh(action)
        return action

    try:
        plaintext = decrypt_credential(sender.encrypted_credential)
    except CredentialEncryptionError:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = "Sender credential could not be decrypted."
        db.commit()
        db.refresh(action)
        return action

    try:
        result = await _call_mailing_agent(
            outreach_action_id=action.id,
            organization_id=organization_id,
            idempotency_key=action.idempotency_key,
            correlation_id=action.correlation_id,
            sender_email_address=sender.email_address,
            sender_display_name=sender.display_name,
            smtp_host=sender.smtp_host,
            smtp_port=sender.smtp_port,
            security_mode=sender.security_mode,
            smtp_username=sender.username or sender.email_address,
            credential_type=sender.credential_type,
            plaintext_credential=plaintext,
            recipient_email=action.recipient_email,
            recipient_name=action.recipient_name,
            subject=action.subject,
            body=action.body,
        )
    finally:
        del plaintext  # never held longer than the one outbound call

    if result.accepted:
        action.state = OutreachState.SUBMITTED
        action.submitted_at = datetime.now(timezone.utc)
        action.mailing_agent_reference = result.mailing_agent_reference
        action.last_dispatch_error = None
    else:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = result.error_code

    db.commit()
    db.refresh(action)
    return action
