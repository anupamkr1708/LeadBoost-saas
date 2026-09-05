"""
P0-A (durable job execution) regression tests.

Concurrency is exercised with real OS threads and independent DB
sessions against the same file-based SQLite database used by the rest of
this suite (see tests/application/conftest.py) -- the same discipline
established in tests/application/test_production_robustness.py -- since
that is what actually proves atomicity at the database level, not two
sequential calls against one shared session.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from application.execution import job_executor, job_repository
from application.execution.job_types import (
    FailureCategory,
    JobStatus,
    JobType,
    classify_exception,
    classify_pipeline_result,
    compute_backoff,
)
from application.services import infra_adapters
from core.domain.models.job import Job
from core.infrastructure.database import SessionLocal


@dataclass
class _FakeScrapingResult:
    success: bool
    data: Dict[str, Any]
    confidence: float = 0.8
    error_message: Optional[str] = None
    processing_time: float = 0.05
    pages_scraped: int = 1
    blocked_detected: bool = False
    tiers_attempted: List[str] = field(default_factory=list)


@pytest.fixture()
def mock_successful_scrape(monkeypatch):
    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(
            success=True,
            data={"title": "Acme Robotics", "text_content": "We build industrial automation hardware."},
        )

    monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)


# ---------------------------------------------------------------------------
# Job creation + claim (A2/A3/A4)
# ---------------------------------------------------------------------------


class TestJobCreationAndClaim:
    def test_create_job_starts_pending(self, db_session, sample_lead):
        job = job_repository.create_job(
            db_session,
            organization_id=sample_lead.organization_id,
            lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        assert job.status == JobStatus.PENDING.value
        assert job.attempt_count == 0
        assert job.lead_id == sample_lead.id

    def test_claimable_job_is_listed_and_claim_succeeds_once(self, db_session, sample_lead):
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )

        assert job.id in job_repository.list_claimable_job_ids(db_session)
        assert job_repository.try_claim(db_session, job.id, "worker-a") is True

        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status == JobStatus.CLAIMED.value
        assert refreshed.worker_id == "worker-a"
        assert refreshed.attempt_count == 1
        # No longer PENDING -> no longer claimable.
        assert job.id not in job_repository.list_claimable_job_ids(db_session)

    def test_two_concurrent_workers_never_both_claim_the_same_job(self, sample_org, sample_user):
        """The actual concurrency guarantee: two independent DB sessions
        (like two separate worker processes would each have) racing to
        claim the identical job -- exactly one must win."""
        session = SessionLocal()
        try:
            from core.domain.models.lead import Lead

            lead = Lead(organization_id=sample_org.id, owner_id=sample_user.id, website="https://job-race.example.com")
            session.add(lead)
            session.commit()
            session.refresh(lead)
            job = job_repository.create_job(
                session, organization_id=sample_org.id, lead_id=lead.id, job_type=JobType.LEAD_PIPELINE
            )
            job_id = job.id
        finally:
            session.close()

        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(10)

        def worker(worker_id: str):
            session = SessionLocal()
            try:
                barrier.wait()
                claimed = job_repository.try_claim(session, job_id, worker_id)
                with lock:
                    results.append(claimed)
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(f"worker-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert sum(1 for r in results if r) == 1, "more than one worker claimed the same job"

        session = SessionLocal()
        try:
            job = job_repository.get_job(session, job_id)
            assert job.attempt_count == 1, "attempt_count must be incremented exactly once, not once per race participant"
        finally:
            session.close()

    def test_running_transition_requires_matching_worker(self, db_session, sample_lead):
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        job_repository.try_claim(db_session, job.id, "worker-a")

        # A different worker (e.g. one that lost a stale reference after
        # a reclaim) must not be able to advance a job it doesn't own.
        assert job_repository.mark_running(db_session, job.id, "worker-b") is False
        assert job_repository.mark_running(db_session, job.id, "worker-a") is True


# ---------------------------------------------------------------------------
# Lease expiry / crash recovery (A5, B4)
# ---------------------------------------------------------------------------


class TestLeaseExpiryCrashRecovery:
    def test_deterministic_crash_recovery_scenario(self, db_session, sample_lead):
        """The exact scenario the spec (B4) asks for, made deterministic
        by directly backdating lease_expires_at instead of sleeping:
        job claimed -> stage running -> worker disappears -> lease
        expires -> job becomes retryable -> a DIFFERENT worker claims
        it."""
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        job_repository.try_claim(db_session, job.id, "worker-doomed", lease_seconds=300)
        job_repository.mark_running(db_session, job.id, "worker-doomed")

        # Simulate "worker disappears": backdate the lease as if it
        # expired 10 minutes ago, without the worker ever calling
        # mark_succeeded/mark_failed -- exactly what a hard process
        # crash mid-stage looks like from the database's point of view.
        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        db_session.query(Job).filter(Job.id == job.id).update({"lease_expires_at": stale})
        db_session.commit()

        reclaimed = job_repository.reclaim_expired_leases(db_session)
        assert job.id in reclaimed

        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status == JobStatus.PENDING.value
        assert refreshed.worker_id is None

        # A new worker can now claim and run it.
        assert job_repository.try_claim(db_session, job.id, "worker-fresh") is True
        assert job_repository.mark_running(db_session, job.id, "worker-fresh") is True

    def test_lease_reclaim_does_not_touch_a_still_alive_worker(self, db_session, sample_lead):
        """Guardrail: a job whose lease has NOT expired must never be
        reclaimed -- proves the fix isn't just "always reclaim RUNNING
        jobs"."""
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        job_repository.try_claim(db_session, job.id, "worker-alive", lease_seconds=300)
        job_repository.mark_running(db_session, job.id, "worker-alive")

        reclaimed = job_repository.reclaim_expired_leases(db_session)

        assert job.id not in reclaimed
        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status == JobStatus.RUNNING.value
        assert refreshed.worker_id == "worker-alive"

    def test_exhausted_attempts_go_to_failed_not_pending_on_reclaim(self, db_session, sample_lead):
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE, max_attempts=1,
        )
        job_repository.try_claim(db_session, job.id, "worker-doomed")  # attempt_count -> 1 == max_attempts
        job_repository.mark_running(db_session, job.id, "worker-doomed")

        stale = datetime.now(timezone.utc) - timedelta(minutes=10)
        db_session.query(Job).filter(Job.id == job.id).update({"lease_expires_at": stale})
        db_session.commit()

        reclaimed = job_repository.reclaim_expired_leases(db_session)

        assert job.id not in reclaimed  # not reclaimed to PENDING
        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status == JobStatus.FAILED.value


