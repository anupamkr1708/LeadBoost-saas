"""
Job repository -- P0-A4/A5 (concurrency-safe claiming, lease-based crash
recovery).

Every state-changing function here uses the SAME proven pattern as the
Phase A/B hardening pass (core/infrastructure/billing/quota_guard.py,
application/concurrency/pipeline_lock.py): a single conditional
`UPDATE ... WHERE <still-in-expected-state>` statement, checked via
`rowcount`, rather than `SELECT ... FOR UPDATE` / `SKIP LOCKED`. This is
deliberate, not an oversight: FOR UPDATE/SKIP LOCKED semantics differ
between SQLite (used in this test suite and local dev) and PostgreSQL
(production), whereas a single UPDATE's WHERE-evaluate-and-SET is atomic
identically on both -- exactly the reasoning already validated by the
Phase A/B quota-reservation tests (20 real concurrent threads racing for
a limit of 10, exactly 10 succeeded).

This module is the ONLY place that writes Job.status -- see
job_types.VALID_TRANSITIONS.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from application.execution.job_types import JobStatus, JobType
from core.domain.models.job import Job
from core.infrastructure.logging import get_logger

logger = get_logger("application.execution.job_repository")

DEFAULT_LEASE_SECONDS = 300  # 5 minutes -- comfortably longer than any
# single lead pipeline run observed in this system's own runtime
# evidence (p95 processing time ~85s per the metrics audit), with
# headroom for a slow real-LLM call.


def create_job(
    db: Session,
    *,
    organization_id: int,
    lead_id: int,
    job_type: JobType,
    max_attempts: int = 3,
) -> Job:
    job = Job(
        organization_id=organization_id,
        lead_id=lead_id,
        job_type=job_type.value if isinstance(job_type, JobType) else job_type,
        status=JobStatus.PENDING.value,
        max_attempts=max_attempts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def list_claimable_job_ids(db: Session, limit: int = 10) -> List[int]:
    """Plain, unlocked read of candidate PENDING jobs whose available_at
    has elapsed. Not itself a claim -- see try_claim."""
    now = datetime.now(timezone.utc)
    rows = (
        db.query(Job.id)
        .filter(Job.status == JobStatus.PENDING.value, Job.available_at <= now)
        .order_by(Job.available_at.asc())
        .limit(limit)
        .all()
    )
    return [r[0] for r in rows]


def try_claim(db: Session, job_id: int, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> bool:
    """Atomically claims job_id if (and only if) it is still PENDING.
    Returns True iff this call won the claim."""
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.PENDING.value)
        .values(
            status=JobStatus.CLAIMED.value,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            worker_id=worker_id,
            attempt_count=Job.attempt_count + 1,
        )
    )
    db.commit()
    return result.rowcount == 1


def mark_running(db: Session, job_id: int, worker_id: str, pipeline_id: Optional[str] = None) -> bool:
    """CLAIMED -> RUNNING. Only succeeds if this worker still holds the
    claim (worker_id match) -- guards against a stale caller acting on a
    job whose lease has already been reclaimed by someone else.
    `pipeline_id` is optional here because run_lead_pipeline() generates
    it internally and only returns it once the run finishes (or fails) --
    see the finalize functions below, which always set it."""
    values = {"status": JobStatus.RUNNING.value, "started_at": datetime.now(timezone.utc)}
    if pipeline_id is not None:
        values["pipeline_id"] = pipeline_id
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == JobStatus.CLAIMED.value, Job.worker_id == worker_id)
        .values(**values)
    )
    db.commit()
    return result.rowcount == 1


def mark_succeeded(db: Session, job_id: int, worker_id: str, pipeline_id: Optional[str] = None) -> bool:
    values = {"status": JobStatus.SUCCEEDED.value, "completed_at": datetime.now(timezone.utc)}
    if pipeline_id is not None:
        values["pipeline_id"] = pipeline_id
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.worker_id == worker_id, Job.status == JobStatus.RUNNING.value)
        .values(**values)
    )
    db.commit()
    return result.rowcount == 1


def mark_retry_wait(
    db: Session,
    job_id: int,
    worker_id: str,
    *,
    available_at: datetime,
    error: str,
    category: str,
    pipeline_id: Optional[str] = None,
) -> bool:
    values = {
        "status": JobStatus.RETRY_WAIT.value,
        "available_at": available_at,
        "last_error": error[:4000] if error else None,
        "last_error_category": category,
    }
    if pipeline_id is not None:
        values["pipeline_id"] = pipeline_id
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.worker_id == worker_id, Job.status == JobStatus.RUNNING.value)
        .values(**values)
    )
    db.commit()
    if result.rowcount == 1:
        # RETRY_WAIT is a transient marker, not a place a job sits
        # indefinitely -- immediately flip it back to PENDING so the next
        # poll can claim it once available_at elapses (its own WHERE
        # clause on available_at already prevents a premature claim).
        db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.RETRY_WAIT.value)
            .values(status=JobStatus.PENDING.value)
        )
        db.commit()
        return True
    return False


def mark_failed(
    db: Session, job_id: int, worker_id: str, *, error: str, category: str, pipeline_id: Optional[str] = None
) -> bool:
    values = {
        "status": JobStatus.FAILED.value,
        "completed_at": datetime.now(timezone.utc),
        "last_error": error[:4000] if error else None,
        "last_error_category": category,
    }
    if pipeline_id is not None:
        values["pipeline_id"] = pipeline_id
    result = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.worker_id == worker_id, Job.status == JobStatus.RUNNING.value)
        .values(**values)
    )
    db.commit()
    return result.rowcount == 1


def reclaim_expired_leases(db: Session, limit: int = 50) -> List[int]:
    """P0-A5/B4: finds CLAIMED/RUNNING jobs whose lease has expired --
    the owning worker crashed (or was killed) between claiming and
    finishing -- and atomically flips each back to PENDING (if attempts
    remain) or FAILED (if exhausted), one row at a time via the same
    conditional-UPDATE pattern, so a worker that is in fact still alive
    and finishes at the exact same moment simply loses the race safely
    (its own mark_succeeded/mark_failed's WHERE clause no longer matches
    once we've moved the row out of CLAIMED/RUNNING, and vice versa).
    Returns the ids of jobs that were reclaimed to PENDING (i.e. now
    retryable) for observability/testing."""
    now = datetime.now(timezone.utc)
    candidates = (
        db.query(Job.id, Job.attempt_count, Job.max_attempts)
        .filter(
            Job.status.in_([JobStatus.CLAIMED.value, JobStatus.RUNNING.value]),
            Job.lease_expires_at.isnot(None),
            Job.lease_expires_at < now,
        )
        .limit(limit)
        .all()
    )

    reclaimed_to_pending: List[int] = []
    for job_id, attempt_count, max_attempts in candidates:
        if attempt_count >= max_attempts:
            result = db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.in_([JobStatus.CLAIMED.value, JobStatus.RUNNING.value]),
                    Job.lease_expires_at < now,
                )
                .values(
                    status=JobStatus.FAILED.value,
                    completed_at=now,
                    last_error="Lease expired and attempts exhausted (worker likely crashed)",
                    last_error_category="TIMEOUT",
                )
            )
            db.commit()
            if result.rowcount == 1:
                logger.warning(f"Job {job_id} FAILED permanently after lease expiry with attempts exhausted")
        else:
            result = db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.in_([JobStatus.CLAIMED.value, JobStatus.RUNNING.value]),
                    Job.lease_expires_at < now,
                )
                .values(
                    status=JobStatus.PENDING.value,
                    claimed_at=None,
                    lease_expires_at=None,
                    worker_id=None,
                    available_at=now,
                )
            )
            db.commit()
            if result.rowcount == 1:
                reclaimed_to_pending.append(job_id)
                logger.info(f"Job {job_id} reclaimed to PENDING after lease expiry (worker likely crashed)")

    return reclaimed_to_pending


def get_job(db: Session, job_id: int) -> Optional[Job]:
    return db.query(Job).filter(Job.id == job_id).first()
