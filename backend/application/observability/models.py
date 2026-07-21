"""
Observability models.

Three new, additive tables, each mapping directly to one of the production
-polish requirements:

  PipelineExecutionRecord  -> Pipeline Metrics (#1)
  EvaluationReportRecord   -> Evaluation Persistence (#3)
  PromptExecutionRecord    -> Prompt Version Tracking (#4)

These reuse the existing `Base`/engine from core.infrastructure.database
(the same SQLAlchemy metadata Lead/Organization/etc. are registered on),
so `init_db()` picks them up automatically -- no new database, no schema
migration tooling introduced.

Deliberately kept separate from core.domain.models.lead.AIDecisionLog:
that table is business-memory/explainability data agents themselves read
back (application.memory). These tables are platform operational data
(for the Analytics API and future dashboards) with a different shape
(pipeline_id-keyed, aggregation-friendly) and a different consumer
(operators, not agents). Merging the two would couple two independently
-evolving concerns into one wide table.
"""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.sql import func

from core.infrastructure.database import Base


class PipelineExecutionRecord(Base):
    """One row per LeadPipeline.execute() call. See
    application.workflows.lead_pipeline for how this is written and
    application.observability.metrics_service for how it is aggregated."""

    __tablename__ = "pipeline_execution_logs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(String, unique=True, index=True, nullable=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )

    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # SUCCESS | PARTIAL_SUCCESS | FAILED (see application.dto.models.PipelineStatus)
    final_status = Column(String, nullable=False, index=True)

    stage_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EvaluationReportRecord(Base):
    """One row per Confidence Evaluation stage execution. Persists the
    existing, unmodified EvaluationReport DTO (application.evaluation) for
    future analytics -- the evaluation logic itself is untouched."""

    __tablename__ = "evaluation_report_logs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(String, index=True, nullable=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )

    # Version of the prompt whose output was evaluated (e.g. the Decision
    # Agent's prompt), when the evaluated stage used the LLM path.
    prompt_version = Column(String, nullable=True)

    confidence = Column(Float, default=0.0)
    completeness = Column(Float, default=0.0)
    grounding = Column(Float, default=0.0)
    consistency = Column(Float, default=0.0)
    overall = Column(Float, default=0.0)

    evaluated_at = Column(DateTime(timezone=True), server_default=func.now())


class PromptExecutionRecord(Base):
    """One row per AI-agent LLM-backed prompt execution (Company
    Intelligence / Decision / Messaging agents), capturing which prompt
    and version produced a given pipeline's output, for future prompt
    -performance comparison. Only written when an agent actually used a
    registered prompt template (i.e. `source == "llm"`); rule-based/
    heuristic/template fallback executions have no prompt to track."""

    __tablename__ = "prompt_execution_logs"

    id = Column(Integer, primary_key=True, index=True)
    pipeline_id = Column(String, index=True, nullable=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )

    agent_name = Column(String, nullable=False)
    prompt_name = Column(String, nullable=False)
    prompt_version = Column(String, nullable=False)
    retry_count = Column(Integer, default=0)

    executed_at = Column(DateTime(timezone=True), server_default=func.now())
