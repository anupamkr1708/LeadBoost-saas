"""
Regression tests for the production-robustness hardening (Phase A:
correctness under retries / duplicate submissions; Phase B: resource
protection / concurrency safety).

Each test class targets exactly one CONFIRMED GAP identified during the
audit (see the final report) and proves both that the gap is now closed
AND that ordinary, non-racing behavior is unchanged:

  TestPipelineLock              -- Phase A3/A4 (lead processing idempotency)
  TestAtomicDailyQuota          -- Phase A5 (quota race)
  TestLeadCreationRace          -- Phase A5 (duplicate-lead race)
  TestLLMConcurrencyGuard       -- Phase B4 (LLM concurrency)

Concurrency is exercised with real OS threads, each opening its own
`SessionLocal()` against the same file-based SQLite database used by the
rest of the suite (see tests/application/conftest.py) -- this is what
actually proves atomicity at the database level, as opposed to two
sequential calls against a single shared session, which would trivially
"pass" a broken implementation too.
"""

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from application.concurrency import pipeline_lock
from application.dto.models import PipelineStatus
from application.services import infra_adapters
from application.workflows.lead_pipeline import LeadPipeline
from core.domain.models.billing import DailyLeadQuotaUsage
from core.domain.models.lead import Lead
from core.domain.models.pipeline_lock import ActivePipelineLock
from core.infrastructure.billing.quota_guard import reserve_daily_lead_quota
from core.infrastructure.database import SessionLocal
from core.infrastructure.database.crud import create_lead, get_lead_by_url
from core.domain.schemas.lead import LeadCreate


# ---------------------------------------------------------------------------
# Phase A3/A4 -- per-lead pipeline lock
# ---------------------------------------------------------------------------


class TestPipelineLock:
    def test_try_acquire_succeeds_when_no_lock_held(self, db_session, sample_lead):
        acquired = pipeline_lock.try_acquire(
            db_session, sample_lead.id, sample_lead.organization_id, "pipeline-1"
        )
        assert acquired is True

    def test_try_acquire_fails_when_lock_already_held(self, db_session, sample_lead):
        assert pipeline_lock.try_acquire(
            db_session, sample_lead.id, sample_lead.organization_id, "pipeline-1"
        )
        # A second attempt for the SAME lead, before the first is
        # released, must be rejected -- this is the core guarantee that
        # prevents a duplicate/concurrent /leads/{id}/process call from
        # launching a second full pipeline run.
        second = pipeline_lock.try_acquire(
            db_session, sample_lead.id, sample_lead.organization_id, "pipeline-2"
        )
        assert second is False

    def test_get_active_reports_the_holder(self, db_session, sample_lead):
        pipeline_lock.try_acquire(db_session, sample_lead.id, sample_lead.organization_id, "pipeline-1")
        active = pipeline_lock.get_active(db_session, sample_lead.id)
        assert active is not None
        assert active.pipeline_id == "pipeline-1"

    def test_release_allows_a_later_non_overlapping_acquire(self, db_session, sample_lead):
        """The lock must never block a *later* reprocess request -- only
        genuine overlap. This is what preserves the existing "manually
        trigger processing for a lead" feature, which legitimately reruns
        the pipeline for the same lead any number of times."""
        pipeline_lock.try_acquire(db_session, sample_lead.id, sample_lead.organization_id, "pipeline-1")
        pipeline_lock.release(db_session, sample_lead.id)

        again = pipeline_lock.try_acquire(
            db_session, sample_lead.id, sample_lead.organization_id, "pipeline-2"
        )
        assert again is True

    def test_stale_lock_is_reclaimed(self, db_session, sample_lead, monkeypatch):
        """A crash between acquiring and releasing the lock (not a normal
        exception -- those are already caught by LeadPipeline's own
        try/finally) must not strand a lead in 'processing' forever."""
        monkeypatch.setenv("PIPELINE_LOCK_STALE_SECONDS", "60")

        stale_time = datetime.now(timezone.utc) - timedelta(seconds=120)
        db_session.add(
            ActivePipelineLock(
                lead_id=sample_lead.id,
                organization_id=sample_lead.organization_id,
                pipeline_id="crashed-pipeline",
                acquired_at=stale_time,
            )
        )
        db_session.commit()

        # Without the stale-reclaim logic this would return False forever.
        acquired = pipeline_lock.try_acquire(
            db_session, sample_lead.id, sample_lead.organization_id, "new-pipeline"
        )
        assert acquired is True

    def test_fresh_lock_is_not_reclaimed_as_stale(self, db_session, sample_lead, monkeypatch):
        monkeypatch.setenv("PIPELINE_LOCK_STALE_SECONDS", "1800")
        pipeline_lock.try_acquire(db_session, sample_lead.id, sample_lead.organization_id, "pipeline-1")

        acquired_again = pipeline_lock.try_acquire(
            db_session, sample_lead.id, sample_lead.organization_id, "pipeline-2"
        )
        assert acquired_again is False

    def test_lock_scoping_is_per_lead_not_global(self, db_session, sample_org, sample_user):
        """Locking lead A must never block processing of lead B."""
        lead_a = Lead(organization_id=sample_org.id, owner_id=sample_user.id, website="https://a.example.com")
        lead_b = Lead(organization_id=sample_org.id, owner_id=sample_user.id, website="https://b.example.com")
        db_session.add_all([lead_a, lead_b])
        db_session.commit()
        db_session.refresh(lead_a)
        db_session.refresh(lead_b)

        assert pipeline_lock.try_acquire(db_session, lead_a.id, sample_org.id, "p-a")
        assert pipeline_lock.try_acquire(db_session, lead_b.id, sample_org.id, "p-b")


