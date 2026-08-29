"""
ActivePipelineLock model.

Additive-only table (production-robustness hardening, Phase A3/A4).

The Lead model has no "currently processing" status of any kind, and
`application.observability.models.PipelineExecutionRecord` is only ever
written *after* a pipeline finishes (success or failure) -- there is no
existing durable signal that a pipeline is *in flight* for a given lead
right now. Without one, a retried/duplicate-clicked/concurrent call to
`POST /leads/{id}/process` (or any other path that reaches
`application.workflows.lead_pipeline.run_lead_pipeline`) has no way to
detect that another execution for the same lead is already running, and
launches a second full scrape + enrichment + LLM run.

This table is a minimal mutual-exclusion primitive, not a job queue or a
second state machine: exactly one row may exist per `lead_id` at a time,
enforced by a real database UNIQUE constraint (durable across worker
processes, unlike an in-memory lock, and dialect-portable across SQLite
and PostgreSQL). A row is inserted immediately before a pipeline run
starts and deleted in a `finally` block once it ends -- see
application.concurrency.pipeline_lock for the acquire/release helpers
actually used by application.workflows.lead_pipeline.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from core.infrastructure.database import Base


class ActivePipelineLock(Base):
    __tablename__ = "active_pipeline_locks"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    pipeline_id = Column(String, nullable=False)

    acquired_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # The actual mutual-exclusion guarantee: a second INSERT for a
        # lead_id that already has a row raises IntegrityError, which the
        # acquire helper interprets as "already processing".
        UniqueConstraint("lead_id", name="uq_active_pipeline_lock_lead_id"),
    )
