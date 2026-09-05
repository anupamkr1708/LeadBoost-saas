"""
Job model -- P0-A (durable job execution).

CONFIRMED GAP (see the Phase 0 audit in the P0 final report): today,
expensive pipeline work is dispatched two ways, and both lose the work
silently on a process crash:

  1. `POST /leads` / `POST /leads/single` schedule `run_lead_pipeline`
     via FastAPI's in-process `BackgroundTasks` -- purely in-memory; a
     crash after the HTTP response is sent, but before or during the
     background task, loses the work with no record it was ever
     supposed to happen.
  2. `POST /discovery/search` and `POST /leads/{id}/process` `await`
     `run_lead_pipeline` synchronously inside the request handler -- a
     crash mid-request loses whatever hadn't finished, and the caller
     gets a bare connection failure with no way to know which leads (if
     any) still need processing.

`celery`/`redis` are listed dependencies, but `core/infrastructure/
workers/orchestrator.py`'s Celery task is dead code (never imported by
`main.py` or any endpoint) and reimplements an *old* pipeline that
bypasses Company Intelligence/Decision/Evaluation/Review entirely --
resurrecting it would mean rewriting its task body to call the current
LangGraph pipeline, which is a bigger, riskier change than adding one
small, additive table on the database this system already depends on
for every other durability guarantee (see
core/domain/models/pipeline_lock.py, core/domain/models/billing.py's
DailyLeadQuotaUsage). Redis itself is used for nothing but a health-check
ping in main.py. Per this task's own instruction ("if the existing
Celery path is legacy/broken and cannot be safely reused: implement the
smallest PostgreSQL-backed durable job mechanism"), this table is that
mechanism.

This is NOT a generic job-queue framework. It has exactly one executor
(`application.execution.job_executor`), which does exactly one thing:
call `application.workflows.lead_pipeline.run_lead_pipeline(lead_id)`
and record the outcome. `job_type` exists purely for observability
labeling (which call site created this job), not for dispatch to
different executors.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from core.infrastructure.database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)

    # "LEAD_PIPELINE" (from POST /leads, POST /leads/single) |
    # "DISCOVERY" (from a discovery-created lead) |
    # "REPROCESS_LEAD" (from POST /leads/{id}/process). Observability
    # labeling only -- every job type is executed identically.
    job_type = Column(String, nullable=False, index=True)

    # PENDING -> CLAIMED -> RUNNING -> SUCCEEDED
    #                               -> RETRY_WAIT -> (back to PENDING once available_at elapses)
    #                               -> FAILED (terminal, attempts exhausted or non-retryable)
    # any of PENDING/CLAIMED/RUNNING -> CANCELLED (not currently set by
    # any code path, reserved for a future explicit-cancel API)
    status = Column(String, nullable=False, default="PENDING", index=True)

    # Set once this job's pipeline execution actually starts, so it can
    # be correlated with AIDecisionLog/PipelineExecutionRecord rows for
    # the same run (see application/execution/job_executor.py).
    pipeline_id = Column(String, nullable=True, index=True)

    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)

    # A PENDING job is only claimable once available_at <= now(); used
    # both for initial scheduling and for retry backoff delay.
    available_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Lease/ownership for crash recovery (P0-A5): a CLAIMED/RUNNING job
    # whose lease_expires_at has passed is reclaimed (flipped back to
    # PENDING or, if attempts are exhausted, FAILED) by the next worker
    # poll -- see application/execution/job_repository.py::reclaim_expired_leases.
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    worker_id = Column(String, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    last_error = Column(Text, nullable=True)
    # One of application.execution.job_types.FailureCategory's values --
    # e.g. VALIDATION_ERROR, TIMEOUT, TRANSIENT_DATABASE,
    # ALREADY_IN_PROGRESS, PERMANENT_PROVIDER_ERROR.
    last_error_category = Column(String, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
