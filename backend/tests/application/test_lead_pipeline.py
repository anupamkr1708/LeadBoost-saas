"""
Tests for application.workflows.graph_nodes.LeadPipelineNodes and
application.workflows.lead_pipeline.LeadPipeline.

The scraper is monkeypatched to avoid any real network I/O, keeping these
tests fast and deterministic. GROQ_API_KEY is unset (see conftest.py), so
every agent exercises its deterministic/heuristic fallback path.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest

from application.services import infra_adapters
from application.dto.models import PipelineStatus
from application.workflows.graph_nodes import LeadPipelineNodes
from application.workflows.lead_pipeline import LeadPipeline
from core.infrastructure.scraping.scraper import ScrapingMethod


@dataclass
class _FakeScrapingResult:
    success: bool
    data: Dict[str, Any]
    method: ScrapingMethod = ScrapingMethod.STRUCTURED_DATA
    confidence: float = 0.8
    processing_time: float = 0.1
    error_message: Optional[str] = None
    pages_scraped: int = 1
    blocked_detected: bool = False
    tiers_attempted: List[str] = field(default_factory=list)


GOOD_SCRAPE_DATA = {
    "title": "Acme Robotics",
    "description": "Industrial automation and robotics for manufacturers.",
    "email": "sales@acme.com",
    "linkedin_url": "https://linkedin.com/company/acme",
    "text_content": "We are hiring across engineering as we expand after our Series B.",
}


@pytest.fixture()
def mock_successful_scrape(monkeypatch):
    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(success=True, data=dict(GOOD_SCRAPE_DATA))

    monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)


@pytest.fixture()
def mock_failed_scrape(monkeypatch):
    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(
            success=False, data={}, confidence=0.0, error_message="Connection refused"
        )

    monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)


# -- Node-level tests --------------------------------------------------------


async def test_scrape_node_persists_lead_fields(db_session, sample_lead, mock_successful_scrape):
    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}

    result = await nodes.scrape(state)

    assert result["scraping_result"]["success"] is True
    assert result["scraped_data"]["email"] == "sales@acme.com"
    assert "scrape" in result["stage_timings_ms"]
    assert result["errors"] == []

    from core.infrastructure.database.crud import get_lead

    refreshed = get_lead(db_session, sample_lead.id)
    assert refreshed.company_name == "Acme Robotics"
    assert refreshed.email == "sales@acme.com"
    assert refreshed.scrape_confidence == pytest.approx(0.8)


async def test_scrape_node_survives_scraper_failure(db_session, sample_lead, mock_failed_scrape):
    """A failed scrape must not raise -- the pipeline continues with empty data."""
    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}

    result = await nodes.scrape(state)

    assert result["scraped_data"] == {}
    assert result["errors"] == []  # a graceful "not success" is not a stage crash


async def test_scrape_node_records_error_on_exception(db_session, sample_lead, monkeypatch):
    async def _boom(url: str):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(infra_adapters, "scrape_lead", _boom)

    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}

    result = await nodes.scrape(state)

    assert result["scraping_result"] is None
    assert len(result["errors"]) == 1
    assert result["errors"][0]["stage"] == "scrape"
    assert "scrape" in result["stage_timings_ms"]  # duration still recorded


async def test_qualification_node_persists_score(db_session, sample_lead):
    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}

    result = await nodes.qualification(state)

    assert result["score_result"] is not None
    assert result["score_result"]["qualification_label"] in (
        "Hot Lead",
        "Warm Lead",
        "Cold Lead",
        "Disqualified",
    )

    from core.infrastructure.database.crud import get_lead

    refreshed = get_lead(db_session, sample_lead.id)
    assert refreshed.qualification_label == result["score_result"]["qualification_label"]
    assert refreshed.score == pytest.approx(result["score_result"]["total_score"])


async def test_decision_node_persists_prompt_execution_when_llm_path_used(
    db_session, sample_lead, monkeypatch
):
    """When an agent's LLM path succeeds, the node must persist a
    PromptExecutionRecord carrying pipeline_id/prompt name/version/retry
    count -- exercised here via a mocked LLM response since no real GROQ
    key is configured in the test environment."""
    import application.agents.decision_agent as decision_module

    monkeypatch.setattr(decision_module, "is_llm_available", lambda: True)
    monkeypatch.setattr(
        decision_module,
        "safe_invoke_json",
        lambda *a, **k: (
            {
                "qualification": "Hot Lead",
                "recommended_action": "proceed",
                "reasoning": "Strong signals",
                "evidence": ["High score"],
                "confidence": 0.9,
            },
            1,  # simulate one retry
        ),
    )

    nodes = LeadPipelineNodes(db_session)
    state = {
        "pipeline_id": "test-pipeline-xyz",
        "lead_id": sample_lead.id,
        "organization_id": sample_lead.organization_id,
        "ai_features_enabled": True,
        "score_result": {"total_score": 90.0, "qualification_label": "Hot Lead"},
        "context": {},
        "stage_timings_ms": {},
        "errors": [],
    }

    result = await nodes.decision(state)

    assert result["decision"]["source"] == "llm"
    assert result["decision"]["prompt_version"] == "v1"

    from application.observability.repository import get_prompt_executions

    records = get_prompt_executions(db_session, organization_id=sample_lead.organization_id)
    matching = [r for r in records if r.pipeline_id == "test-pipeline-xyz"]
    assert len(matching) == 1
    assert matching[0].prompt_name == "decision"
    assert matching[0].agent_name == "decision_agent"
    assert matching[0].retry_count == 1


async def test_message_generation_skips_on_human_review(db_session, sample_lead):
    nodes = LeadPipelineNodes(db_session)
    state = {
        "lead_id": sample_lead.id,
        "organization_id": sample_lead.organization_id,
        "ai_features_enabled": True,
        "review": {"decision": "human_review"},
        "context": {},
        "decision": {},
        "stage_timings_ms": {},
        "errors": [],
    }

    result = await nodes.message_generation(state)
    assert result["message"] is None


async def test_message_generation_free_tier_uses_static_disclaimer(db_session, sample_lead):
    nodes = LeadPipelineNodes(db_session)
    state = {
        "lead_id": sample_lead.id,
        "organization_id": sample_lead.organization_id,
        "ai_features_enabled": False,
        "review": {"decision": "auto_approved"},
        "context": {},
        "decision": {},
        "stage_timings_ms": {},
        "errors": [],
    }

    result = await nodes.message_generation(state)
    assert "not available on your plan" in result["message"]["email_body"]


# -- Full-graph tests ----------------------------------------------------------


async def test_full_pipeline_runs_all_stages_successfully(
    db_session, sample_lead, mock_successful_scrape
):
    pipeline = LeadPipeline(db_session)
    result = await pipeline.execute(sample_lead.id)

    assert result.status == PipelineStatus.SUCCESS
    assert result.pipeline_id is not None
    assert result.duration_ms is not None
    assert result.scraping_success is True
    assert result.score is not None
    assert result.qualification_label is not None
    assert result.decision is not None
    assert result.evaluation is not None
    assert result.review is not None
    # Every stage should have recorded a timing entry
    for stage in (
        "scrape",
        "enrich",
        "company_intelligence",
        "qualification",
        "decision",
        "confidence_evaluation",
        "review",
        "persistence",
        "analytics",
    ):
        assert stage in result.stage_timings_ms

    # A PipelineExecutionRecord should have been persisted for this run.
    from application.observability.repository import get_pipeline_executions

    records = get_pipeline_executions(db_session, organization_id=sample_lead.organization_id)
    matching = [r for r in records if r.pipeline_id == result.pipeline_id]
    assert len(matching) == 1
    assert matching[0].final_status == PipelineStatus.SUCCESS.value


async def test_full_pipeline_handles_scrape_failure_gracefully(
    db_session, sample_lead, mock_failed_scrape
):
    pipeline = LeadPipeline(db_session)
    result = await pipeline.execute(sample_lead.id)

    # The pipeline must reach the end and still produce a decision/review,
    # even though scraping failed outright. A gracefully-handled business
    # failure (no exception raised) is still an overall SUCCESS.
    assert result.status == PipelineStatus.SUCCESS
    assert result.scraping_success is False
    assert result.decision is not None
    assert result.review is not None


async def test_pipeline_returns_error_for_missing_lead(db_session):
    pipeline = LeadPipeline(db_session)
    result = await pipeline.execute(999999)
    assert result.status == PipelineStatus.FAILED
    assert result.errors
