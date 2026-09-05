"""
Workflow node implementations.

Each method on `LeadPipelineNodes` is one LangGraph node, matching exactly
one stage of the workflow:

  Lead Created -> Scraper -> Enrichment -> Company Intelligence ->
  Lead Qualification -> Decision Engine -> Confidence Evaluation ->
  Review Decision -> Message Generation -> Persistence -> Analytics

Every node:
  - is wrapped by `_run_stage`, which logs start/completion/duration via
    application.utils.stage_logger and catches any exception so a single
    stage failure never crashes the whole pipeline (partial recovery);
  - delegates all infrastructure access to application.services.infra_adapters
    (never imports core.infrastructure classes directly here);
  - delegates all reasoning to application.agents (never contains prompt
    text or LLM calls itself);
  - offloads any blocking/synchronous call (LLM invocations, the existing
    synchronous WaterfallEnricher/Messenger) to a worker thread via
    `asyncio.to_thread`, so the pipeline never blocks the FastAPI event loop.
"""

import asyncio
import json
from typing import Any, Callable, Coroutine, Dict, List, Optional

from sqlalchemy.orm import Session

from application.agents.company_intelligence_agent import CompanyIntelligenceAgent
from application.agents.decision_agent import DecisionAgent
from application.agents.messaging_agent import MessagingAgent
from application.agents.review_agent import ReviewAgent
from application.context.context_builder import ContextBuilder
from application.dto.models import (
    CompanyIntelligenceOutput,
    DecisionOutput,
    EvaluationReport,
    MessagingOutput,
    ReviewOutput,
)
from application.evaluation.evaluators import EVALUATION_VERSION, build_evaluation_report
from application.memory.db_memory import SQLBusinessMemory
from application.observability import repository as observability_repo
from application.services import infra_adapters
from application.services.llm_provider import get_model_name
from application.state.lead_state import DecisionContext, LeadState
from application.utils.stage_logger import StageTimer, stage_span
from core.domain.schemas.lead import LeadUpdate
from core.infrastructure.database.crud import (
    create_lead_enrichment_log,
    create_scraping_log,
    get_lead,
    update_lead,
)
from core.infrastructure.logging import get_logger, log_enrichment_attempt, log_scraping_attempt

logger = get_logger("application.workflow.nodes")

_FREE_TIER_MESSAGE = "No outreach message generated - AI features not available on your plan"


async def _run_stage(
    stage_name: str,
    lead_id: int,
    errors: List[Dict[str, Any]],
    timings: Dict[str, int],
    coro_fn: Callable[[StageTimer], Coroutine[Any, Any, Any]],
    pipeline_id: Optional[str] = None,
) -> Optional[Any]:
    """Runs `coro_fn(timer)` inside a logged stage span, recording duration
    and catching any exception so the caller can continue the pipeline.

    `timer` is passed into `coro_fn` so stages that call an LLM-backed
    agent can set `timer.retry_count` before the block exits -- the
    completion/failure log line then includes that retry count.
    """
    timer = StageTimer()
    try:
        with stage_span(stage_name, lead_id=lead_id, pipeline_id=pipeline_id) as timer:
            value = await coro_fn(timer)
        timings[stage_name] = timer.duration_ms
        return value
    except Exception as e:
        errors.append({"stage": stage_name, "error": str(e)})
        timings[stage_name] = timer.duration_ms
        return None


def _serialize_scraping_result(result) -> Dict[str, Any]:
    return {
        "success": result.success,
        "method": result.method.value,
        "confidence": result.confidence,
        "processing_time": result.processing_time,
        "error_message": result.error_message,
        "blocked_detected": result.blocked_detected,
        "tiers_attempted": result.tiers_attempted,
        "pages_scraped": result.pages_scraped,
    }


def _serialize_enrichment_result(result) -> Dict[str, Any]:
    method = result.method.value if hasattr(result.method, "value") else str(result.method)
    return {
        "success": result.success,
        "method": method,
        "confidence": result.confidence,
        "processing_time": result.processing_time,
        "data": result.data,
    }


def _serialize_score_result(result) -> Dict[str, Any]:
    return {
        "total_score": result.total_score,
        "qualification_label": result.qualification_label,
        "criteria_scores": result.criteria_scores,
    }