class TestPipelineLockIntegration:
    """Exercises the lock through the actual LeadPipeline.execute() entry
    point (the real chokepoint every existing call site already uses),
    not just the lock helpers in isolation."""

    async def test_execute_skips_duplicate_run_and_reports_in_flight_pipeline_id(
        self, db_session, sample_lead
    ):
        pipeline_lock.try_acquire(db_session, sample_lead.id, sample_lead.organization_id, "already-running")

        pipeline = LeadPipeline(db_session)
        result = await pipeline.execute(sample_lead.id)

        assert result.status == PipelineStatus.SKIPPED_IN_PROGRESS
        assert result.pipeline_id == "already-running"
        assert result.errors and "already running" in result.errors[0]["error"]

        # No duplicate scraping/enrichment/LLM work -- and no
        # PipelineExecutionRecord should be written for a run that never
        # actually executed a single stage (would otherwise pollute
        # pipeline-metrics success/partial/failed accounting).
        from application.observability.repository import get_pipeline_executions

        records = get_pipeline_executions(db_session, organization_id=sample_lead.organization_id)
        assert not any(r.pipeline_id == result.pipeline_id for r in records)

    async def test_execute_runs_normally_when_no_lock_held(
        self, db_session, sample_lead, monkeypatch
    ):
        async def _fake_scrape_lead(url: str):
            from dataclasses import dataclass, field
            from typing import Any, Dict, List, Optional
            from core.infrastructure.scraping.scraper import ScrapingMethod

            @dataclass
            class _R:
                success: bool
                data: Dict[str, Any]
                method: ScrapingMethod = ScrapingMethod.STRUCTURED_DATA
                confidence: float = 0.8
                processing_time: float = 0.1
                error_message: Optional[str] = None
                pages_scraped: int = 1
                blocked_detected: bool = False
                tiers_attempted: List[str] = field(default_factory=list)

            return _R(success=True, data={"title": "Acme"})

        monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)

        pipeline = LeadPipeline(db_session)
        result = await pipeline.execute(sample_lead.id)

        assert result.status in (PipelineStatus.SUCCESS, PipelineStatus.PARTIAL_SUCCESS)
        # And the lock must be released afterwards so a later reprocess
        # request is never blocked by a run that already finished.
        assert pipeline_lock.get_active(db_session, sample_lead.id) is None

    async def test_a_second_execute_after_completion_is_allowed(
        self, db_session, sample_lead, monkeypatch
    ):
        """Proves the lock only blocks genuine overlap, never a later,
        separate manual-reprocess request -- the existing intended
        behavior of POST /leads/{id}/process."""
        async def _fake_scrape_lead(url: str):
            from dataclasses import dataclass
            @dataclass
            class _R:
                success: bool = False
                data: dict = None
                confidence: float = 0.0
                error_message: str = None
            r = _R(data={})
            return r

        monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)

        pipeline = LeadPipeline(db_session)
        first = await pipeline.execute(sample_lead.id)
        second = await pipeline.execute(sample_lead.id)

        assert first.status != PipelineStatus.SKIPPED_IN_PROGRESS
        assert second.status != PipelineStatus.SKIPPED_IN_PROGRESS
        assert first.pipeline_id != second.pipeline_id

    async def test_concurrent_execute_calls_only_run_one_pipeline(
        self, db_session, sample_org, sample_user
    ):
        """The actual concurrency scenario the spec calls out: two
        overlapping requests to process the same lead. Uses two
        independent DB sessions (like two independent HTTP requests
        would each get via `Depends(get_db)`) racing via asyncio, backed
        by the real database-level UNIQUE constraint -- not a
        Python-level lock that only some interpreters/paths would
        respect."""
        lead = Lead(
            organization_id=sample_org.id, owner_id=sample_user.id, website="https://race.example.com"
        )
        db_session.add(lead)
        db_session.commit()
        db_session.refresh(lead)
        lead_id = lead.id

        import asyncio
        from application.services import infra_adapters as ia
        from dataclasses import dataclass

        release_first = asyncio.Event()
        entered_first = asyncio.Event()

        @dataclass
        class _SlowResult:
            success: bool = True
            data: dict = None
            confidence: float = 0.5
            error_message: str = None

        async def _slow_scrape(url: str):
            entered_first.set()
            await release_first.wait()
            return _SlowResult(data={"title": "Race Co"})

        async def run_pipeline_with_own_session(patch_scrape: bool):
            session = SessionLocal()
            try:
                if patch_scrape:
                    import unittest.mock as mock

                    with mock.patch.object(ia, "scrape_lead", _slow_scrape):
                        pipeline = LeadPipeline(session)
                        return await pipeline.execute(lead_id)
                else:
                    await entered_first.wait()
                    pipeline = LeadPipeline(session)
                    result = await pipeline.execute(lead_id)
                    release_first.set()
                    return result
            finally:
                session.close()

        first_result, second_result = await asyncio.gather(
            run_pipeline_with_own_session(True),
            run_pipeline_with_own_session(False),
        )

        statuses = {first_result.status, second_result.status}
        # Exactly one of the two genuinely ran the pipeline; the other
        # must have been turned away by the lock, never both running.
        assert PipelineStatus.SKIPPED_IN_PROGRESS in statuses
        assert statuses != {PipelineStatus.SKIPPED_IN_PROGRESS}


