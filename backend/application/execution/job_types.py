"""
Job lifecycle enums and the deterministic retry classifier -- P0-A6.

This is deliberately NOT a generic retry framework: it is one small
function that inspects the one shape of result this system's one
executor ever produces (`application.workflows.lead_pipeline.
run_lead_pipeline`'s return dict) and decides retryable-or-not plus a
category, reusing the pipeline's own existing graceful-degradation
signals (PipelineStatus, error messages already produced by
LeadPipeline.execute) rather than inventing new failure taxonomy the
pipeline doesn't already surface.

Evidence this is grounded in the actual pipeline (see
application/workflows/lead_pipeline.py::LeadPipeline.execute):
  - run_lead_pipeline() never raises -- it always returns a dict with a
    "status" key. Every job-level exception this module has to handle is
    therefore either (a) a bug in the job/DB bookkeeping code itself, or
    (b) the two documented FAILED-producing branches inside execute():
    "Lead not found" (permanent -- retrying can't make a lead exist) and
    an unhandled graph-runtime exception (str(e) is all that's captured).
  - PipelineStatus.SKIPPED_IN_PROGRESS (added in the Phase A3/A4
    hardening pass) means the per-lead pipeline lock is already held by
    another execution -- not a failure at all, just contention; safe and
    correct to retry shortly.
"""

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, Tuple


class JobType(str, Enum):
    LEAD_PIPELINE = "LEAD_PIPELINE"
    DISCOVERY = "DISCOVERY"
    REPROCESS_LEAD = "REPROCESS_LEAD"


