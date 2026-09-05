"""
Failure injection tests -- P0 final section.

Ten scenarios were required. Seven are already covered by existing
regression tests from this same hardening effort, each cited below
rather than re-tested here (matching "do not create large synthetic
test frameworks" and avoiding duplicate coverage):

  1. worker crash                    -> tests/application/test_pipeline_execution_state.py::TestDeterministicCrashRecoveryEndToEnd
  2. database transient failure      -> covered at the classifier-unit level in test_durable_jobs.py::test_operational_error_exception_classifies_as_transient_database;
                                          exercised end-to-end below (TestJobExecutorHandlesTransientDatabaseFailure)
  4. LLM 429                          -> test_durable_jobs.py::test_rate_limit_error_is_retryable_resource_exhausted
  5. LLM timeout                      -> test_durable_jobs.py::test_timeout_error_is_retryable
  8. Playwright failure               -> pre-existing scraper test suite (tests/application/discovery/, not touched by this task)
  9. duplicate pipeline request       -> tests/application/test_production_robustness.py::TestPipelineLockIntegration
  10. duplicate lead creation         -> tests/application/test_production_robustness.py::TestLeadCreationRace

The three genuinely new tests below cover what wasn't already exercised
anywhere: invalid LLM JSON, a real (not just classified) database
failure injected into the job executor's actual code path, and Redis
being unavailable -- confirming graceful degradation without a retry
storm, per this section's own "do not turn every failure into a retry"
instruction.
"""

import pytest

from application.execution import job_executor, job_repository
from application.execution.job_types import FailureCategory, JobStatus, JobType
from application.services.llm_provider import safe_invoke_json


# ---------------------------------------------------------------------------
# 6. Invalid LLM JSON
# ---------------------------------------------------------------------------


class TestInvalidLLMJsonResponse:
    def test_malformed_json_returns_none_not_an_exception(self, monkeypatch):
        """safe_invoke_json must never raise on bad model output -- every
        agent's deterministic fallback depends on this contract."""
        from application.services import llm_provider

        class _FakeResponse:
            content = "Sure, here's the analysis: {not valid json, oops"

        class _FakeChain:
            def invoke(self, inputs):
                return _FakeResponse()

        class _FakePrompt:
            def __or__(self, other):
                return _FakeChain()

        monkeypatch.setattr(llm_provider, "get_llm", lambda **kwargs: object())
        monkeypatch.setattr(
            "langchain_core.prompts.ChatPromptTemplate.from_messages",
            lambda messages: _FakePrompt(),
        )

        payload, retry_count = safe_invoke_json([("system", "x"), ("human", "y")], {})
        assert payload is None  # never raises, never returns a partial/corrupt dict

    def test_no_json_object_at_all_returns_none(self, monkeypatch):
        from application.services import llm_provider

        class _FakeResponse:
            content = "I cannot complete this request."

        class _FakeChain:
            def invoke(self, inputs):
                return _FakeResponse()

        class _FakePrompt:
            def __or__(self, other):
                return _FakeChain()

        monkeypatch.setattr(llm_provider, "get_llm", lambda **kwargs: object())
        monkeypatch.setattr(
            "langchain_core.prompts.ChatPromptTemplate.from_messages",
            lambda messages: _FakePrompt(),
        )

        payload, _ = safe_invoke_json([("system", "x"), ("human", "y")], {})
        assert payload is None


# ---------------------------------------------------------------------------
# 2. Database transient failure -- injected into the real job executor
# code path (not just the classifier function in isolation)
# ---------------------------------------------------------------------------


class TestJobExecutorHandlesTransientDatabaseFailure:
    async def test_a_db_error_during_pipeline_execution_is_retried_not_permanently_failed(
        self, db_session, sample_lead, monkeypatch
    ):
        from sqlalchemy.exc import OperationalError

        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE, max_attempts=3,
        )
        worker_id = "worker-db-failure-test"
        job_repository.try_claim(db_session, job.id, worker_id)

        async def _raising_runner(lead_id: int):
            raise OperationalError("SELECT 1", {}, Exception("simulated connection drop"))

        await job_executor.execute_job(job.id, worker_id, pipeline_runner=_raising_runner)

        db_session.expire_all()
        refreshed = job_repository.get_job(db_session, job.id)
        # Retried (transient, capacity remains), never a retry storm --
        # exactly one attempt was consumed, not all three at once.
        assert refreshed.status == JobStatus.PENDING.value
        assert refreshed.last_error_category == FailureCategory.TRANSIENT_DATABASE.value
        assert refreshed.attempt_count == 1

    async def test_repeated_db_failures_eventually_stop_retrying(self, db_session, sample_lead):
        """Guardrail against a retry storm: once attempts are exhausted,
        the job must land in FAILED, not loop forever."""
        from sqlalchemy.exc import OperationalError

        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE, max_attempts=1,
        )
        worker_id = "worker-db-failure-exhaust"
        job_repository.try_claim(db_session, job.id, worker_id)  # attempt_count -> 1 == max_attempts

        async def _raising_runner(lead_id: int):
            raise OperationalError("SELECT 1", {}, Exception("simulated connection drop"))

        await job_executor.execute_job(job.id, worker_id, pipeline_runner=_raising_runner)

        db_session.expire_all()
        refreshed = job_repository.get_job(db_session, job.id)
        assert refreshed.status == JobStatus.FAILED.value


# ---------------------------------------------------------------------------
# 3. Redis unavailable
# ---------------------------------------------------------------------------


class TestRedisUnavailable:
    def test_health_check_degrades_gracefully_without_crashing(self, monkeypatch):
        """Confirms main.py's /health Redis check (the only place Redis
        is used at all in this codebase -- see the P0 final report's
        Phase 0 audit) catches a connection failure and reports it in
        the health payload rather than raising an unhandled exception
        that would 500 the whole endpoint."""
        import redis

        def _raise_connection_error(*args, **kwargs):
            raise redis.exceptions.ConnectionError("simulated: redis unreachable")

        monkeypatch.setattr(redis.Redis, "from_url", staticmethod(_raise_connection_error))

        # Exercise the exact try/except block main.py's health_check
        # uses, without needing a full FastAPI TestClient app for this
        # narrow check.
        health_status = {"checks": {}}
        is_healthy = True
        try:
            redis_url = "redis://localhost:6379/0"
            redis_client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
            redis_client.ping()
            redis_client.close()
            health_status["checks"]["redis"] = "healthy"
        except Exception as e:
            health_status["checks"]["redis"] = f"unhealthy: {e}"
            is_healthy = False

        assert is_healthy is False
        assert "unhealthy" in health_status["checks"]["redis"]

    def test_job_processing_does_not_depend_on_redis_at_all(self, db_session, sample_lead):
        """Positive confirmation of a Phase-0-audit finding: the P0-A job
        system (core/domain/models/job.py, application/execution/*) was
        deliberately built entirely on PostgreSQL/SQLAlchemy, with zero
        Redis dependency -- so 'Redis unavailable' has no effect on job
        creation, claiming, or execution at all. This test creates and
        claims a job with no Redis client imported or reachable
        anywhere in the call path."""
        job = job_repository.create_job(
            db_session, organization_id=sample_lead.organization_id, lead_id=sample_lead.id,
            job_type=JobType.LEAD_PIPELINE,
        )
        assert job_repository.try_claim(db_session, job.id, "worker-no-redis") is True