# ---------------------------------------------------------------------------
# Retry classification (A6)
# ---------------------------------------------------------------------------


class TestRetryClassification:
    def test_success_is_not_retried(self):
        succeeded, category, retryable = classify_pipeline_result({"status": "SUCCESS", "errors": []})
        assert succeeded is True

    def test_partial_success_counts_as_job_success(self):
        succeeded, _, _ = classify_pipeline_result({"status": "PARTIAL_SUCCESS", "errors": [{"stage": "scrape", "error": "x"}]})
        assert succeeded is True

    def test_skipped_in_progress_is_retryable(self):
        succeeded, category, retryable = classify_pipeline_result({"status": "SKIPPED_IN_PROGRESS", "errors": []})
        assert succeeded is False
        assert category == FailureCategory.ALREADY_IN_PROGRESS
        assert retryable is True

    def test_lead_not_found_is_not_retryable(self):
        succeeded, category, retryable = classify_pipeline_result(
            {"status": "FAILED", "errors": [{"stage": "pipeline", "error": "Lead not found"}]}
        )
        assert succeeded is False
        assert category == FailureCategory.VALIDATION_ERROR
        assert retryable is False

    def test_timeout_error_is_retryable(self):
        succeeded, category, retryable = classify_pipeline_result(
            {"status": "FAILED", "errors": [{"stage": "pipeline", "error": "Read timeout while calling provider"}]}
        )
        assert retryable is True
        assert category == FailureCategory.TIMEOUT

    def test_rate_limit_error_is_retryable_resource_exhausted(self):
        succeeded, category, retryable = classify_pipeline_result(
            {"status": "FAILED", "errors": [{"stage": "pipeline", "error": "received 429 Too Many Requests"}]}
        )
        assert retryable is True
        assert category == FailureCategory.RESOURCE_EXHAUSTED

    def test_unrecognized_error_defaults_to_permanent_provider_error_but_still_retryable_once(self):
        succeeded, category, retryable = classify_pipeline_result(
            {"status": "FAILED", "errors": [{"stage": "pipeline", "error": "something bizarre and unclassified happened"}]}
        )
        assert category == FailureCategory.PERMANENT_PROVIDER_ERROR
        assert retryable is True  # capped by max_attempts, not retried forever

    def test_operational_error_exception_classifies_as_transient_database(self):
        from sqlalchemy.exc import OperationalError

        category, retryable = classify_exception(OperationalError("stmt", {}, Exception("db down")))
        assert category == FailureCategory.TRANSIENT_DATABASE
        assert retryable is True

    def test_backoff_grows_and_is_capped(self):
        first = compute_backoff(1, base_seconds=5, max_seconds=300)
        second = compute_backoff(2, base_seconds=5, max_seconds=300)
        huge = compute_backoff(20, base_seconds=5, max_seconds=300)
        now = datetime.now(timezone.utc)
        assert first < second
        assert (huge - now).total_seconds() <= 300


