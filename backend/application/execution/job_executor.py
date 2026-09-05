"""
Job executor -- P0-A (durable job execution).

The one place that turns a claimed Job into an actual pipeline run. Does
NOT reimplement or wrap the pipeline in any way -- it calls the exact
same `application.workflows.lead_pipeline.run_lead_pipeline(lead_id)`
every existing call site already used before this change, so a job's
outcome is byte-for-byte the same PipelineResult a direct call would have
produced. This module only adds durable bookkeeping around that call.
"""

from typing import Any, Callable, Coroutine, Dict, Optional
import uuid

from sqlalchemy.orm import Session

from application.execution import job_repository
from application.execution.job_types import classify_exception, classify_pipeline_result, compute_backoff
from application.workflows.lead_pipeline import run_lead_pipeline
from core.infrastructure.database import SessionLocal
from core.infrastructure.logging import get_logger

logger = get_logger("application.execution.job_executor")

PipelineRunner = Callable[[int], Coroutine[Any, Any, Dict[str, Any]]]


async def execute_job(
    job_id: int, worker_id: str, pipeline_runner: Optional[PipelineRunner] = None
) -> Optional[Dict[str, Any]]:
    """Executes exactly one job by id, using its own fresh DB session
    (safe to call from a background worker loop or synchronously inline
    within a request handler -- both call this the same way). No-ops
    silently if the job is not actually CLAIMED by this worker_id (e.g.
    its lease was already reclaimed by someone else): this is the
    expected, safe outcome of losing a race, not an error.

    `pipeline_runner` defaults to the real `run_lead_pipeline` and should
    only ever be overridden by a caller that needs to preserve its own
    existing, independently-mockable reference to it -- see
    `application/discovery/discovery_service.py`, whose own tests
    monkeypatch `discovery_service.run_lead_pipeline` directly (predating
    this module) and would otherwise silently stop being mocked if
    execution always went through this module's own imported copy of the
    function instead. This is not a general plugin system; it exists
    solely to avoid breaking that one existing test-mocking convention.

    Returns the raw `run_lead_pipeline()` result dict when the pipeline
    actually ran (regardless of its outcome), so a synchronous caller
    (e.g. POST /leads/{id}/process, which must keep returning the full
    PipelineResult in its response body -- see api/endpoints/leads.py)
    can use it directly. Returns None when the pipeline never ran at all
    (job not found, claim/lease lost, or an exception below the
    pipeline's own graceful degradation) -- callers that need a result
    dict regardless should treat None the same as a FAILED status.
    """
    runner = pipeline_runner or run_lead_pipeline
    db: Session = SessionLocal()
    try:
        job = job_repository.get_job(db, job_id)
        if job is None:
            logger.warning(f"execute_job: job {job_id} does not exist")
            return None

        if not job_repository.mark_running(db, job_id, worker_id):
            logger.info(
                f"execute_job: job {job_id} could not be marked RUNNING "
                f"(claim likely lost to another worker or lease reclaim) -- skipping"
            )
            return None

        try:
            result = await runner(job.lead_id)
        except Exception as exc:  # noqa: BLE001 -- see module docstring: run_lead_pipeline
            # is proven not to raise for ordinary pipeline failures, so
            # reaching this branch means something below the pipeline's
            # own graceful degradation broke (e.g. a DB outage while
            # opening the pipeline's own SessionLocal).
            logger.error(f"execute_job: job {job_id} raised unexpectedly: {exc}", exc_info=True)
            category, retryable = classify_exception(exc)
            _finalize_failure(db, job, worker_id, error=str(exc), category=category.value, retryable=retryable)
            return None

        pipeline_id = result.get("pipeline_id")
        succeeded, category, retryable = classify_pipeline_result(result)

        if succeeded:
            job_repository.mark_succeeded(db, job_id, worker_id, pipeline_id=pipeline_id)
            logger.info(f"execute_job: job {job_id} (lead {job.lead_id}) succeeded")
            return result

        errors = result.get("errors") or []
        error_text = "; ".join(str(e.get("error", e)) for e in errors) or f"pipeline status={result.get('status')}"
        _finalize_failure(
            db, job, worker_id, error=error_text, category=category.value, retryable=retryable, pipeline_id=pipeline_id
        )
        return result
    finally:
        db.close()