# ---------------------------------------------------------------------------
# Phase A5 -- atomic daily quota
# ---------------------------------------------------------------------------


class TestAtomicDailyQuota:
    def test_reservation_succeeds_up_to_the_limit(self, db_session, sample_org):
        for _ in range(5):
            assert reserve_daily_lead_quota(db_session, sample_org.id, max_per_day=5) is True

    def test_reservation_fails_once_limit_reached(self, db_session, sample_org):
        for _ in range(3):
            assert reserve_daily_lead_quota(db_session, sample_org.id, max_per_day=3) is True
        # The 4th unit must be rejected, not silently allowed through.
        assert reserve_daily_lead_quota(db_session, sample_org.id, max_per_day=3) is False

    def test_reservation_is_scoped_per_organization(self, db_session, sample_org):
        from core.domain.models.organization import Organization

        other_org = Organization(name="Other Org")
        db_session.add(other_org)
        db_session.commit()
        db_session.refresh(other_org)

        for _ in range(2):
            assert reserve_daily_lead_quota(db_session, sample_org.id, max_per_day=2) is True
        assert reserve_daily_lead_quota(db_session, sample_org.id, max_per_day=2) is False

        # A different organization's quota must be completely
        # unaffected by sample_org having exhausted its own.
        assert reserve_daily_lead_quota(db_session, other_org.id, max_per_day=2) is True

    def test_concurrent_reservations_never_exceed_the_limit(self, sample_org_id_factory):
        """The scenario from the spec verbatim: remaining quota = 10,
        two requests both read 10 and both accept -- reproduced here
        with 20 real concurrent threads (each its own DB connection)
        racing for a limit of 10, proving the database-level atomic
        UPDATE, not a Python-level lock, is what closes the race."""
        org_id = sample_org_id_factory()
        max_per_day = 10
        num_threads = 20
        results = []
        lock = threading.Lock()

        barrier = threading.Barrier(num_threads)

        def worker():
            session = SessionLocal()
            try:
                barrier.wait()  # maximize actual overlap
                ok = reserve_daily_lead_quota(session, org_id, max_per_day=max_per_day)
                with lock:
                    results.append(ok)
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(results) == num_threads
        successes = sum(1 for r in results if r)
        assert successes == max_per_day, (
            f"expected exactly {max_per_day} successful reservations out of "
            f"{num_threads} concurrent attempts, got {successes}"
        )

        # And the persisted counter must match reality exactly.
        session = SessionLocal()
        try:
            row = (
                session.query(DailyLeadQuotaUsage)
                .filter(DailyLeadQuotaUsage.organization_id == org_id)
                .first()
            )
            assert row.leads_reserved == max_per_day
        finally:
            session.close()

    def test_get_organization_usage_display_is_unchanged_by_reservations(
        self, db_session, sample_org, sample_user
    ):
        """The atomic reservation table is a separate enforcement gate --
        it must NOT change the existing COUNT(*)-based usage/reporting
        behavior that other parts of the app already rely on."""
        from core.infrastructure.billing.subscription_service import SubscriptionService

        # Reserve quota WITHOUT creating any actual Lead rows (simulating
        # the rare "reserved but lead-creation failed" edge case
        # documented in quota_guard.py).
        reserve_daily_lead_quota(db_session, sample_org.id, max_per_day=50, count=3)

        service = SubscriptionService(db_session)
        usage = service.get_organization_usage(sample_org.id)
        # Display usage is still computed from actual Lead rows (there
        # are none), completely unaffected by the reservation table.
        assert usage.current_usage == 0


