"""
Data Transfer Objects for the Application layer.

Every agent output embeds `reasoning`, `evidence`, and `confidence` -- this
is what "no black-box outputs" means in practice: the DTO shape itself
enforces explainability, rather than relying on agents to remember to add it.
"""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PipelineStatus(str, Enum):
    """Terminal status of a single LeadPipeline execution. See
    application.workflows.lead_pipeline for how this is computed and
    application.observability for how it is persisted/aggregated."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


class Explanation(BaseModel):
    """Standard explainability envelope. See application.explainability."""

    reasoning: str = Field(default="", description="Why the agent reached this output")
    evidence: List[str] = Field(
        default_factory=list, description="Short, concrete supporting facts"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CompanyIntelligenceOutput(BaseModel):
    """Output of the Company Intelligence Agent. Analysis only - no outreach."""

    industry_analysis: Optional[str] = None
    website_quality: Optional[str] = None
    technology_signals: List[str] = Field(default_factory=list)
    market_position: Optional[str] = None
    pain_points: List[str] = Field(default_factory=list)
    growth_indicators: List[str] = Field(default_factory=list)
    icp_alignment_score: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: Explanation = Field(default_factory=Explanation)
    source: str = Field(default="heuristic", description="heuristic | llm")
    # Prompt version tracking (populated only when `source == "llm"`; see
    # application.observability for where this is persisted).
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    retry_count: int = 0


class DecisionOutput(BaseModel):
    """Output of the Decision Agent."""

    qualification: str = Field(
        default="Unqualified",
        description="Hot Lead | Warm Lead | Cold Lead | Disqualified",
    )
    recommended_action: str = Field(
        default="review", description="proceed | review | reject"
    )
    explanation: Explanation = Field(default_factory=Explanation)
    source: str = Field(default="rule_based", description="rule_based | llm")
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    retry_count: int = 0


class EvaluationReport(BaseModel):
    """Output of the (deterministic, non-LLM) Confidence Evaluation stage."""

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    completeness: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding: float = Field(default=0.0, ge=0.0, le=1.0)
    consistency: float = Field(default=0.0, ge=0.0, le=1.0)
    overall: float = Field(default=0.0, ge=0.0, le=1.0)
    notes: List[str] = Field(default_factory=list)


class ReviewOutput(BaseModel):
    """Output of the Review Agent. Pure routing decision, no LLM call."""

    decision: str = Field(
        default="human_review",
        description="auto_approved | flagged | human_review",
    )
    reason: str = ""
    threshold_used: Optional[float] = None


class MessagingOutput(BaseModel):
    """Output of the Messaging Agent. Communication only - no qualification."""

    email_subject: Optional[str] = None
    email_body: Optional[str] = None
    linkedin_opener: Optional[str] = None
    follow_up_strategy: Optional[str] = None
    channel_notes: Optional[str] = None
    explanation: Explanation = Field(default_factory=Explanation)
    source: str = Field(default="template", description="template | llm")
    prompt_name: Optional[str] = None
    prompt_version: Optional[str] = None
    retry_count: int = 0


class PipelineResult(BaseModel):
    """Top-level result returned by LeadPipeline.execute()."""

    pipeline_id: Optional[str] = None
    lead_id: int
    status: PipelineStatus = PipelineStatus.FAILED
    ai_features_enabled: bool = False
    scraping_success: bool = False
    enrichment_success: bool = False
    company_intelligence: Optional[CompanyIntelligenceOutput] = None
    score: Optional[float] = None
    qualification_label: Optional[str] = None
    decision: Optional[DecisionOutput] = None
    evaluation: Optional[EvaluationReport] = None
    review: Optional[ReviewOutput] = None
    message: Optional[MessagingOutput] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_ms: Optional[int] = None
    stage_timings_ms: Dict[str, int] = Field(default_factory=dict)
    errors: List[Dict[str, Any]] = Field(default_factory=list)


class PipelineMetricsSummary(BaseModel):
    """Aggregated pipeline execution metrics, served by the Analytics API."""

    total_runs: int = 0
    success_count: int = 0
    partial_success_count: int = 0
    failed_count: int = 0
    success_rate_pct: float = 0.0
    avg_processing_time_ms: float = 0.0
    median_processing_time_ms: float = 0.0
    p95_processing_time_ms: float = 0.0


class EvaluationMetricsSummary(BaseModel):
    """Aggregated evaluation-report metrics, served by the Analytics API."""

    total_evaluations: int = 0
    average_overall_score: float = 0.0
    average_confidence: float = 0.0
    average_completeness: float = 0.0
    average_grounding: float = 0.0
    average_consistency: float = 0.0


class DiscoveryMetricsSummary(BaseModel):
    """Aggregated Business Discovery Layer metrics, served by the
    Analytics API. See application.discovery and
    application.observability.metrics_service for how these are computed."""

    total_discovery_runs: int = 0
    total_businesses_found: int = 0
    total_leads_created: int = 0
    discovery_success_rate_pct: float = 0.0
    website_resolution_rate_pct: float = 0.0
    duplicate_removal_rate_pct: float = 0.0
    avg_discovery_time_ms: float = 0.0