def _finalize_failure(
    db: Session,
    job,
    worker_id: str,
    *,
    error: str,
    category: str,
    retryable: bool,
    pipeline_id=None,
) -> None:
    attempts_remaining = retryable and job.attempt_count < job.max_attempts
    if attempts_remaining:
        available_at = compute_backoff(job.attempt_count)
        applied = job_repository.mark_retry_wait(
            db, job.id, worker_id, available_at=available_at, error=error, category=category, pipeline_id=pipeline_id
        )
        if applied:
            logger.warning(
                f"execute_job: job {job.id} failed (category={category}), "
                f"retrying at {available_at.isoformat()} (attempt {job.attempt_count}/{job.max_attempts})"
            )
        else:
            # Lost the RUNNING->RETRY_WAIT transition -- most likely this
            # job's lease was already reclaimed by another worker (see
            # job_repository.reclaim_expired_leases) between execute_job
            # starting and finishing. Not an error: the reclaiming worker
            # now owns whatever happens to this job next.
            logger.info(
                f"execute_job: job {job.id} finished with a retryable failure, but its "
                f"RUNNING state was already taken over by another worker/reclaim -- not "
                f"applying our own retry transition"
            )
    else:
        applied = job_repository.mark_failed(db, job.id, worker_id, error=error, category=category, pipeline_id=pipeline_id)
        if applied:
            logger.error(
                f"execute_job: job {job.id} FAILED permanently (category={category}, "
                f"retryable={retryable}, attempts={job.attempt_count}/{job.max_attempts})"
            )
        else:
            logger.info(
                f"execute_job: job {job.id} reached a terminal failure, but its RUNNING "
                f"state was already taken over by another worker/reclaim -- not applying "
                f"our own failure transition"
            )


def new_worker_id() -> str:
    return f"worker-{uuid.uuid4().hex[:12]}"


async def create_and_run_inline(
    *, organization_id: int, lead_id: int, job_type, pipeline_runner: Optional[PipelineRunner] = None
) -> Dict[str, Any]:
    """For call sites that must keep their existing SYNCHRONOUS response
    contract (POST /leads/{id}/process, discovery's per-business
    pipeline loop -- see A7: "if existing synchronous /process behavior
    is intentionally relied upon, preserve it") while still gaining
    durable bookkeeping: creates a Job, claims and runs it immediately
    within the same call (no polling wait -- the caller IS the worker
    for this one job), and returns the actual pipeline result dict so
    the caller's response body is unchanged.

    `pipeline_runner` is forwarded to execute_job -- see that function's
    docstring for why it exists (preserving discovery_service.py's own
    pre-existing, independently-mockable `run_lead_pipeline` reference).

    Deliberately takes no `db` parameter and always opens its own fresh,
    short-lived session (matching execute_job's own pattern) rather than
    accepting a caller-supplied session: discovery's per-business loop
    (application/discovery/discovery_service.py::_run_pipelines) calls
    this concurrently (bounded by a semaphore) from multiple coroutines
    that would otherwise all share that service's single long-lived
    `self.db` Session -- a SQLAlchemy Session is not safe to drive from
    overlapping concurrent calls, and an earlier version of this
    function that accepted the caller's session caused exactly that
    (found via a hanging/serializing test run, not by inspection alone).

    This does not make these endpoints "truly asynchronous" -- if the
    process crashes mid-execution, the Job row still shows
    CLAIMED/RUNNING with a lease that the background worker loop will
    eventually reclaim (see job_repository.reclaim_expired_leases /
    worker_loop.py), giving the same crash-recovery safety net as the
    fire-and-forget job path, without changing this endpoint's contract
    at all.
    """
    worker_id = new_worker_id()
    db = SessionLocal()
    try:
        job = job_repository.create_job(
            db, organization_id=organization_id, lead_id=lead_id, job_type=job_type
        )
        if not job_repository.try_claim(db, job.id, worker_id):
            # Cannot happen in practice (we just created this row and no
            # one else knows its id yet), but never silently pretend
            # success.
            logger.error(f"create_and_run_inline: failed to claim freshly-created job {job.id}")
            return {
                "status": "FAILED",
                "lead_id": lead_id,
                "errors": [{"stage": "job", "error": "Failed to claim freshly-created job"}],
            }
        job_id = job.id
    finally:
        db.close()

    result = await execute_job(job_id, worker_id, pipeline_runner=pipeline_runner)
    if result is None:
        return {
            "status": "FAILED",
            "lead_id": lead_id,
            "errors": [{"stage": "job", "error": "Job execution did not return a result"}],
        }
    return result