class JobStatus(str, Enum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Valid transitions -- documented per A3's instruction ("document valid
# transitions. Only one component should own transitions" --
# application.execution.job_repository is that one component; nothing
# else should ever write Job.status directly).
VALID_TRANSITIONS = {
    JobStatus.PENDING: {JobStatus.CLAIMED, JobStatus.CANCELLED},
    JobStatus.CLAIMED: {JobStatus.RUNNING, JobStatus.PENDING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {
        JobStatus.SUCCEEDED,
        JobStatus.RETRY_WAIT,
        JobStatus.FAILED,
        JobStatus.PENDING,  # lease-expiry reclaim
        JobStatus.CANCELLED,
    },
    JobStatus.RETRY_WAIT: {JobStatus.PENDING},
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
    JobStatus.CANCELLED: set(),
}


class FailureCategory(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHORIZATION_ERROR = "AUTHORIZATION_ERROR"
    RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    TIMEOUT = "TIMEOUT"
    TRANSIENT_DATABASE = "TRANSIENT_DATABASE"
    PERMANENT_PROVIDER_ERROR = "PERMANENT_PROVIDER_ERROR"
    BUSINESS_REJECTION = "BUSINESS_REJECTION"
    # Not in the task's illustrative list, but grounded in a real,
    # already-existing pipeline signal (PipelineStatus.SKIPPED_IN_PROGRESS,
    # Phase A3/A4's per-lead lock) rather than invented.
    ALREADY_IN_PROGRESS = "ALREADY_IN_PROGRESS"
    UNKNOWN = "UNKNOWN"


RETRYABLE_CATEGORIES = {
    FailureCategory.RESOURCE_EXHAUSTED,
    FailureCategory.PROVIDER_UNAVAILABLE,
    FailureCategory.TIMEOUT,
    FailureCategory.TRANSIENT_DATABASE,
    FailureCategory.ALREADY_IN_PROGRESS,
    FailureCategory.PERMANENT_PROVIDER_ERROR,  # retried, but capped low by max_attempts
    FailureCategory.UNKNOWN,  # retried defensively, capped by max_attempts
}
# Never retried, regardless of attempts remaining.
NON_RETRYABLE_CATEGORIES = {
    FailureCategory.VALIDATION_ERROR,
    FailureCategory.AUTHORIZATION_ERROR,
    FailureCategory.BUSINESS_REJECTION,
}

_TRANSIENT_SIGNATURES = (
    ("timeout", FailureCategory.TIMEOUT),
    ("timed out", FailureCategory.TIMEOUT),
    ("connection", FailureCategory.TRANSIENT_DATABASE),
    ("operationalerror", FailureCategory.TRANSIENT_DATABASE),
    ("database is locked", FailureCategory.TRANSIENT_DATABASE),
    ("429", FailureCategory.RESOURCE_EXHAUSTED),
    ("rate limit", FailureCategory.RESOURCE_EXHAUSTED),
    ("too many requests", FailureCategory.RESOURCE_EXHAUSTED),
    ("unavailable", FailureCategory.PROVIDER_UNAVAILABLE),
    ("503", FailureCategory.PROVIDER_UNAVAILABLE),
)


def classify_pipeline_result(result: Dict[str, Any]) -> Tuple[bool, FailureCategory, bool]:
    """
    Classifies a `run_lead_pipeline()` result dict.

    Returns (succeeded, category, retryable):
      - succeeded=True for PipelineStatus.SUCCESS/PARTIAL_SUCCESS -- the
        job's unit of work ("attempt to process this lead") completed;
        category is UNKNOWN and unused in this case.
      - succeeded=False otherwise, with the failure category and whether
        it's retryable.
    """
    status = (result or {}).get("status")

    if status in ("SUCCESS", "PARTIAL_SUCCESS"):
        return True, FailureCategory.UNKNOWN, False

    if status == "SKIPPED_IN_PROGRESS":
        return False, FailureCategory.ALREADY_IN_PROGRESS, True

    # status == "FAILED", or missing/unexpected shape entirely.
    errors = (result or {}).get("errors") or []
    combined_message = " ".join(str(e.get("error", "")) for e in errors if isinstance(e, dict)).lower()

    if "lead not found" in combined_message:
        return False, FailureCategory.VALIDATION_ERROR, False

    for signature, category in _TRANSIENT_SIGNATURES:
        if signature in combined_message:
            return False, category, True

    if not combined_message:
        # FAILED with no error detail at all, or a result shape we don't
        # recognize -- treat conservatively as retryable-but-unknown
        # rather than silently swallowing it as permanent.
        return False, FailureCategory.UNKNOWN, True

    return False, FailureCategory.PERMANENT_PROVIDER_ERROR, True


def classify_exception(exc: BaseException) -> Tuple[FailureCategory, bool]:
    """Classifies an exception raised outside run_lead_pipeline's own
    catch-all (e.g. a DB error while the job executor itself is doing
    bookkeeping). run_lead_pipeline is proven not to raise for ordinary
    pipeline failures (see this module's docstring), so reaching this
    path at all means something below the pipeline's own graceful
    degradation broke."""
    module = type(exc).__module__ or ""
    name = type(exc).__name__

    if "sqlalchemy" in module or name in ("OperationalError", "DBAPIError", "IntegrityError"):
        # IntegrityError specifically is usually NOT retryable (e.g. a
        # genuine constraint violation), but here it's grouped with
        # transient DB errors deliberately: the only IntegrityErrors this
        # executor could plausibly hit are already handled inline (see
        # job_repository's atomic claim/reclaim), so one reaching this
        # far is far more likely a transient contention artifact than a
        # real data-integrity bug.
        return FailureCategory.TRANSIENT_DATABASE, True

    message = str(exc).lower()
    for signature, category in _TRANSIENT_SIGNATURES:
        if signature in message:
            return category, True

    return FailureCategory.UNKNOWN, True


def compute_backoff(attempt_count: int, base_seconds: int = 5, max_seconds: int = 300) -> datetime:
    """Simple, explicit exponential backoff -- not a generic policy
    engine, just enough to avoid hot-looping a job that keeps failing."""
    delay = min(base_seconds * (2 ** max(0, attempt_count - 1)), max_seconds)
    return datetime.now(timezone.utc) + timedelta(seconds=delay)
