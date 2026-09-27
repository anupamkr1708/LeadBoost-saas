"""
OrganizationOutreachPolicy model (P1.4).

A 1:1 per-organization settings row, following the exact pattern already
established by OrganizationQualificationSettings (P1.2, see
core/domain/models/qualification_settings.py): a small, independently
evolvable settings table rather than new columns on Organization.

This is the persisted, explicit "customer safety policy" the P1.4 brief
requires before an AUTOMATIC-mode OutreachAction (see
core/domain/models/outreach_action.py::OutreachMode) may be authorized
without a human approving it -- never a hardcoded score/industry
heuristic (brief #10, #27, #28). It has no effect on MANUAL-mode actions
except `is_paused`, which is a global kill switch applied to both modes
at dispatch time (see application/services/outreach_service.py).

No row is required to exist for a given organization. Absence means
"automatic outreach not opted into yet" -- application/services/
outreach_service.py::get_or_create_policy defaults an organization with
no row to the safest posture (automatic_sending_enabled=False,
require_approval_for_automatic=True), never to "wide open".
"""

from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from core.infrastructure.database import Base


class OrganizationOutreachPolicy(Base):
    __tablename__ = "organization_outreach_policies"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, unique=True, index=True
    )

    # Master opt-in: an organization must explicitly turn this on before
    # any OutreachAction can be created with mode=automatic at all.
    automatic_sending_enabled = Column(Boolean, nullable=False, default=False)

    # Even when automatic sending is enabled, this decides whether an
    # automatic-mode action that otherwise passes every policy check
    # (verified sender, within send limits, within sending window) still
    # lands in PENDING_REVIEW for a human to approve, or is created
    # directly as APPROVED. Defaults True -- "automatic" starts out
    # meaning "the system may prepare it", not "the system may also
    # approve it unattended" -- an organization must separately choose to
    # relax this.
    require_approval_for_automatic = Column(Boolean, nullable=False, default=True)

    # Rolling-window caps evaluated against this organization's own
    # AUTOMATIC-mode OutreachAction history only (see
    # outreach_service.py's policy check and
    # crud.count_outreach_actions_since's mode filter) -- not a global
    # platform limit, and not an organization-wide cap across both
    # modes: a manually-approved action never counts against these.
    # NULL means unlimited.
    daily_send_limit = Column(Integer, nullable=True)
    hourly_send_limit = Column(Integer, nullable=True)

    # Inclusive UTC hour-of-day window, e.g. 13-21. Both NULL means no
    # window restriction. Deliberately UTC-only and hour-granularity for
    # this phase -- a full per-organization timezone/calendar model is a
    # real future need but not one P1.4's actual scope requires (brief
    # #10: "do not blindly implement every imaginable control").
    sending_window_start_hour_utc = Column(Integer, nullable=True)
    sending_window_end_hour_utc = Column(Integer, nullable=True)

    # Global kill switch: when true, no OutreachAction for this
    # organization -- manual or automatic -- may be dispatched to the
    # Mailing Agent, and no automatic-mode action may be auto-approved.
    # Existing PENDING_REVIEW/APPROVED rows are left untouched (pausing
    # does not cancel work in flight); it only blocks forward progress.
    is_paused = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "daily_send_limit IS NULL OR daily_send_limit >= 0",
            name="ck_outreach_policy_daily_limit_nonneg",
        ),
        CheckConstraint(
            "hourly_send_limit IS NULL OR hourly_send_limit >= 0",
            name="ck_outreach_policy_hourly_limit_nonneg",
        ),
        CheckConstraint(
            "sending_window_start_hour_utc IS NULL OR "
            "(sending_window_start_hour_utc >= 0 AND sending_window_start_hour_utc <= 23)",
            name="ck_outreach_policy_window_start_range",
        ),
        CheckConstraint(
            "sending_window_end_hour_utc IS NULL OR "
            "(sending_window_end_hour_utc >= 0 AND sending_window_end_hour_utc <= 23)",
            name="ck_outreach_policy_window_end_range",
        ),
    )

    organization = relationship("Organization")