# ---------------------------------------------------------------------------
# End-to-end job execution (A7/A8, via the real executor + real
# run_lead_pipeline, still fully offline -- GROQ_API_KEY unset)
# ---------------------------------------------------------------------------


class TestJobExecutorEndToEnd:
    async def test_successful_job_is_marked_succeeded_with_pipeline_id(
        self, db_session, sample_lead, mock_successful_scrape
    ):
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        worker_id = "worker-e2e"
        job_repository.try_claim(db_session, job.id, worker_id)

        await job_executor.execute_job(job.id, worker_id)

        # execute_job runs on its OWN internal DB session (by design --
        # it must be callable standalone from the background worker
        # loop), so db_session's identity map holds a stale, pre-update
        # copy of this row until expired -- a test-only artifact, not a
        # production concern (a real caller always gets a fresh
        # request-scoped session).
        db_session.expire_all()
        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status in (JobStatus.SUCCEEDED.value,)
        assert refreshed.pipeline_id is not None
        assert refreshed.completed_at is not None

    async def test_job_for_a_deleted_lead_fails_without_retry(self, db_session, sample_org, sample_user):
        from core.domain.models.lead import Lead

        lead = Lead(organization_id=sample_org.id, owner_id=sample_user.id, website="https://ghost.example.com")
        db_session.add(lead)
        db_session.commit()
        db_session.refresh(lead)
        lead_id = lead.id

        job = job_repository.create_job(
            db_session, organization_id=sample_org.id, lead_id=lead_id, job_type=JobType.LEAD_PIPELINE
        )
        worker_id = "worker-ghost"
        job_repository.try_claim(db_session, job.id, worker_id)

        db_session.delete(lead)
        db_session.commit()

        await job_executor.execute_job(job.id, worker_id)

        db_session.expire_all()
        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status == JobStatus.FAILED.value
        assert refreshed.last_error_category == FailureCategory.VALIDATION_ERROR.value

    async def test_already_in_progress_job_is_scheduled_for_retry_not_failed(
        self, db_session, sample_lead
    ):
        """Uses the real Phase A3/A4 pipeline lock to force a genuine
        SKIPPED_IN_PROGRESS outcome, proving the job layer correctly
        treats existing-lock contention as retryable, not a failure."""
        from application.concurrency import pipeline_lock

        pipeline_lock.try_acquire(db_session, sample_lead.id, sample_lead.organization_id, "someone-else-running")

        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        worker_id = "worker-contended"
        job_repository.try_claim(db_session, job.id, worker_id)

        await job_executor.execute_job(job.id, worker_id)

        db_session.expire_all()
        refreshed = job_repository.get_job(db_session, job.id)
        # Retried -> flipped back to PENDING (not stuck in RETRY_WAIT,
        # not FAILED) so it will be tried again shortly.
        assert refreshed.status == JobStatus.PENDING.value
        assert refreshed.last_error_category == FailureCategory.ALREADY_IN_PROGRESS.value
        assert refreshed.available_at > refreshed.claimed_at
