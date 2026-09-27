"""
OutreachAction model (P1.4).

Represents the LeadBoost-owned authorization/audit boundary for a single
outbound outreach attempt:

    "LeadBoost has authorized / prepared an outreach operation for this
    lead using this sender context and this generated message, and the
    separate Mailing Agent may process it."

    Organization
        1                    1
        |                    |
        *                    *
    Lead ------------- OutreachAction ------------- EmailAccount

This table is deliberately narrow. It does NOT duplicate Lead, Organization
or EmailAccount data -- it references them by id and, where correctness
requires stability over time, snapshots the minimum immutable fields (see
the "message snapshot" note below). It does NOT track delivery/bounce/
reply outcomes -- those belong to the separate Mailing Agent (and a later
phase's reply/bounce-tracking work), never to this table. See the P1.4
brief's explicit distinction:

    message generated != outreach approved != mail accepted != mail
    delivered != recipient replied

`state` (OutreachState) covers only the first two of those; everything
from "mail accepted" onward is the Mailing Agent's own domain. This table
only records whether LeadBoost successfully *handed the action off* to
the Mailing Agent (SUBMITTED) or failed to do so (DISPATCH_FAILED) --
never whether the recipient's mail server, or the recipient, did anything
with it.

MESSAGE SNAPSHOT (brief #18): `subject`/`body` are copied from the lead's
already-generated outreach content (Lead.outreach_message, plus the
Messaging Agent's own subject where available -- see
application/services/outreach_service.py::build_message_snapshot) at the
moment this row is created, and never regenerated or re-read from the
lead afterward. If the lead is later reprocessed and produces different
content, that does not retroactively change an already-authorized
action -- a new OutreachAction would need to be created for the new
message. This avoids "approved today, different message sent tomorrow".

IDEMPOTENCY: `idempotency_key` is unique per organization. A caller may
supply their own key; if omitted,
application/services/outreach_service.py derives a deterministic one --
a canonical JSON serialization of (lead_id, email_account_id, mode,
recipient_email, recipient_name, subject, body), hashed with SHA-256 --
so that retrying the exact same authorization request (API retry,
worker retry, job reclaim) is safely treated as "this action already
exists" rather than creating a duplicate outbound action. The same key
is forwarded to the Mailing Agent on every dispatch attempt -- see
core/infrastructure/mailing_agent/CONTRACT.md for the cross-service
idempotency requirement this enables.

CONCURRENCY (DISPATCHING): dispatch is claimed by a single conditional
UPDATE that both filters on, and changes, `state` in the same statement
-- see OutreachState's docstring and
application/services/outreach_service.py::dispatch_action's "DISPATCH
CLAIMING" note for why the state transition itself, not a separate
read-then-write, is what makes concurrent dispatch attempts for the same
action safe.

TENANCY: every query in application/services/outreach_service.py and
api/endpoints/outreach.py filters on organization_id in the SQL itself
(never fetch-then-check only) -- the same enforced-at-the-query-layer
pattern already used by EmailAccount (see
core/domain/models/email_account.py and api/endpoints/email_accounts.py).

CREDENTIAL SECURITY: this table has no credential-shaped column at all,
by design -- the same "there is no field to forget to redact" approach
EmailAccount already uses. The Mailing Agent contract
(core/infrastructure/mailing_agent/client.py) decrypts the sender's
credential only in-memory, at dispatch time, and never persists it here
or anywhere else.
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.infrastructure.database import Base


class OutreachMode:
    """Who authorized this action to be created, per the P1.4 brief's
    MANUAL vs AUTOMATIC distinction. AUTOMATIC does not by itself mean
    "sent immediately" -- see OrganizationOutreachPolicy and
    application/services/outreach_service.py, which decide whether an
    AUTOMATIC-mode action still requires human approval before it can be
    dispatched."""

    MANUAL = "manual"
    AUTOMATIC = "automatic"

    ALL = (MANUAL, AUTOMATIC)


class OutreachState:
    """LeadBoost-owned authorization/handoff lifecycle. See this module's
    docstring for why delivery/bounce/reply states are intentionally
    absent.

        PENDING_REVIEW -> APPROVED -> DISPATCHING -> SUBMITTED
                                    -> DISPATCHING -> DISPATCH_FAILED
        DISPATCH_FAILED -> (retry) -> DISPATCHING -> SUBMITTED | DISPATCH_FAILED
        PENDING_REVIEW  -> CANCELLED
        APPROVED        -> CANCELLED
        DISPATCH_FAILED -> CANCELLED

    DISPATCHING is a short-lived, machine-only claim state -- no API
    operation ever sets it directly; it exists purely so that "an
    action is currently being dispatched" is a fact recorded in the
    database itself (via a single atomic UPDATE ... WHERE state IN
    (...) that both reads and changes state at once), not something two
    concurrent requests could each independently believe. See
    application/services/outreach_service.py::dispatch_action.

    It is deliberately NOT cancellable and NOT in TERMINAL: a request
    that crashes after claiming DISPATCHING but before resolving to
    SUBMITTED/DISPATCH_FAILED would leave a row stuck there. Recovering
    that (a lease/reclamation mechanism, mirroring
    core/domain/models/job.py's) is explicitly out of P1.4's scope --
    the brief calls for a minimal atomic claim, not a distributed lock
    or lease system, and a crash in the tiny window between the claim
    and the single subsequent HTTP call is rare enough that a manual
    fix is an acceptable, explicitly documented limitation for this
    phase rather than a reason to build that machinery now.

    SUBMITTED and CANCELLED are terminal -- no further state transition
    is valid from either (see outreach_service.py's transition guards).
    """

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    DISPATCH_FAILED = "dispatch_failed"
    CANCELLED = "cancelled"

    ALL = (PENDING_REVIEW, APPROVED, DISPATCHING, SUBMITTED, DISPATCH_FAILED, CANCELLED)
    TERMINAL = (SUBMITTED, CANCELLED)
    # States dispatch_action's atomic claim UPDATE may transition *from*.
    DISPATCHABLE_FROM = (APPROVED, DISPATCH_FAILED)
    # States cancel_action may transition from. DISPATCHING is
    # deliberately excluded -- see this class's docstring.
    CANCELLABLE_FROM = (PENDING_REVIEW, APPROVED, DISPATCH_FAILED)


class OutreachAction(Base):
    __tablename__ = "outreach_actions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    email_account_id = Column(Integer, ForeignKey("email_accounts.id"), nullable=False, index=True)

    mode = Column(String, nullable=False, default=OutreachMode.MANUAL)
    state = Column(String, nullable=False, default=OutreachState.PENDING_REVIEW, index=True)

    # --- Immutable message snapshot (see module docstring) ---
    recipient_email = Column(String, nullable=False)
    recipient_name = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    body = Column(Text, nullable=False)

    # Correlates back to the specific lead_pipeline run that produced the
    # snapshotted message, when known (AIDecisionLog.pipeline_id -- see
    # core/domain/models/lead.py). Nullable because not every message was
    # necessarily produced by a pipeline run this system can still trace
    # (e.g. a very old lead re-approved after a schema gap).
    correlation_id = Column(String, nullable=True, index=True)

    idempotency_key = Column(String, nullable=False, index=True)

    # Free-text, safe explanation of the current state (e.g. "automatic
    # policy requires approval", "daily send limit reached", "cancelled by
    # user") -- never raw exception/provider text. See brief #24.
    reason = Column(Text, nullable=True)

    approved_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    # Minimal handoff acknowledgment from the Mailing Agent -- NOT
    # delivery/bounce/reply tracking (see module docstring). Just enough
    # to correlate this row with the Mailing Agent's own record.
    mailing_agent_reference = Column(String, nullable=True)
    dispatch_attempts = Column(Integer, nullable=False, default=0)
    last_dispatch_error = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        # The idempotency guarantee itself (brief #12) -- enforced at the
        # database level, not only in application logic, so a race between
        # two concurrent identical requests can't both insert.
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_outreach_actions_org_idempotency_key"
        ),
        # Supports the two real access patterns this phase needs: listing
        # an organization's actions filtered by state (dashboard/API list
        # endpoint), and listing an organization's actions for one lead
        # (lead detail view).
        Index("ix_outreach_actions_org_state", "organization_id", "state"),
        Index("ix_outreach_actions_org_lead", "organization_id", "lead_id"),
    )

    organization = relationship("Organization")
    lead = relationship("Lead")
    email_account = relationship("EmailAccount")
    approved_by = relationship("User")
