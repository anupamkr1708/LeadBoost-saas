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

L1 HAND-OFF (what dispatch_action sends, and what it never does): dispatch
no longer decrypts or forwards any SMTP credential and no longer sends a
pre-written message. LeadBoost authorizes (this OutreachAction); the Mailer
generates, grounds, stores and sends the Message through its own Mailbox
(see core/infrastructure/mailing_agent/CONTRACT.md). Dispatch therefore sends
only recipient + business context (application/services/outreach_context.py)
+ the stable idempotency key. OutreachAction.subject/body remain the
legacy authorization snapshot; the authoritative DELIVERED text is the
Mailer's Message. Before calling the Mailer, dispatch requires that the
action's sender is the organization's single eligible account and that its
Mailer mailbox is provisioned and in sync (application/services/
mailer_mailbox_sync.py); otherwise it fails closed without a network call.

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
from application.services.outreach_context import build_recipient_facts, build_value_proposition
from core.domain.models.email_account import EmailAccount, MailerSyncState, VerificationStatus
from core.domain.models.lead import Lead
from core.domain.models.outreach_action import OutreachAction, OutreachMode, OutreachState
from core.domain.models.outreach_policy import OrganizationOutreachPolicy
from core.infrastructure.database import crud
from core.infrastructure.logging import get_logger
from core.infrastructure.mailing_agent import reply_draft_client
from core.infrastructure.mailing_agent.client import submit_generated_outreach as _call_mailing_agent

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
    # L1: more than one active+verified sender -> the integrated path refuses to guess.
    MULTIPLE_ACTIVE_SENDERS = "multiple_active_senders"
    # C9.4 replies: always human-reviewed, and identified only by the inbound message.
    REPLY_REQUIRES_REVIEW = "reply_requires_review"
    REPLY_IDEMPOTENCY_KEY_NOT_ALLOWED = "reply_idempotency_key_not_allowed"
    # C9.5 exact-content approval of a managed reply.
    NOT_A_REPLY = "not_a_reply"
    REPLY_DRAFT_NOT_READY = "reply_draft_not_ready"          # not requested yet / still generating / failed / cancelled
    REPLY_DRAFT_STALE = "reply_draft_stale"                  # the draft changed since the user reviewed it
    REPLY_DRAFT_UNAVAILABLE = "reply_draft_unavailable"      # the Mailer could not tell us (transport / schema / key rejected)
    REPLY_DRAFT_TOO_LARGE = "reply_draft_too_large"          # a draft the Mailer refuses to show in full: never approvable
    REPLY_CONTENT_HASH_REQUIRED = "reply_content_hash_required"  # a reply is approved by digest of the reviewed draft
    REPLY_APPROVAL_MISSING = "reply_approval_missing"        # dispatch of a reply that carries no approved digest


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


def derive_reply_idempotency_key(*, organization_id: int, reply_to_message_reference: str) -> str:
    """The ONE identity of a reply: tenant + the inbound message it answers -- nothing else.
    Lead, sender account, mode and the message snapshot are deliberately NOT part of it, so
    a retry, a re-poll, a different sender or a different lead can never produce a second,
    independently dispatchable reply to the same inbound message. A caller-supplied
    idempotency_key is refused for replies (create_action) so it cannot bypass this."""
    canonical = json.dumps(
        {"organization_id": organization_id, "reply_to_message_reference": reply_to_message_reference},
        sort_keys=True, separators=(",", ":"),
    )
    return "reply:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:40]


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
    reply_to_message_reference: Optional[str] = None,
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
    if reply_to_message_reference:
        # A reply's identity is (tenant, inbound message). A replay is the same request when it
        # targets the same lead/sender/recipient; the free-text snapshot may legitimately have
        # changed since (lead reprocessed) and must not turn a retry into a conflict. A different
        # lead or sender for the same inbound message is NOT a replay: it is refused (409).
        return (
            existing.reply_to_message_reference == reply_to_message_reference
            and existing.lead_id == lead_id
            and existing.email_account_id == email_account_id
            and existing.recipient_email == recipient_email
        )
    return (
        existing.lead_id == lead_id
        and existing.email_account_id == email_account_id
        and existing.mode == mode
        and existing.recipient_email == recipient_email
        and (existing.recipient_name or None) == (recipient_name or None)
        and (existing.subject or None) == (subject or None)
        and existing.body == body
        and (existing.reply_to_message_reference or None) == (reply_to_message_reference or None)
    )


