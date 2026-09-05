"""
P0-D (AI provenance) regression tests.

Confirms every meaningful AI output persists: pipeline_id, source (llm vs.
heuristic/rule_based/template/deterministic), the actual model identity
only when a model was genuinely invoked, and evaluation_version -- and
that deterministic-fallback executions are clearly distinguishable from
real LLM executions in the persisted record, not just in the in-memory
DTO. GROQ_API_KEY is unset in this test environment (see conftest.py), so
every agent below runs its deterministic fallback path -- exactly the
path where the pre-existing `model_used=output.source` bug produced the
most misleading result (a fallback execution's `model_used` column held
"heuristic"/"rule_based"/"template" instead of being correctly empty).
"""

from application.evaluation.evaluators import EVALUATION_VERSION
from application.workflows.graph_nodes import LeadPipelineNodes
from core.domain.models.lead import AIDecisionLog


def _log_for(db_session, lead_id: int, stage: str) -> AIDecisionLog:
    row = (
        db_session.query(AIDecisionLog)
        .filter(AIDecisionLog.lead_id == lead_id, AIDecisionLog.stage == stage)
        .order_by(AIDecisionLog.id.desc())
        .first()
    )
    assert row is not None, f"no AIDecisionLog row was persisted for stage={stage!r}"
    return row


class TestCompanyIntelligenceProvenance:
    async def test_deterministic_fallback_is_correctly_labeled(self, db_session, sample_lead):
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "prov-test-ci-1",
            "stage_timings_ms": {},
            "errors": [],
        }

        await nodes.company_intelligence(state)
        log = _log_for(db_session, sample_lead.id, "company_intelligence")

        assert log.pipeline_id == "prov-test-ci-1"
        assert log.source == "heuristic"
        # The core bug this fixes: model_used must NEVER hold the source
        # string -- it must be empty when no model was actually invoked.
        assert log.model_used is None
        assert log.model_used != "heuristic"
        assert log.evaluation_version == EVALUATION_VERSION


class TestDecisionProvenance:
    async def test_deterministic_fallback_is_correctly_labeled(self, db_session, sample_lead):
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "prov-test-decision-1",
            "score_result": {"total_score": 60.0, "qualification_label": "Warm Lead"},
            "company_intelligence": {},
            "stage_timings_ms": {},
            "errors": [],
        }

        await nodes.decision(state)
        log = _log_for(db_session, sample_lead.id, "decision")

        assert log.pipeline_id == "prov-test-decision-1"
        assert log.source == "rule_based"
        assert log.model_used is None
        assert log.model_used != "rule_based"
        assert log.evaluation_version == EVALUATION_VERSION


class TestMessagingProvenance:
    async def test_deterministic_fallback_is_correctly_labeled(self, db_session, sample_lead, sample_org):
        from core.infrastructure.billing.subscription_service import SubscriptionService

        # Messaging is gated on AI-tier plan access -- use a real plan
        # check rather than assuming, consistent with existing tests.
        ai_enabled = SubscriptionService(db_session).can_use_ai_features(sample_org.id)

        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "prov-test-messaging-1",
            "ai_features_enabled": ai_enabled,
            "review": {"decision": "auto_approved"},
            "context": {},
            "decision": {"qualification": "Warm Lead", "recommended_action": "review"},
            "stage_timings_ms": {},
            "errors": [],
        }

        await nodes.message_generation(state)

        if not ai_enabled:
            # Free-tier path never calls the messaging agent at all --
            # nothing to assert on provenance for a message that was
            # never AI-generated in the first place.
            return

        log = _log_for(db_session, sample_lead.id, "messaging")
        assert log.pipeline_id == "prov-test-messaging-1"
        assert log.source == "template"
        assert log.model_used is None
        assert log.model_used != "template"
        assert log.evaluation_version == EVALUATION_VERSION


class TestEvaluationAndReviewProvenance:
    async def test_evaluation_stage_persists_pipeline_id_and_version(self, db_session, sample_lead):
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "prov-test-eval-1",
            "decision": {"qualification": "Warm Lead", "explanation": {"evidence": []}},
            "score_result": {"total_score": 55.0, "qualification_label": "Warm Lead"},
            "context": {},
            "company_intelligence": {},
            "stage_timings_ms": {},
            "errors": [],
        }

        await nodes.confidence_evaluation(state)
        log = _log_for(db_session, sample_lead.id, "evaluation")

        assert log.pipeline_id == "prov-test-eval-1"
        assert log.source == "deterministic"
        assert log.evaluation_version == EVALUATION_VERSION

    async def test_review_stage_persists_pipeline_id_and_version(self, db_session, sample_lead):
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "prov-test-review-1",
            "evaluation": {
                "confidence": 0.9, "completeness": 1.0, "grounding": 0.9,
                "consistency": 1.0, "overall": 0.9,
            },
            "stage_timings_ms": {},
            "errors": [],
        }

        await nodes.review(state)
        log = _log_for(db_session, sample_lead.id, "review")

        assert log.pipeline_id == "prov-test-review-1"
        assert log.source == "deterministic"
        assert log.evaluation_version == EVALUATION_VERSION


class TestPromptExecutionRecordModelField:
    """Confirms PromptExecutionRecord (only ever written on the LLM path)
    now also records the model identity -- previously missing entirely.
    Since GROQ_API_KEY is unset, no LLM path fires in this offline test
    environment, so this is a direct unit test of the repository function
    rather than a full pipeline run (which would never reach this path
    without a real/faked LLM response)."""

    def test_model_field_persists(self, db_session, sample_lead):
        from application.observability.repository import create_prompt_execution_record

        record = create_prompt_execution_record(
            db_session,
            pipeline_id="prov-test-prompt-1",
            lead_id=sample_lead.id,
            organization_id=sample_lead.organization_id,
            agent_name="CompanyIntelligenceAgent",
            prompt_name="company_intelligence",
            prompt_version="v1",
            retry_count=0,
            model="openai/gpt-oss-120b",
        )

        assert record.model == "openai/gpt-oss-120b"
