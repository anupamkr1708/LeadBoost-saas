"""
Organization Qualification Settings model.

P1.2: introduces the organization-level qualification *policy* that was
previously entirely absent from this codebase. Before this table existed,
"qualified" was defined only by a hardcoded 80/60/40 cutoff baked into
core.domain.services.scoring.LeadScoringService._classify_lead -- the same
for every tenant, with no way for an organization to say what score it
personally considers worth pursuing.

This table does NOT replace that scoring/classification logic (Lead.score
and Lead.qualification_label are untouched -- see core/domain/models/lead.py)
and does NOT get consulted by the AI pipeline/agents. It is read only at
the API layer (see api/endpoints/leads.py) to derive a per-organization
`is_qualified` boolean from the lead's already-computed, already-persisted
`score`:

    is_qualified = lead.score >= org_settings.qualification_threshold

Kept as a separate 1:1 table (organization_id unique FK) rather than new
columns on Organization, following the exact same pattern already used by
Subscription (core/domain/models/billing.py) -- a small, independently
evolvable, organization-scoped settings table, not an ever-growing
Organization row.
"""

from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base

# Matches the existing "Warm Lead" cutoff in LeadScoringService._classify_lead
# (core/domain/services/scoring.py) -- chosen only as a backward-compatible
# default for organizations that have not configured their own threshold
# yet, not as a hardcoded qualification rule. The rule itself always reads
# from the persisted row below.
DEFAULT_QUALIFICATION_THRESHOLD = 60.0


class OrganizationQualificationSettings(Base):
    __tablename__ = "organization_qualification_settings"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, unique=True, index=True
    )

    # Same 0-100 scale as Lead.score (core/domain/models/lead.py) -- deliberately
    # NOT 0-1, to avoid a silent unit mismatch against the value it is compared
    # against at read time.
    qualification_threshold = Column(Float, nullable=False, default=DEFAULT_QUALIFICATION_THRESHOLD)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        CheckConstraint(
            "qualification_threshold >= 0.0 AND qualification_threshold <= 100.0",
            name="ck_qualification_threshold_range",
        ),
    )

    # Relationships
    organization = relationship("Organization", back_populates="qualification_settings")