class LeadPipelineNodes:
    """Holds the db session and agent instances shared by every node for a
    single pipeline run. Instantiated fresh per `LeadPipeline` (see
    application/workflows/lead_pipeline.py)."""

    def __init__(self, db: Session):
        self.db = db
        self.context_builder = ContextBuilder(db)
        self.memory = SQLBusinessMemory(db)
        self.company_intelligence_agent = CompanyIntelligenceAgent()
        self.decision_agent = DecisionAgent()
        self.review_agent = ReviewAgent()
        self.messaging_agent = MessagingAgent()

    # -- Stage: Scraper -------------------------------------------------

    async def scrape(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))

        async def _do(timer: StageTimer):
            lead = get_lead(self.db, lead_id)
            result = await infra_adapters.scrape_lead(lead.website)

            create_scraping_log(
                self.db,
                lead_id=lead.id,
                scraping_method=result.method.value,
                success=result.success,
                confidence_score=result.confidence,
                error_message=result.error_message,
                processing_time_ms=(
                    int(result.processing_time * 1000) if result.processing_time else None
                ),
                scraped_data=json.dumps(result.data, default=str) if result.data else None,
                pipeline_id=pipeline_id,
                organization_id=lead.organization_id,
            )
            log_scraping_attempt(
                logger=logger,
                url=lead.website,
                method=result.method.value,
                success=result.success,
                confidence=result.confidence,
                processing_time=result.processing_time or 0,
                error_message=result.error_message,
            )

            if result.success:
                update_fields: Dict[str, Any] = {
                    "scrape_confidence": result.confidence,
                    "scrape_source": result.method.value,
                }
                data = result.data
                if data.get("title"):
                    update_fields["company_name"] = data["title"]
                if data.get("description"):
                    update_fields["about_text"] = data["description"]
                if data.get("og_description"):
                    update_fields["about_text"] = data["og_description"]
                if data.get("email"):
                    update_fields["email"] = data["email"]
                if data.get("phone"):
                    update_fields["phone"] = data["phone"]
                if data.get("linkedin_url"):
                    update_fields["linkedin_url"] = data["linkedin_url"]
                else:
                    for link in data.get("links", []) or []:
                        if "linkedin.com" in link:
                            update_fields["linkedin_url"] = link
                            break
                # Bug fix (integration audit #1, #2): the scraper already
                # extracts twitter_url/facebook_url/address (see
                # core/infrastructure/scraping/scraper.py) and both columns
                # exist on the Lead model + are already accepted by
                # LeadUpdate (core/domain/schemas/lead.py), but this stage
                # never copied them from `data` into `update_fields`, so
                # they stayed NULL in the database no matter what the
                # scraper found. Mirrors the linkedin_url handling above.
                if data.get("twitter_url"):
                    update_fields["twitter_url"] = data["twitter_url"]
                else:
                    for link in data.get("links", []) or []:
                        if "twitter.com" in link or "x.com" in link:
                            update_fields["twitter_url"] = link
                            break
                if data.get("facebook_url"):
                    update_fields["facebook_url"] = data["facebook_url"]
                else:
                    for link in data.get("links", []) or []:
                        if "facebook.com" in link:
                            update_fields["facebook_url"] = link
                            break
                if data.get("address"):
                    update_fields["address"] = data["address"]
                update_lead(self.db, lead_id, LeadUpdate(**update_fields))

            return result

        result = await _run_stage("scrape", lead_id, errors, timings, _do, pipeline_id)

        return {
            "scraping_result": _serialize_scraping_result(result) if result else None,
            "scraped_data": (result.data if result and result.success else {}),
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Enrichment (existing WaterfallEnricher, unchanged) ----------

    async def enrich(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))
        ai_enabled = state.get("ai_features_enabled", False)
        scraped_data = state.get("scraped_data", {})

        async def _do(timer: StageTimer):
            lead = get_lead(self.db, lead_id)
            if not ai_enabled:
                logger.info(
                    "AI features not enabled for organization; skipping enrichment",
                    extra={"lead_id": lead_id, "organization_id": lead.organization_id},
                )
                return None

            result = await asyncio.to_thread(infra_adapters.enrich_lead, lead, scraped_data)
            if not result:
                return None

            method = (
                result.method.value if hasattr(result.method, "value") else str(result.method)
            )
            create_lead_enrichment_log(
                self.db,
                lead_id=lead.id,
                enrichment_type=method,
                enrichment_data=json.dumps(result.data, default=str),
                confidence_score=result.confidence,
                processing_time_ms=result.processing_time,
                pipeline_id=pipeline_id,
                organization_id=lead.organization_id,
            )
            log_enrichment_attempt(
                logger=logger,
                lead_id=lead.id,
                method=method,
                success=True,
                confidence=result.confidence,
                processing_time=result.processing_time or 0,
            )

            update_fields: Dict[str, Any] = {
                "enrichment_confidence": result.confidence,
                "enrichment_source": method,
            }
            for key in (
                "industry",
                "employees",
                "revenue_band",
                "founded_year",
                "contact_name",
                "contact_title",
            ):
                if key in result.data:
                    update_fields[key] = result.data[key]
            update_lead(self.db, lead_id, LeadUpdate(**update_fields))

            return result

        result = await _run_stage("enrich", lead_id, errors, timings, _do, pipeline_id)

        return {
            "enrichment_result": _serialize_enrichment_result(result) if result else None,
            "enriched_data": result.data if result else {},
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Company Intelligence -----------------------------------------

    async def company_intelligence(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))
        ai_enabled = state.get("ai_features_enabled", False)

        async def _do(timer: StageTimer):
            lead = get_lead(self.db, lead_id)
            context = self.context_builder.build(
                lead,
                scraped_data=state.get("scraped_data", {}),
                enriched_data=state.get("enriched_data", {}),
            )
            output: CompanyIntelligenceOutput = await asyncio.to_thread(
                self.company_intelligence_agent.run, context, ai_enabled
            )

            self.memory.store(
                lead_id=lead.id,
                organization_id=lead.organization_id,
                stage="company_intelligence",
                agent_name=self.company_intelligence_agent.name,
                output_data=output.model_dump(exclude={"explanation"}),
                reasoning=output.explanation.reasoning,
                evidence=output.explanation.evidence,
                confidence=output.explanation.confidence,
                # P0-D fix: this previously stored `output.source` (e.g.
                # "llm"/"heuristic") into `model_used`, which is supposed
                # to identify the actual model (e.g. "openai/gpt-oss-120b")
                # -- confirmed by cross-referencing D1's requirement
                # ("persist the model identity at execution time... do not
                # infer it later"), which this column could not have
                # satisfied as written. `source` (added below) now
                # explicitly carries the llm/heuristic/fallback
                # distinction; `model_used` now only carries the actual
                # model identity, and only when a model was truly invoked.
                model_used=get_model_name() if output.source == "llm" else None,
                pipeline_id=pipeline_id,
                source=output.source,
                evaluation_version=EVALUATION_VERSION,
            )

            if output.prompt_name and pipeline_id:
                timer.retry_count = output.retry_count
                observability_repo.create_prompt_execution_record(
                    self.db,
                    pipeline_id=pipeline_id,
                    lead_id=lead.id,
                    organization_id=lead.organization_id,
                    agent_name=self.company_intelligence_agent.name,
                    prompt_name=output.prompt_name,
                    prompt_version=output.prompt_version or "unknown",
                    retry_count=output.retry_count,
                    model=get_model_name(),
                )

            return context, output

        result = await _run_stage(
            "company_intelligence", lead_id, errors, timings, _do, pipeline_id
        )
        context, output = result if result else (None, None)

        return {
            "context": context.model_dump() if context else state.get("context", {}),
            "company_intelligence": output.model_dump() if output else None,
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Lead Qualification (existing LeadScoringService, unchanged) -

    async def qualification(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))
        company_intelligence_dict = state.get("company_intelligence")

        async def _do(timer: StageTimer):
            lead = get_lead(self.db, lead_id)
            company_intelligence = (
                CompanyIntelligenceOutput(**company_intelligence_dict)
                if company_intelligence_dict
                else None
            )
            result = await asyncio.to_thread(
                infra_adapters.score_lead, lead, company_intelligence
            )
            update_lead(
                self.db,
                lead_id,
                LeadUpdate(
                    score=result.total_score,
                    qualification_label=result.qualification_label,
                ),
            )
            return result

        result = await _run_stage("qualification", lead_id, errors, timings, _do, pipeline_id)

        return {
            "score_result": _serialize_score_result(result) if result else None,
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Decision Engine -----------------------------------------------

    async def decision(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))
        ai_enabled = state.get("ai_features_enabled", False)
        score_result = state.get("score_result") or {}
        context_dict = state.get("context") or {}

        async def _do(timer: StageTimer):
            from application.state.lead_state import LeadContext

            lead_context = LeadContext(**context_dict) if context_dict else None
            if lead_context is None:
                lead = get_lead(self.db, lead_id)
                lead_context = self.context_builder.build(lead)

            decision_context = DecisionContext(
                lead_context=lead_context,
                company_intelligence=state.get("company_intelligence"),
                score=score_result.get("total_score", 0.0),
                qualification_label=score_result.get("qualification_label", "Low Priority"),
                scrape_confidence=(state.get("scraping_result") or {}).get("confidence", 0.0),
                enrichment_confidence=(state.get("enrichment_result") or {}).get(
                    "confidence", 0.0
                ),
            )

            output: DecisionOutput = await asyncio.to_thread(
                self.decision_agent.run, decision_context, ai_enabled
            )

            self.memory.store(
                lead_id=lead_id,
                organization_id=state["organization_id"],
                stage="decision",
                agent_name=self.decision_agent.name,
                output_data=output.model_dump(exclude={"explanation"}),
                reasoning=output.explanation.reasoning,
                evidence=output.explanation.evidence,
                confidence=output.explanation.confidence,
                # P0-D fix -- see the identical note at the
                # company_intelligence call site above.
                model_used=get_model_name() if output.source == "llm" else None,
                pipeline_id=pipeline_id,
                source=output.source,
                evaluation_version=EVALUATION_VERSION,
            )

            if output.prompt_name and pipeline_id:
                timer.retry_count = output.retry_count
                observability_repo.create_prompt_execution_record(
                    self.db,
                    pipeline_id=pipeline_id,
                    lead_id=lead_id,
                    organization_id=state["organization_id"],
                    agent_name=self.decision_agent.name,
                    prompt_name=output.prompt_name,
                    prompt_version=output.prompt_version or "unknown",
                    retry_count=output.retry_count,
                    model=get_model_name(),
                )

            return output

        output = await _run_stage("decision", lead_id, errors, timings, _do, pipeline_id)

        return {
            "decision": output.model_dump() if output else None,
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Confidence Evaluation (deterministic, no LLM) ----------------

    async def confidence_evaluation(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))

        async def _do(timer: StageTimer):
            decision = state.get("decision") or {}
            explanation = decision.get("explanation") or {}
            score_result = state.get("score_result") or {}
            context = state.get("context") or {}
            company_intel = state.get("company_intelligence") or {}
            scraping_result = state.get("scraping_result") or {}
            enrichment_result = state.get("enrichment_result") or {}

            # Metrics-audit fix (average_grounding was ~0.02 in production
            # despite genuinely-grounded evidence): source_text previously
            # covered only about_text/scraped_data/industry_analysis, but
            # the Decision Agent's own prompt
            # (application/prompts/templates/decision_v1.yaml) explicitly
            # instructs it to draw "evidence" from pain_points,
            # growth_indicators, the deterministic score,
            # qualification_label, and icp_alignment_score too (see
            # DecisionAgent._decide_with_llm's `inputs` dict) -- and its
            # deterministic fallback (_decide_deterministically) produces
            # evidence strings made *entirely* of the score/confidence
            # values below, never of scraped prose at all. None of that
            # was in source_text, so genuinely-grounded evidence citing
            # those fields was scored as ungrounded purely because the
            # reference text didn't contain what was being checked
            # against -- this expands source_text to cover every field
            # evidence can legitimately be drawn from in either path, not
            # a change to evaluate_grounding's formula itself.
            score = score_result.get("total_score")
            qualification_label = score_result.get("qualification_label")
            icp_alignment_score = company_intel.get("icp_alignment_score")
            scrape_confidence = scraping_result.get("confidence")
            enrichment_confidence = enrichment_result.get("confidence")

            source_text = " ".join(
                filter(
                    None,
                    [
                        context.get("about_text"),
                        (context.get("scraped_data") or {}).get("text_content"),
                        str(company_intel.get("industry_analysis") or ""),
                        ", ".join(company_intel.get("pain_points") or []),
                        ", ".join(company_intel.get("growth_indicators") or []),
                        f"Deterministic score: {round(score, 1)}/100"
                        if score is not None
                        else None,
                        f"Qualification label: {qualification_label}"
                        if qualification_label
                        else None,
                        f"ICP alignment score: {icp_alignment_score}"
                        if icp_alignment_score is not None
                        else None,
                        f"Scrape confidence: {round(scrape_confidence, 2)}"
                        if scrape_confidence is not None
                        else None,
                        f"Enrichment confidence: {round(enrichment_confidence, 2)}"
                        if enrichment_confidence is not None
                        else None,
                    ],
                )
            )

            report: EvaluationReport = build_evaluation_report(
                self_reported_confidence=explanation.get("confidence", 0.0),
                fields=decision,
                expected_fields=["qualification", "recommended_action"],
                evidence=explanation.get("evidence", []),
                source_text=source_text,
                qualification_label=score_result.get("qualification_label", ""),
                decision_qualification=decision.get("qualification"),
            )

            self.memory.store(
                lead_id=lead_id,
                organization_id=state["organization_id"],
                stage="evaluation",
                agent_name="confidence_evaluator",
                output_data=report.model_dump(),
                confidence=report.overall,
                completeness_score=report.completeness,
                grounding_score=report.grounding,
                consistency_score=report.consistency,
                pipeline_id=pipeline_id,
                source="deterministic",
                evaluation_version=EVALUATION_VERSION,
            )

            if pipeline_id:
                observability_repo.create_evaluation_report_record(
                    self.db,
                    pipeline_id=pipeline_id,
                    lead_id=lead_id,
                    organization_id=state["organization_id"],
                    confidence=report.confidence,
                    completeness=report.completeness,
                    grounding=report.grounding,
                    consistency=report.consistency,
                    overall=report.overall,
                    prompt_version=decision.get("prompt_version"),
                    evaluation_version=EVALUATION_VERSION,
                )

            return report

        report = await _run_stage(
            "confidence_evaluation", lead_id, errors, timings, _do, pipeline_id
        )

        return {
            "evaluation": report.model_dump() if report else None,
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Review Decision (deterministic thresholding, no LLM) --------

    async def review(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))

        async def _do(timer: StageTimer):
            evaluation_dict = state.get("evaluation") or {}
            evaluation = EvaluationReport(**evaluation_dict) if evaluation_dict else EvaluationReport()
            output: ReviewOutput = self.review_agent.run(evaluation)

            self.memory.store(
                lead_id=lead_id,
                organization_id=state["organization_id"],
                stage="review",
                agent_name=self.review_agent.name,
                output_data=output.model_dump(),
                review_status=output.decision,
                pipeline_id=pipeline_id,
                source="deterministic",
                evaluation_version=EVALUATION_VERSION,
            )
            return output

        output = await _run_stage("review", lead_id, errors, timings, _do, pipeline_id)

        return {
            "review": output.model_dump() if output else None,
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Message Generation --------------------------------------------

    async def message_generation(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))
        ai_enabled = state.get("ai_features_enabled", False)
        review = state.get("review") or {}
        context_dict = state.get("context") or {}
        decision_dict = state.get("decision") or {}

        async def _do(timer: StageTimer):
            lead = get_lead(self.db, lead_id)

            # Mirrors the pre-existing product/business gating: outreach
            # message generation (even the deterministic template path) is
            # an AI-tier feature, exactly as it was in
            # core/infrastructure/workers/orchestrator.py.
            if not ai_enabled:
                update_lead(
                    self.db, lead_id, LeadUpdate(outreach_message=_FREE_TIER_MESSAGE)
                )
                return MessagingOutput(
                    email_body=_FREE_TIER_MESSAGE,
                    channel_notes="AI features not available on this subscription plan.",
                )

            # Skip generation entirely if flagged for human review -- a low
            # -confidence decision should not auto-generate outreach copy.
            if review.get("decision") == "human_review":
                logger.info(
                    "Skipping message generation: lead flagged for human review",
                    extra={"lead_id": lead_id},
                )
                return None

            from application.state.lead_state import LeadContext

            lead_context = LeadContext(**context_dict) if context_dict else self.context_builder.build(lead)
            decision = DecisionOutput(**decision_dict) if decision_dict else DecisionOutput()

            output: MessagingOutput = await asyncio.to_thread(
                self.messaging_agent.run, lead, lead_context, decision, True
            )

            update_lead(
                self.db, lead_id, LeadUpdate(outreach_message=output.email_body or "")
            )

            self.memory.store(
                lead_id=lead_id,
                organization_id=state["organization_id"],
                stage="messaging",
                agent_name=self.messaging_agent.name,
                output_data=output.model_dump(exclude={"explanation"}),
                reasoning=output.explanation.reasoning,
                confidence=output.explanation.confidence,
                # P0-D fix -- see the identical note at the
                # company_intelligence call site above.
                model_used=get_model_name() if output.source == "llm" else None,
                pipeline_id=pipeline_id,
                source=output.source,
                evaluation_version=EVALUATION_VERSION,
            )

            if output.prompt_name and pipeline_id:
                timer.retry_count = output.retry_count
                observability_repo.create_prompt_execution_record(
                    self.db,
                    pipeline_id=pipeline_id,
                    lead_id=lead_id,
                    organization_id=state["organization_id"],
                    agent_name=self.messaging_agent.name,
                    prompt_name=output.prompt_name,
                    prompt_version=output.prompt_version or "unknown",
                    retry_count=output.retry_count,
                    model=get_model_name(),
                )

            return output

        output = await _run_stage(
            "message_generation", lead_id, errors, timings, _do, pipeline_id
        )

        return {
            "message": output.model_dump() if output else None,
            "stage_timings_ms": timings,
            "errors": errors,
        }

    # -- Stage: Persistence ----------------------------------------------------

    async def persistence(self, state: LeadState) -> Dict[str, Any]:
        """
        Most fields are already committed incrementally by earlier stages
        (matching the original orchestrator's pattern of persisting after
        each step, which also gives partial recovery if the process dies
        mid-pipeline). This stage is the final commit/consistency point.
        The pipeline's overall final_status (SUCCESS/PARTIAL_SUCCESS/FAILED)
        is computed by LeadPipeline.execute() after the graph completes,
        since only it has visibility into the full error list.
        """
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))

        async def _do(timer: StageTimer):
            self.db.commit()
            return True

        await _run_stage("persistence", lead_id, errors, timings, _do, pipeline_id)

        return {"stage_timings_ms": timings, "errors": errors}

    # -- Stage: Analytics --------------------------------------------------------

    async def analytics(self, state: LeadState) -> Dict[str, Any]:
        lead_id = state["lead_id"]
        pipeline_id = state.get("pipeline_id")
        errors = list(state.get("errors", []))
        timings = dict(state.get("stage_timings_ms", {}))

        async def _do(timer: StageTimer):
            logger.info(
                "Lead pipeline analytics",
                extra={
                    "event": "pipeline_analytics",
                    "pipeline_id": pipeline_id,
                    "lead_id": lead_id,
                    "organization_id": state.get("organization_id"),
                    "ai_features_enabled": state.get("ai_features_enabled"),
                    "scraping_success": (state.get("scraping_result") or {}).get("success"),
                    "enrichment_success": bool(state.get("enrichment_result")),
                    "qualification_label": (state.get("score_result") or {}).get(
                        "qualification_label"
                    ),
                    "decision": (state.get("decision") or {}).get("qualification"),
                    "review_decision": (state.get("review") or {}).get("decision"),
                    "evaluation_overall": (state.get("evaluation") or {}).get("overall"),
                    "stage_timings_ms": timings,
                    "error_count": len(errors),
                },
            )
            return True

        await _run_stage("analytics", lead_id, errors, timings, _do, pipeline_id)

        return {"stage_timings_ms": timings, "errors": errors}