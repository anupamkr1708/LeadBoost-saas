"""
Background job worker loop -- P0-A (durable job execution).

Deliberately NOT a separate deployment/process/queue technology: this is
one small asyncio task, started from the SAME FastAPI process via
main.py's existing `lifespan` (see D1/D2 of the P0 spec: "do not
introduce distributed infrastructure... do not introduce a distributed
queue framework solely for this task"). It polls the `jobs` table
(core/domain/models/job.py) for claimable work and executes it through
`application.execution.job_executor.execute_job`, which calls the exact
same `run_lead_pipeline` every existing call site already used.

Why this is enough for genuine crash recovery without a separate worker
fleet: if the whole process dies, every PENDING job is still sitting in
PostgreSQL and gets picked up again the moment ANY process (the same one
restarted, or a different uvicorn worker process in a multi-process
deployment) starts this same loop -- the atomic claim in job_repository
ensures two processes running this loop concurrently can never
double-claim the same row, so this scales to multiple processes "for
free" without any new infrastructure, exactly matching the task's own
architecture diagram (API -> Durable Job -> Worker Runtime -> Existing
Pipeline).
"""

import asyncio
import os

from application.execution import job_executor, job_repository
from core.infrastructure.database import SessionLocal
from core.infrastructure.logging import get_logger

logger = get_logger("application.execution.worker_loop")

_worker_task: "asyncio.Task | None" = None
_stop_event: "asyncio.Event | None" = None


def _poll_interval_seconds() -> float:
    return max(0.5, float(os.getenv("JOB_POLL_INTERVAL_SECONDS", "2")))


def _worker_concurrency() -> int:
    return max(1, int(os.getenv("JOB_WORKER_CONCURRENCY", "3")))


def _worker_enabled() -> bool:
    return os.getenv("JOB_WORKER_ENABLED", "true").lower() not in ("false", "0", "no")


async def _run_one_poll_cycle(worker_id: str, semaphore: asyncio.Semaphore) -> None:
    # Lease reclaim runs every cycle, cheaply, on its own short-lived
    # session -- see job_repository.reclaim_expired_leases for why this
    # is safe to run concurrently with any in-flight job.
    db = SessionLocal()
    try:
        job_repository.reclaim_expired_leases(db)
    except Exception as e:
        logger.error(f"worker_loop: lease reclaim pass failed: {e}", exc_info=True)
    finally:
        db.close()

    db = SessionLocal()
    try:
        candidate_ids = job_repository.list_claimable_job_ids(db, limit=_worker_concurrency() * 2)
    except Exception as e:
        logger.error(f"worker_loop: listing claimable jobs failed: {e}", exc_info=True)
        return
    finally:
        db.close()

    async def _claim_and_run(job_id: int) -> None:
        async with semaphore:
            claim_db = SessionLocal()
            try:
                claimed = job_repository.try_claim(claim_db, job_id, worker_id)
            finally:
                claim_db.close()
            if not claimed:
                return  # lost the race to another worker -- expected, not an error
            await job_executor.execute_job(job_id, worker_id)

    await asyncio.gather(*(_claim_and_run(jid) for jid in candidate_ids), return_exceptions=True)


async def _loop(worker_id: str, stop_event: asyncio.Event) -> None:
    semaphore = asyncio.Semaphore(_worker_concurrency())
    logger.info(f"worker_loop: started ({worker_id}), poll_interval={_poll_interval_seconds()}s")
    while not stop_event.is_set():
        try:
            await _run_one_poll_cycle(worker_id, semaphore)
        except Exception as e:
            logger.error(f"worker_loop: poll cycle crashed: {e}", exc_info=True)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=_poll_interval_seconds())
        except asyncio.TimeoutError:
            pass
    logger.info(f"worker_loop: stopped ({worker_id})")


def start() -> None:
    """Starts the background poll loop as an asyncio task on the current
    event loop. Safe to call even when JOB_WORKER_ENABLED=false (no-op) --
    useful for tests that want durable job *creation* without a
    background loop racing the test's own assertions."""
    global _worker_task, _stop_event
    if not _worker_enabled():
        logger.info("worker_loop: JOB_WORKER_ENABLED is false -- not starting")
        return
    if _worker_task is not None and not _worker_task.done():
        return
    _stop_event = asyncio.Event()
    _worker_task = asyncio.create_task(_loop(job_executor.new_worker_id(), _stop_event))


async def stop() -> None:
    global _worker_task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _worker_task is not None:
        try:
            await asyncio.wait_for(_worker_task, timeout=10)
        except asyncio.TimeoutError:
            _worker_task.cancel()
    _worker_task = None
    _stop_event = None
