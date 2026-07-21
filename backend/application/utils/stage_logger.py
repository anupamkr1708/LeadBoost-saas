"""
Stage timing/logging helper.

Wraps every workflow stage with consistent structured logging (stage name,
start timestamp, end timestamp, duration, success/failure, retry count) by
reusing the existing core.infrastructure.logging module rather than
introducing a second logging system.

Retry count is explicit, caller-supplied data (via `StageTimer.retry_count`,
settable from inside the `with` block) rather than implicit/global state:
a stage that calls an LLM-backed agent sets `timer.retry_count` from the
agent's own reported retry count (see application.services.llm_provider)
before the block exits. This avoids relying on contextvars or thread-locals,
which do not reliably propagate across the `asyncio.to_thread` boundary
used to run blocking LLM calls off the event loop.
"""

import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional

from core.infrastructure.logging import get_logger

logger = get_logger("application.workflow")


class StageTimer:
    """Mutable holder so the caller can read timing/outcome data afterwards,
    and optionally report a retry count observed during the stage."""

    def __init__(self):
        self.duration_ms: int = 0
        self.error: Optional[str] = None
        self.retry_count: int = 0
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None


@contextmanager
def stage_span(
    stage_name: str,
    lead_id: Optional[int] = None,
    pipeline_id: Optional[str] = None,
    **extra: Any,
) -> Generator[StageTimer, None, None]:
    """
    Usage:
        with stage_span("scrape", lead_id=lead.id, pipeline_id=pid) as timer:
            ... do work ...
            timer.retry_count = 2  # optional, only if the stage retried something
        # timer.duration_ms / timer.retry_count are now populated
    """
    timer = StageTimer()
    start = time.time()
    start_dt = datetime.now(timezone.utc)
    timer.started_at = start_dt.isoformat()

    logger.info(
        f"Stage '{stage_name}' started",
        extra={
            "event": "stage_start",
            "stage": stage_name,
            "lead_id": lead_id,
            "pipeline_id": pipeline_id,
            "started_at": timer.started_at,
            **extra,
        },
    )
    try:
        yield timer
    except Exception as e:
        end_dt = datetime.now(timezone.utc)
        timer.error = str(e)
        timer.duration_ms = int((time.time() - start) * 1000)
        timer.completed_at = end_dt.isoformat()
        logger.error(
            f"Stage '{stage_name}' failed: {e}",
            exc_info=True,
            extra={
                "event": "stage_failed",
                "stage": stage_name,
                "lead_id": lead_id,
                "pipeline_id": pipeline_id,
                "started_at": timer.started_at,
                "completed_at": timer.completed_at,
                "duration_ms": timer.duration_ms,
                "success": False,
                "retry_count": timer.retry_count,
                **extra,
            },
        )
        raise
    else:
        end_dt = datetime.now(timezone.utc)
        timer.duration_ms = int((time.time() - start) * 1000)
        timer.completed_at = end_dt.isoformat()
        logger.info(
            f"Stage '{stage_name}' completed",
            extra={
                "event": "stage_complete",
                "stage": stage_name,
                "lead_id": lead_id,
                "pipeline_id": pipeline_id,
                "started_at": timer.started_at,
                "completed_at": timer.completed_at,
                "duration_ms": timer.duration_ms,
                "success": True,
                "retry_count": timer.retry_count,
                **extra,
            },
        )


def record_timing(state_timings: Dict[str, int], stage_name: str, timer: StageTimer) -> None:
    state_timings[stage_name] = timer.duration_ms
