"""
Per-lead pipeline mutual-exclusion lock.

Production-robustness hardening, Phase A3/A4 (correctness under
retries / duplicate submissions -- see core.domain.models.pipeline_lock
for why this table exists).

Used by exactly one call site: `application.workflows.lead_pipeline`,
which is itself the single chokepoint every pipeline execution already
passes through (fresh-lead background processing from api/endpoints/
leads.py, discovery's per-business processing in
application/discovery/discovery_service.py, and the manual
`POST /leads/{id}/process` trigger) -- so guarding it there protects all
of them without touching any of their call sites.

Behavior:
  - `try_acquire` inserts a row for `lead_id`. If a row already exists
    (another execution is genuinely in flight for this same lead right
    now), the UNIQUE constraint raises IntegrityError and this returns
    False -- the caller must not run the pipeline again and should
    report the already-running `pipeline_id` back to the caller instead.
  - `release` deletes the row unconditionally once a run ends (success
    or failure), so a *subsequent, non-concurrent* call to reprocess the
    same lead -- the actual intended behavior of "manually trigger
    processing for a lead" -- is unaffected: the lock only ever blocks
    genuine overlap, never a later, separate request.
  - A stale lock (the owning process died between acquiring the lock and
    releasing it -- e.g. a hard crash/OOM kill, not a normal exception,
    since normal exceptions are already caught inside `finally`) is
    reclaimed automatically after `PIPELINE_LOCK_STALE_SECONDS`, so a
    single crash can never permanently strand a lead in "processing"
    forever.
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.domain.models.pipeline_lock import ActivePipelineLock
from core.infrastructure.logging import get_logger

logger = get_logger("application.concurrency.pipeline_lock")


def _stale_cutoff() -> datetime:
    stale_seconds = max(60, int(os.getenv("PIPELINE_LOCK_STALE_SECONDS", "1800")))
    return datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)


def _reclaim_if_stale(db: Session, lead_id: int) -> None:
    """Best-effort: deletes an existing lock row for `lead_id` only if it
    is older than the stale cutoff. A failure here must never block a
    pipeline run, so any error is logged and swallowed -- worst case, a
    stale lock simply isn't reclaimed on this particular attempt."""
    try:
        db.query(ActivePipelineLock).filter(
            ActivePipelineLock.lead_id == lead_id,
            ActivePipelineLock.acquired_at < _stale_cutoff(),
        ).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.debug(f"Stale pipeline-lock reclaim check failed for lead {lead_id}: {e}")


def try_acquire(db: Session, lead_id: int, organization_id: int, pipeline_id: str) -> bool:
    """Attempts to acquire the per-lead pipeline lock. Returns True if
    acquired (caller may proceed), False if another execution already
    holds it (caller must not run the pipeline)."""
    _reclaim_if_stale(db, lead_id)

    db.add(
        ActivePipelineLock(
            lead_id=lead_id, organization_id=organization_id, pipeline_id=pipeline_id
        )
    )
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def get_active(db: Session, lead_id: int) -> Optional[ActivePipelineLock]:
    """Returns the current lock row for `lead_id`, if any (e.g. so a
    caller whose `try_acquire` failed can report the in-flight
    pipeline_id back to the client)."""
    return db.query(ActivePipelineLock).filter(ActivePipelineLock.lead_id == lead_id).first()


def release(db: Session, lead_id: int) -> None:
    """Releases the lock for `lead_id`, if held. Must be called from a
    `finally` block around every pipeline execution so a normal
    completion (success OR failure) never leaves the lock stuck -- only
    an actual process crash should ever require the stale-lock reclaim
    path above."""
    try:
        db.query(ActivePipelineLock).filter(ActivePipelineLock.lead_id == lead_id).delete()
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to release pipeline lock for lead {lead_id}: {e}")