def create_action(
    db: Session,
    *,
    organization_id: int,
    lead_id: int,
    email_account_id: int,
    mode: str = OutreachMode.MANUAL,
    idempotency_key: Optional[str] = None,
    reply_to_message_reference: Optional[str] = None,
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
    if reply_to_message_reference:
        # Policy lives HERE, not in the UI. First implementation: a reply is always created in
        # PENDING_REVIEW and needs an explicit approve + dispatch. AUTOMATIC is refused before
        # anything is read or written, so the AUTOMATIC policy branch below (which can set
        # APPROVED) is unreachable for replies. Ordinary outreach is unchanged.
        if mode != OutreachMode.MANUAL:
            raise OutreachError(
                OutreachErrorCode.REPLY_REQUIRES_REVIEW,
                "A reply action must be created in manual mode and approved explicitly.",
            )
        if idempotency_key:
            raise OutreachError(
                OutreachErrorCode.REPLY_IDEMPOTENCY_KEY_NOT_ALLOWED,
                "A reply action is identified by the inbound message; do not supply an idempotency_key.",
            )

    lead = _get_owned_lead(db, organization_id, lead_id)
    sender = _get_eligible_sender(db, organization_id, email_account_id)

    if not lead.email:
        raise OutreachError(OutreachErrorCode.RECIPIENT_INVALID, "Lead has no recipient email address.")

    if reply_to_message_reference:
        # A reply's text is generated by the Mailer from the inbound message, so the lead's ORIGINAL outreach
        # text is irrelevant to it. The snapshot is still stored when it exists (unchanged C9.4 behaviour), but
        # its absence must not stop a legitimate reply: fall back to an empty snapshot. Never invented email
        # text, and never shown as the reply (the composer reads the Mailer's persisted draft).
        try:
            snapshot = build_message_snapshot(db, lead)
        except OutreachError as exc:
            if exc.error_code != OutreachErrorCode.MESSAGE_NOT_READY:
                raise
            snapshot = MessageSnapshot(subject=None, body="", correlation_id=None)
    else:
        snapshot = build_message_snapshot(db, lead)
    if reply_to_message_reference:
        key = derive_reply_idempotency_key(
            organization_id=organization_id, reply_to_message_reference=reply_to_message_reference
        )
    elif idempotency_key:
        key = idempotency_key
    else:
        key = derive_idempotency_key(
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
            reply_to_message_reference=reply_to_message_reference,
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
            reply_to_message_reference=reply_to_message_reference,
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
                reply_to_message_reference=reply_to_message_reference,
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
    if action.reply_to_message_reference:
        # C9.5: a managed reply is approved by the digest of the exact draft the user reviewed
        # (approve_reply_action). This ordinary path has no such evidence, so it must never approve one.
        raise OutreachError(
            OutreachErrorCode.REPLY_CONTENT_HASH_REQUIRED,
            "A reply must be approved against the exact draft that was reviewed.",
        )
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
    if action.reply_to_message_reference and not action.approved_content_sha256:
        # C9.5: a reply is released by the digest the user approved. Raised BEFORE the claim, so the action
        # stays where it is and the Mailer is never contacted.
        raise OutreachError(
            OutreachErrorCode.REPLY_APPROVAL_MISSING,
            (_LEGACY_MESSAGE + " It cannot be sent. Cancel it to close it.")
            if _is_legacy_reply(action)
            else "This reply has no approved draft. Review and approve the generated draft first.",
        )

    # L1: the Mailer selects its organization's sole ACTIVE mailbox, so more than
    # one eligible (active + verified) account here is a configuration conflict.
    # Raised BEFORE the claim: the action stays APPROVED/DISPATCH_FAILED
    # (retryable once the extra account is disabled), nothing is marked failed,
    # and the Mailer is never contacted. (api maps this to HTTP 409.)
    if crud.count_eligible_email_accounts(db, organization_id) > 1:
        raise OutreachError(
            OutreachErrorCode.MULTIPLE_ACTIVE_SENDERS,
            "Multiple active email accounts are configured. "
            "Exactly one mailbox must be active for integrated outreach.",
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

    # L1: the Mailer owns the send-time credential, so the only thing LeadBoost
    # checks is that ITS mailbox for this account exists and mirrors the
    # account's current state. No credential is read, decrypted or forwarded here.
    if not sender.mailer_mailbox_ref or sender.mailer_sync_state != MailerSyncState.SYNCED:
        detail = f" ({sender.mailer_sync_error_code})" if sender.mailer_sync_error_code else ""
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = (
            f"Sender mailbox is not synchronized with the Mailer{detail}. "
            "Re-verify the account or retry synchronization."
        )
        db.commit()
        db.refresh(action)
        return action

    if action.reply_to_message_reference:
        # C9.5: the draft already exists at the Mailer and the user approved its exact content. RELEASE that
        # same draft -- never submit a generation request, never create another dispatch or message.
        return await _release_approved_reply(db, action)

    organization = crud.get_organization(db, organization_id)
    value_proposition = build_value_proposition(organization)
    if not value_proposition:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = (
            "value_proposition_not_configured: set the organization description "
            "(\"What does your team do?\") before sending."
        )
        db.commit()
        db.refresh(action)
        return action

    try:
        lead = _get_owned_lead(db, organization_id, action.lead_id)
    except OutreachError as exc:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = exc.message
        db.commit()
        db.refresh(action)
        return action

    result = await _call_mailing_agent(
        organization_id=organization_id,
        outreach_action_id=action.id,
        idempotency_key=action.idempotency_key,
        correlation_id=action.correlation_id,
        recipient_email=action.recipient_email,
        recipient_name=action.recipient_name or lead.contact_name,
        recipient_title=lead.contact_title,
        recipient_company=lead.company_name,
        value_proposition=value_proposition,
        recipient_facts=build_recipient_facts(lead),
        **(
            {"reply_to_message_reference": action.reply_to_message_reference}
            if action.reply_to_message_reference else {}
        ),
    )

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


# =====================================================================================================
# C9.5 -- managed-reply exact-content approval
#
#   create_action (reply, PENDING_REVIEW)           nothing is sent, nothing is generated
#   start_reply_draft  -> Mailer generates + HOLDS  the existing generated-outreach request; idempotent on the
#                                                   action's key; sends nothing and cannot be made sendable
#   get_reply_draft    -> the Mailer's persisted subject/body, read server-to-server (strict schema)
#   approve_reply_action(content_sha256)            fetches the CURRENT draft, requires its digest == the one the
#                                                   user reviewed, then ONE atomic UPDATE -> APPROVED that also
#                                                   writes the digest as approval evidence
#   dispatch_action (explicit)  -> release          the Mailer re-verifies the digest and releases the SAME draft
#   cancel_reply_action                             LeadBoost's atomic cancel + revocation of the held draft
#
# LeadBoost's OutreachState machine is unchanged. The draft's own status is read from the Mailer on demand.
# =====================================================================================================

_HEX64 = frozenset("0123456789abcdef")


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _require_reply(action: Optional[OutreachAction]) -> OutreachAction:
    if action is None:
        raise OutreachError(OutreachErrorCode.ACTION_NOT_FOUND, "Outreach action not found.")
    if not action.reply_to_message_reference:
        raise OutreachError(OutreachErrorCode.NOT_A_REPLY, "This action is not a reply.")
    return action


@dataclass(frozen=True)
class ReplyDraftView:
    """Product-level projection of the Mailer's draft. `state` is "not_started" (LeadBoost has not asked for a
    draft yet), "cancelled" (LeadBoost cancelled the action), or the Mailer's own state."""

    state: str
    subject: Optional[str] = None
    body: Optional[str] = None
    content_sha256: Optional[str] = None
    draft_version: Optional[int] = None
    updated_at: Optional[datetime] = None


_UNAVAILABLE = "The reply draft is temporarily unavailable. Try again."


def _draft_error(result) -> OutreachError:
    """Map a non-ok Mailer draft result to ONE closed LeadBoost error. Distinct outcomes stay distinct: "the Mailer has no
    draft yet" is retry-later, "too large to review in full" can never succeed (so it must not look like an outage), and
    everything else (transport, timeout, 5xx, schema drift, the key being rejected) is "unavailable". No Mailer text,
    status or draft content is ever carried into the message."""
    if result.status == "not_found":
        return OutreachError(OutreachErrorCode.REPLY_DRAFT_NOT_READY, "The Mailer has no draft for this reply.")
    if result.error_code == reply_draft_client.ReplyDraftErrorCode.DRAFT_TOO_LARGE:
        return OutreachError(
            OutreachErrorCode.REPLY_DRAFT_TOO_LARGE,
            "The generated reply is too long to show in full, so it cannot be reviewed or approved. Cancel it.",
        )
    return OutreachError(OutreachErrorCode.REPLY_DRAFT_UNAVAILABLE, _UNAVAILABLE)


# A reply action created BEFORE exact-content approval existed (C9.4) was approved as an ACTION, and its text was written
# by the Mailer afterwards. It has no `reply_draft_requested_at` and no approved digest. Such an approval is NEVER treated
# as approval of a newly generated draft: it cannot be re-approved, restarted or sent -- only cancelled while that is
# still allowed. (A C9.4 reply still PENDING_REVIEW is not legacy in this sense: it never approved anything, and simply
# takes the new flow.)
_LEGACY_STATES = (
    OutreachState.APPROVED,
    OutreachState.DISPATCHING,
    OutreachState.DISPATCH_FAILED,
    OutreachState.SUBMITTED,
)


def _is_legacy_reply(action: OutreachAction) -> bool:
    return (
        bool(action.reply_to_message_reference)
        and action.reply_draft_requested_at is None
        and action.state in _LEGACY_STATES
    )


_LEGACY_MESSAGE = (
    "This reply was created before exact-content review existed, so its earlier approval cannot be applied to a new "
    "draft."
)


async def start_reply_draft(db: Session, *, organization_id: int, action_id: int) -> OutreachAction:
    """Ask the Mailer to generate this reply's draft. Allowed BEFORE approval because it is not a send: the Mailer
    holds every managed reply. Idempotent: it is the existing generated-outreach request under the action's own
    key, which the Mailer replays instead of regenerating, so repeating it after a timeout is the recovery."""
    action = _require_reply(crud.get_outreach_action(db, organization_id, action_id))
    if _is_legacy_reply(action):
        raise OutreachError(OutreachErrorCode.INVALID_STATE_TRANSITION, _LEGACY_MESSAGE)
    if action.state != OutreachState.PENDING_REVIEW:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            f"Cannot start a reply draft for an action in state '{action.state}'.",
        )
    sender = _get_eligible_sender(db, organization_id, action.email_account_id)
    if not sender.mailer_mailbox_ref or sender.mailer_sync_state != MailerSyncState.SYNCED:
        raise OutreachError(
            OutreachErrorCode.REPLY_DRAFT_UNAVAILABLE,
            "Sender mailbox is not synchronized with the Mailer. Re-verify the account or retry synchronization.",
        )
    organization = crud.get_organization(db, organization_id)
    value_proposition = build_value_proposition(organization)
    if not value_proposition:
        raise OutreachError(
            OutreachErrorCode.REPLY_DRAFT_UNAVAILABLE,
            "Set the organization description (\"What does your team do?\") before generating a reply.",
        )
    lead = _get_owned_lead(db, organization_id, action.lead_id)

    result = await _call_mailing_agent(
        organization_id=organization_id,
        outreach_action_id=action.id,
        idempotency_key=action.idempotency_key,
        correlation_id=action.correlation_id,
        recipient_email=action.recipient_email,
        recipient_name=action.recipient_name or lead.contact_name,
        recipient_title=lead.contact_title,
        recipient_company=lead.company_name,
        value_proposition=value_proposition,
        recipient_facts=build_recipient_facts(lead),
        reply_to_message_reference=action.reply_to_message_reference,
    )
    if not result.accepted:
        logger.warning(
            "Mailer did not accept the reply draft request",
            extra={"organization_id": organization_id, "outreach_action_id": action.id, "error_code": result.error_code},
        )
        raise OutreachError(OutreachErrorCode.REPLY_DRAFT_UNAVAILABLE, _UNAVAILABLE)

    marked = crud.mark_reply_draft_requested(
        db,
        organization_id=organization_id,
        action_id=action.id,
        requested_at=datetime.now(timezone.utc),
        mailing_agent_reference=result.mailing_agent_reference,
    )
    db.commit()
    if not marked:
        # The action was cancelled/approved while the Mailer call was in flight. The Mailer may now hold a draft
        # for it; revoke it so it can never become sendable, then report the lost race.
        await reply_draft_client.cancel_reply_draft(
            organization_id=organization_id, idempotency_key=action.idempotency_key
        )
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            "This action's state changed before the reply draft could be started.",
        )
    db.refresh(action)
    return action


async def get_reply_draft(db: Session, *, organization_id: int, action_id: int) -> ReplyDraftView:
    """The Mailer's persisted draft, fetched server-to-server and validated against a strict schema. Read-only: it
    never starts generation, approves, releases or mutates anything, so it is safe to poll."""
    action = _require_reply(crud.get_outreach_action(db, organization_id, action_id))
    if action.state == OutreachState.CANCELLED:
        return ReplyDraftView(state="cancelled")
    if _is_legacy_reply(action):
        return ReplyDraftView(state="legacy")        # no draft was ever reviewed for it; nothing to fetch
    if action.reply_draft_requested_at is None:
        return ReplyDraftView(state="not_started")

    # Copy what the network call needs, then end the read transaction: no pooled DB connection is held across the
    # Mailer call (up to MAILING_AGENT_TIMEOUT_SECONDS, and the browser polls this read while a draft is being
    # generated). Same convention as outreach_mailer_state.get_outreach_mailer_state. The rollback expires `action`,
    # so it must not be touched again below.
    idempotency_key = action.idempotency_key
    db.rollback()

    result = await reply_draft_client.fetch_reply_draft(
        organization_id=organization_id, idempotency_key=idempotency_key
    )
    if result.status == "ok":
        d = result.draft
        return ReplyDraftView(
            state=d.state, subject=d.subject, body=d.body, content_sha256=d.content_sha256,
            draft_version=d.draft_version, updated_at=d.updated_at,
        )
    raise _draft_error(result)


async def approve_reply_action(
    db: Session, *, organization_id: int, action_id: int, approved_by_user_id: int, content_sha256: str
) -> OutreachAction:
    """Approve the EXACT draft the user reviewed. The digest in the request is only a claim ("this is what I saw"):
    it is never trusted as proof. LeadBoost fetches the Mailer's CURRENT draft server-to-server and approves only
    if that draft is ready and its digest is the one that was reviewed; the stored evidence is the Mailer's digest.
    A stale or changed draft is refused and the user must review the current one."""
    action = _require_reply(crud.get_outreach_action(db, organization_id, action_id))
    if not _is_digest(content_sha256):
        raise OutreachError(
            OutreachErrorCode.REPLY_CONTENT_HASH_REQUIRED,
            "Approve a reply by the digest of the draft you reviewed.",
        )
    if _is_legacy_reply(action):
        raise OutreachError(OutreachErrorCode.INVALID_STATE_TRANSITION, _LEGACY_MESSAGE)
    if action.state != OutreachState.PENDING_REVIEW:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION, f"Cannot approve an action in state '{action.state}'."
        )
    if action.reply_draft_requested_at is None:
        raise OutreachError(OutreachErrorCode.REPLY_DRAFT_NOT_READY, "Generate the reply draft first.")
    # Sender may have been disabled or un-verified since creation (same re-check as ordinary approval).
    _get_eligible_sender(db, organization_id, action.email_account_id)

    result = await reply_draft_client.fetch_reply_draft(
        organization_id=organization_id, idempotency_key=action.idempotency_key
    )
    if result.status != "ok":
        raise _draft_error(result)
    draft = result.draft
    if draft.state != "awaiting_approval":
        raise OutreachError(
            OutreachErrorCode.REPLY_DRAFT_NOT_READY,
            "The reply draft is not ready for approval."
            if draft.state == "generating"
            else f"The reply draft cannot be approved (it is {draft.state}).",
        )
    if draft.content_sha256 != content_sha256:
        raise OutreachError(
            OutreachErrorCode.REPLY_DRAFT_STALE,
            "The reply draft changed after you reviewed it. Review the current draft.",
        )

    claimed = crud.claim_outreach_action_for_approval(
        db,
        organization_id=organization_id,
        action_id=action_id,
        approved_by_user_id=approved_by_user_id,
        approved_at=datetime.now(timezone.utc),
        approved_content_sha256=draft.content_sha256,
        approved_draft_version=draft.draft_version,
    )
    db.commit()
    if not claimed:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION,
            "This action's state changed before approval could be applied.",
        )
    db.refresh(action)
    return action