@pytest.fixture()
def sample_org_id_factory(db_engine):
    """Creates a fresh Organization per call, each in its own committed
    session, for tests that need an organization_id usable from
    independent threads/sessions."""
    from core.domain.models.organization import Organization

    def _make():
        session = SessionLocal()
        try:
            org = Organization(name=f"Concurrency Test Org {uuid.uuid4().hex[:8]}")
            session.add(org)
            session.commit()
            session.refresh(org)
            return org.id
        finally:
            session.close()

    return _make


# ---------------------------------------------------------------------------
# Phase A5 -- lead-creation race (duplicate-URL UNIQUE constraint)
# ---------------------------------------------------------------------------


class TestLeadCreationRace:
    def test_duplicate_url_same_organization_raises_integrity_error(
        self, db_session, sample_org, sample_user
    ):
        create_lead(
            db_session,
            LeadCreate(
                website="https://dup.example.com",
                organization_id=sample_org.id,
                owner_id=sample_user.id,
            ),
        )
        with pytest.raises(IntegrityError):
            create_lead(
                db_session,
                LeadCreate(
                    website="https://dup.example.com",
                    organization_id=sample_org.id,
                    owner_id=sample_user.id,
                ),
            )
        db_session.rollback()

    def test_same_url_different_organization_is_independent(
        self, db_session, sample_org, sample_user
    ):
        from core.domain.models.organization import Organization
        from core.domain.models.user import User
        from core.infrastructure.auth.security import get_password_hash

        other_org = Organization(name="Other Org 2")
        db_session.add(other_org)
        db_session.commit()
        db_session.refresh(other_org)

        other_user = User(
            email=f"other_{uuid.uuid4().hex[:8]}@test.com",
            hashed_password=get_password_hash("x"),
            organization_id=other_org.id,
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        lead1 = create_lead(
            db_session,
            LeadCreate(
                website="https://shared-name.example.com",
                organization_id=sample_org.id,
                owner_id=sample_user.id,
            ),
        )
        # Tenant isolation: the exact same website for a DIFFERENT
        # organization must succeed without any conflict.
        lead2 = create_lead(
            db_session,
            LeadCreate(
                website="https://shared-name.example.com",
                organization_id=other_org.id,
                owner_id=other_user.id,
            ),
        )
        assert lead1.id != lead2.id
        assert lead1.organization_id != lead2.organization_id

    def test_concurrent_duplicate_creation_results_in_exactly_one_lead(
        self, sample_org_id_factory
    ):
        """True concurrency: two threads, two independent DB sessions,
        both racing to create a lead for the identical URL for the same
        organization -- the scenario a duplicate-clicked 'Add Lead'
        button or a client retry produces. Exactly one must win; the
        other must observe IntegrityError (which every real call site
        now catches and converts into the existing 'lead already
        exists' response -- see api/endpoints/leads.py)."""
        org_id = sample_org_id_factory()

        # Need an owner_id (Lead.owner_id is NOT NULL) in this org.
        session = SessionLocal()
        try:
            from core.domain.models.user import User
            from core.infrastructure.auth.security import get_password_hash

            user = User(
                email=f"race_owner_{uuid.uuid4().hex[:8]}@test.com",
                hashed_password=get_password_hash("x"),
                organization_id=org_id,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            owner_id = user.id
        finally:
            session.close()

        url = "https://concurrent-race.example.com"
        outcomes = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker():
            session = SessionLocal()
            try:
                barrier.wait()
                try:
                    create_lead(
                        session,
                        LeadCreate(website=url, organization_id=org_id, owner_id=owner_id),
                    )
                    with lock:
                        outcomes.append("created")
                except IntegrityError:
                    session.rollback()
                    with lock:
                        outcomes.append("conflict")
            finally:
                session.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert sorted(outcomes) == ["conflict", "created"]

        session = SessionLocal()
        try:
            matching = (
                session.query(Lead)
                .filter(Lead.organization_id == org_id, Lead.website == url)
                .all()
            )
            assert len(matching) == 1
        finally:
            session.close()


# ---------------------------------------------------------------------------
# Phase B4 -- LLM concurrency guard
# ---------------------------------------------------------------------------


class TestLLMConcurrencyGuard:
    def test_llm_call_slot_bounds_concurrent_access(self, monkeypatch):
        """Directly exercises the shared semaphore used by all three Groq
        call sites (llm_provider.py, enricher.py, messenger.py) without
        needing a real API key -- proves at most LLM_MAX_CONCURRENT_CALLS
        'calls' are ever inside the guarded section at once, and that
        every call still eventually completes (no deadlock)."""
        from application.services import llm_provider

        original_semaphore = llm_provider._llm_call_semaphore
        llm_provider._llm_call_semaphore = threading.Semaphore(3)
        try:
            max_concurrent_observed = 0
            current_concurrent = 0
            state_lock = threading.Lock()
            num_threads = 12

            def fake_llm_call():
                nonlocal max_concurrent_observed, current_concurrent
                with llm_provider.llm_call_slot():
                    with state_lock:
                        current_concurrent += 1
                        max_concurrent_observed = max(max_concurrent_observed, current_concurrent)
                    time.sleep(0.05)
                    with state_lock:
                        current_concurrent -= 1

            threads = [threading.Thread(target=fake_llm_call) for _ in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

            assert max_concurrent_observed <= 3
            assert max_concurrent_observed > 0  # sanity: the section actually ran
        finally:
            llm_provider._llm_call_semaphore = original_semaphore

    def test_llm_call_slot_is_reentrant_safe_across_calls(self):
        """Sequential (non-overlapping) calls must never deadlock or
        leak permits -- each acquire is paired with a release even if
        the guarded code raises."""
        from application.services import llm_provider

        original_semaphore = llm_provider._llm_call_semaphore
        llm_provider._llm_call_semaphore = threading.Semaphore(2)
        try:
            with pytest.raises(ValueError):
                with llm_provider.llm_call_slot():
                    raise ValueError("simulated failure inside the guarded call")

            # If the permit from the raised call above had leaked, only
            # 1 (not 2) non-blocking acquires would succeed here.
            first = llm_provider._llm_call_semaphore.acquire(blocking=False)
            second = llm_provider._llm_call_semaphore.acquire(blocking=False)
            try:
                assert first and second, "a permit leaked after an exception inside llm_call_slot()"
            finally:
                if first:
                    llm_provider._llm_call_semaphore.release()
                if second:
                    llm_provider._llm_call_semaphore.release()
        finally:
            llm_provider._llm_call_semaphore = original_semaphore
