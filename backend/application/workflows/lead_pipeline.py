"""
LeadPipeline: the Application layer's primary orchestration workflow,
built with LangGraph.

    Lead Created
        |
        v
     Scraper --> Enrichment --> Company Intelligence --> Lead Qualification
        |                                                        |
        v                                                        v
   Decision Engine --> Confidence Evaluation --> Review Decision
                                                        |
                                     +------------------+------------------+
                                     |                                     |
                              (not human_review)                   (human_review)
                                     v                                     |
                          Message Generation                              |
                                     |                                     |
                                     +------------------+------------------+
                                                        v
                                                  Persistence --> Analytics
                                                        |
                                                        v
                                          [pipeline_id, timing, final_status
                                           recorded to PipelineExecutionRecord]

Existing API endpoints call `LeadPipeline.execute(lead_id)` instead of
manually chaining scrape -> enrich -> score -> message the way
core/infrastructure/workers/orchestrator.py used to (that module is left
untouched for backward compatibility with any existing Celery worker
deployment, but new code paths use this pipeline).

Every execution is assigned a `pipeline_id` (UUID) that is threaded through
`LeadState` into every stage log line and every observability record
(PromptExecutionRecord, EvaluationReportRecord, PipelineExecutionRecord),
so a single execution's full trail can be reconstructed by pipeline_id.
"""

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from application.concurrency import pipeline_lock
from application.dto.models import PipelineResult, PipelineStatus
from application.observability import repository as observability_repo
from application.services import infra_adapters
from application.state.lead_state import LeadState
from application.workflows.graph_nodes import LeadPipelineNodes
from core.infrastructure.database import SessionLocal
from core.infrastructure.database.crud import get_lead
from core.infrastructure.logging import get_logger

logger = get_logger("application.workflow.lead_pipeline")


def _route_after_review(state: LeadState) -> str:
    review = state.get("review") or {}
    if review.get("decision") == "human_review":
        return "skip_message"
    return "generate_message"