async def _release_approved_reply(db: Session, action: OutreachAction) -> OutreachAction:
    """DISPATCHING -> SUBMITTED | DISPATCH_FAILED for a managed reply. Releases the SAME held draft under the same
    idempotency key with the digest the user approved; the Mailer re-verifies it. An ambiguous result (timeout,
    5xx) leaves DISPATCH_FAILED and is reconciled by repeating the call: the release is idempotent for the same
    digest, so it either replays as already released (-> SUBMITTED) or is released now. It can never double-send."""
    result = await reply_draft_client.release_reply_draft(
        organization_id=action.organization_id,
        idempotency_key=action.idempotency_key,
        content_sha256=action.approved_content_sha256,
    )
    if result.status == "ok":
        action.state = OutreachState.SUBMITTED
        action.submitted_at = datetime.now(timezone.utc)
        action.last_dispatch_error = None
    elif result.status == "conflict":
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = f"reply_release_{result.conflict_code}"
    elif result.status == "not_found":
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = "reply_release_draft_not_found"
    else:
        action.state = OutreachState.DISPATCH_FAILED
        action.last_dispatch_error = result.error_code
    db.commit()
    db.refresh(action)
    return action


async def cancel_reply_action(
    db: Session, *, organization_id: int, action_id: int, reason: Optional[str] = None
) -> OutreachAction:
    """Cancel a reply AND make sure its held draft can never be sent.

    PENDING_REVIEW / APPROVED: nothing has been released (only dispatch releases, and dispatch needs APPROVED), so
    LeadBoost's atomic cancel runs first and is the single arbiter against a concurrent approve/dispatch; the
    Mailer draft is then revoked (best effort -- even if that call fails the draft cannot be released, because
    LeadBoost will never dispatch a CANCELLED action).

    DISPATCH_FAILED: a release may have been attempted and may have SUCCEEDED despite an ambiguous result, so
    LeadBoost must not say "cancelled" while the Mailer is about to send. The Mailer is asked to revoke FIRST: if
    it was already released the cancel is refused (the user can retry sending, which replays idempotently);
    if the Mailer cannot confirm, the cancel is refused rather than guessed."""
    action = _require_reply(crud.get_outreach_action(db, organization_id, action_id))
    if action.state not in OutreachState.CANCELLABLE_FROM:
        raise OutreachError(
            OutreachErrorCode.INVALID_STATE_TRANSITION, f"Cannot cancel an action in state '{action.state}'."
        )
    # A Mailer operation may exist if a draft was requested -- or, for a C9.4 reply, if it was ever dispatched (the old flow
    # submitted a generation request at dispatch). Either way the Mailer is told. It is asked FIRST when a release may
    # already have happened (dispatch_failed) and for any legacy reply that has been dispatched: a legacy operation
    # may be queued, sending or sent at the Mailer, so LeadBoost must not mark it cancelled on its own say-so.
    remote = action.reply_draft_requested_at is not None or (action.dispatch_attempts or 0) > 0
    key = action.idempotency_key
    revoked_first = False

    if remote and (action.state == OutreachState.DISPATCH_FAILED or _is_legacy_reply(action)):
        res = await reply_draft_client.cancel_reply_draft(organization_id=organization_id, idempotency_key=key)
        if res.status == "conflict":
            if res.conflict_code == "legacy_reply_requires_reconciliation":
                raise OutreachError(
                    OutreachErrorCode.INVALID_STATE_TRANSITION,
                    "This reply was sent through the Mailer before reply review existed, and the Mailer cannot "
                    "confirm it was not sent. It cannot be cancelled here; an operator must reconcile it.",
                )
            raise OutreachError(
                OutreachErrorCode.INVALID_STATE_TRANSITION,
                "This reply was already released to the Mailer and can no longer be cancelled.",
            )
        if res.status == "error":
            raise OutreachError(
                OutreachErrorCode.REPLY_DRAFT_UNAVAILABLE,
                "Could not confirm that the draft was revoked. Try again.",
            )
        if res.status == "ok" and (res.draft is None or res.draft.state not in ("cancelled", "failed")):
            # Defence in depth: only a Mailer that PROVES nothing can be sent (revoked, or definitively failed) may
            # let LeadBoost cancel. An "ok" carrying any other state is not that proof.
            raise OutreachError(
                OutreachErrorCode.INVALID_STATE_TRANSITION,
                "The Mailer did not confirm that this reply was revoked, so it cannot be cancelled here.",
            )
        # "ok" (revoked / provably never sent) or "not_found" (the Mailer genuinely has no such operation).
        revoked_first = True

    action = cancel_action(db, organization_id=organization_id, action_id=action_id, reason=reason)

    if remote and not revoked_first:
        res = await reply_draft_client.cancel_reply_draft(organization_id=organization_id, idempotency_key=key)
        if res.status not in ("ok", "not_found"):
            logger.warning(
                "Reply draft revocation was not confirmed after LeadBoost cancelled the action",
                extra={"organization_id": organization_id, "outreach_action_id": action_id,
                       "status": res.status, "error_code": res.error_code, "conflict_code": res.conflict_code},
            )
    return action