class LeadPipeline:
    """Builds and runs the LangGraph lead-processing workflow for a single
    database session. Construct one per request/background task (it is not
    safe to share across concurrent DB sessions)."""

    def __init__(self, db: Session):
        self.db = db
        self.nodes = LeadPipelineNodes(db)
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(LeadState)

        builder.add_node("scrape", self.nodes.scrape)
        builder.add_node("enrich", self.nodes.enrich)
        builder.add_node("analyze_company", self.nodes.company_intelligence)
        builder.add_node("qualification", self.nodes.qualification)
        builder.add_node("decide", self.nodes.decision)
        builder.add_node("confidence_evaluation", self.nodes.confidence_evaluation)
        builder.add_node("review_decision", self.nodes.review)
        builder.add_node("message_generation", self.nodes.message_generation)
        builder.add_node("persistence", self.nodes.persistence)
        builder.add_node("analytics", self.nodes.analytics)

        builder.add_edge(START, "scrape")
        builder.add_edge("scrape", "enrich")
        builder.add_edge("enrich", "analyze_company")
        builder.add_edge("analyze_company", "qualification")
        builder.add_edge("qualification", "decide")
        builder.add_edge("decide", "confidence_evaluation")
        builder.add_edge("confidence_evaluation", "review_decision")
        builder.add_conditional_edges(
            "review_decision",
            _route_after_review,
            {
                "generate_message": "message_generation",
                "skip_message": "persistence",
            },
        )
        builder.add_edge("message_generation", "persistence")
        builder.add_edge("persistence", "analytics")
        builder.add_edge("analytics", END)

        return builder.compile()

    async def execute(self, lead_id: int) -> PipelineResult:
        pipeline_id = str(uuid.uuid4())
        started_at = datetime.now(timezone.utc)
        perf_start = time.time()

        lead = get_lead(self.db, lead_id)
        if lead is None:
            logger.error(f"LeadPipeline.execute: lead {lead_id} not found")
            completed_at = datetime.now(timezone.utc)
            # No PipelineExecutionRecord is written here: there is no valid
            # lead/organization to attach the FK-constrained row to. The
            # failure is still fully visible via the returned PipelineResult
            # and the error log line above.
            return PipelineResult(
                pipeline_id=pipeline_id,
                lead_id=lead_id,
                status=PipelineStatus.FAILED,
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                duration_ms=int((time.time() - perf_start) * 1000),
                errors=[{"error": "Lead not found"}],
            )

        # Production-robustness hardening, Phase A3/A4: refuse to start a
        # second full pipeline run for a lead that already has one in
        # flight (concurrent request, duplicate click, or a retry that
        # arrives before the first attempt has finished). This is a pure
        # mutual-exclusion guard, not a caching layer -- it never
        # suppresses a *later*, non-overlapping reprocess request, which
        # is the actual intended behavior of "manually trigger processing
        # for a lead" (see api/endpoints/leads.py::process_lead_now).
        if not pipeline_lock.try_acquire(self.db, lead.id, lead.organization_id, pipeline_id):
            active = pipeline_lock.get_active(self.db, lead.id)
            active_pipeline_id = active.pipeline_id if active else None
            logger.info(
                "LeadPipeline.execute: lead %s already has a pipeline in flight (pipeline_id=%s); skipping duplicate run",
                lead.id,
                active_pipeline_id,
            )
            completed_at = datetime.now(timezone.utc)
            return PipelineResult(
                pipeline_id=active_pipeline_id or pipeline_id,
                lead_id=lead.id,
                status=PipelineStatus.SKIPPED_IN_PROGRESS,
                started_at=started_at.isoformat(),
                completed_at=completed_at.isoformat(),
                duration_ms=int((time.time() - perf_start) * 1000),
                errors=[
                    {
                        "stage": "pipeline",
                        "error": (
                            "A pipeline is already running for this lead "
                            f"(pipeline_id={active_pipeline_id}). No duplicate "
                            "scraping/enrichment/LLM work was started."
                        ),
                    }
                ],
            )

        ai_features_enabled = infra_adapters.check_ai_features_enabled(
            self.db, lead.organization_id
        )

        initial_state: LeadState = {
            "pipeline_id": pipeline_id,
            "lead_id": lead.id,
            "organization_id": lead.organization_id,
            "ai_features_enabled": ai_features_enabled,
            "lead_snapshot": {"id": lead.id, "website": lead.website},
            "stage_timings_ms": {},
            "errors": [],
        }

        try:
            try:
                final_state: Dict[str, Any] = await self._graph.ainvoke(initial_state)
            except Exception as e:
                # Safety net only: every node already catches its own
                # exceptions (see graph_nodes._run_stage), so this branch
                # guards against an unexpected failure in the graph runtime
                # itself, ensuring a pipeline execution is always recorded.
                logger.error(
                    f"LeadPipeline graph execution failed for lead {lead_id}: {e}", exc_info=True
                )
                completed_at = datetime.now(timezone.utc)
                duration_ms = int((time.time() - perf_start) * 1000)
                self._record_execution(
                    pipeline_id,
                    lead.id,
                    lead.organization_id,
                    started_at,
                    completed_at,
                    duration_ms,
                    PipelineStatus.FAILED,
                    stage_count=0,
                    error_count=1,
                    error_message=str(e)[:4000],
                )
                return PipelineResult(
                    pipeline_id=pipeline_id,
                    lead_id=lead.id,
                    status=PipelineStatus.FAILED,
                    ai_features_enabled=ai_features_enabled,
                    started_at=started_at.isoformat(),
                    completed_at=completed_at.isoformat(),
                    duration_ms=duration_ms,
                    errors=[{"stage": "pipeline", "error": str(e)}],
                )

            completed_at = datetime.now(timezone.utc)
            duration_ms = int((time.time() - perf_start) * 1000)
            errors = final_state.get("errors", [])

            # SUCCESS: every stage completed with no recorded errors.
            # PARTIAL_SUCCESS: the pipeline reached the end but one or more
            # stages degraded gracefully (see graph_nodes._run_stage).
            # FAILED is reserved for the two cases above (lead not found, or
            # an unhandled graph-runtime exception) -- a run that *completes*
            # is, by definition, at worst partially successful.
            final_status = (
                PipelineStatus.SUCCESS if not errors else PipelineStatus.PARTIAL_SUCCESS
            )

            self._record_execution(
                pipeline_id,
                lead.id,
                lead.organization_id,
                started_at,
                completed_at,
                duration_ms,
                final_status,
                stage_count=len(final_state.get("stage_timings_ms", {})),
                error_count=len(errors),
                error_message="; ".join(str(e.get("error", e)) for e in errors)[:4000] if errors else None,
            )

            return self._to_result(
                final_state, pipeline_id, started_at, completed_at, duration_ms, final_status
            )
        finally:
            # Production-robustness hardening, Phase A3/A4: always release
            # the lock, whatever the outcome, so a later (non-overlapping)
            # reprocess request is never blocked by this run.
            pipeline_lock.release(self.db, lead.id)

    def _record_execution(
        self,
        pipeline_id: str,
        lead_id: int,
        organization_id: int,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        final_status: PipelineStatus,
        stage_count: int,
        error_count: int,
        error_message: Optional[str] = None,
    ) -> None:
        """Best-effort: a metrics-write failure must never affect the
        pipeline's actual result, so this only logs on failure."""
        try:
            observability_repo.create_pipeline_execution_record(
                self.db,
                pipeline_id=pipeline_id,
                lead_id=lead_id,
                organization_id=organization_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                final_status=final_status.value,
                stage_count=stage_count,
                error_count=error_count,
                error_message=error_message,
            )
        except Exception as e:
            logger.error(f"Failed to persist pipeline execution record: {e}")

    @staticmethod
    def _to_result(
        state: Dict[str, Any],
        pipeline_id: str,
        started_at: datetime,
        completed_at: datetime,
        duration_ms: int,
        final_status: PipelineStatus,
    ) -> PipelineResult:
        scraping_result = state.get("scraping_result") or {}
        score_result = state.get("score_result") or {}

        return PipelineResult(
            pipeline_id=pipeline_id,
            lead_id=state["lead_id"],
            status=final_status,
            ai_features_enabled=state.get("ai_features_enabled", False),
            scraping_success=scraping_result.get("success", False),
            enrichment_success=bool(state.get("enrichment_result")),
            company_intelligence=state.get("company_intelligence"),
            score=score_result.get("total_score"),
            qualification_label=score_result.get("qualification_label"),
            decision=state.get("decision"),
            evaluation=state.get("evaluation"),
            review=state.get("review"),
            message=state.get("message"),
            started_at=started_at.isoformat(),
            completed_at=completed_at.isoformat(),
            duration_ms=duration_ms,
            stage_timings_ms=state.get("stage_timings_ms", {}),
            errors=state.get("errors", []),
        )


async def run_lead_pipeline(lead_id: int) -> Dict[str, Any]:
    """
    Module-level entry point matching the calling convention of the
    previous `process_lead_async(lead_id)` (see
    core/infrastructure/workers/orchestrator.py), so API endpoints can pass
    it directly to FastAPI's BackgroundTasks. Opens its own DB session --
    background tasks run after the request's own session has already been
    closed by the `get_db` dependency, exactly as the original
    implementation did via `SessionLocal()` inside `process_lead_task`.
    """
    db = SessionLocal()
    try:
        pipeline = LeadPipeline(db)
        result = await pipeline.execute(lead_id)
        return result.model_dump()
    except Exception as e:
        logger.error(f"LeadPipeline run failed for lead_id {lead_id}: {e}", exc_info=True)
        db.rollback()
        return {
            "lead_id": lead_id,
            "status": PipelineStatus.FAILED.value,
            "errors": [{"error": str(e)}],
        }
    finally:
        db.close()
