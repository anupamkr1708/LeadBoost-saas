"""
Workflow state.

`LeadState` is the single object threaded through every LangGraph node.
Each node reads what it needs from it and returns a partial dict that
LangGraph merges back in -- nodes never receive or pass around dozens of
loose parameters.

`LeadContext` / `DecisionContext` are typed, immutable-by-convention bundles
built once (by application.context.ContextBuilder) and handed to agents, so
an agent's signature is `analyze(context: LeadContext)` rather than
`analyze(lead, org, scraped, enriched, history, memory, ...)`.
"""

from typing import Any, Dict, List, Optional, TypedDict

from pydantic import BaseModel, Field


class LeadState(TypedDict, total=False):
    # Identity
    pipeline_id: str
    lead_id: int
    organization_id: int
    ai_features_enabled: bool

    # Stage 0: snapshot of the lead row at pipeline start
    lead_snapshot: Dict[str, Any]

    # Stage 1: Scraper
    scraping_result: Optional[Dict[str, Any]]
    scraped_data: Dict[str, Any]

    # Stage 2: Enrichment
    enrichment_result: Optional[Dict[str, Any]]
    enriched_data: Dict[str, Any]

    # Built once, reused by every downstream agent
    context: Dict[str, Any]

    # Stage 3: Company Intelligence
    company_intelligence: Optional[Dict[str, Any]]

    # Stage 4: Lead Qualification (deterministic scoring)
    score_result: Optional[Dict[str, Any]]

    # Stage 5: Decision Engine
    decision: Optional[Dict[str, Any]]

    # Stage 6: Confidence Evaluation
    evaluation: Optional[Dict[str, Any]]

    # Stage 7: Review Decision
    review: Optional[Dict[str, Any]]

    # Stage 8: Message Generation
    message: Optional[Dict[str, Any]]

    # Bookkeeping
    stage_timings_ms: Dict[str, int]
    errors: List[Dict[str, Any]]
    status: str


class LeadContext(BaseModel):
    """Combined context an agent reasons over. Built once per pipeline run."""

    lead_id: int
    organization_id: int

    company_name: Optional[str] = None
    website: str
    industry: Optional[str] = None
    about_text: Optional[str] = None
    employees: Optional[str] = None
    revenue_band: Optional[str] = None
    founded_year: Optional[int] = None

    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    linkedin_url: Optional[str] = None

    scraped_data: Dict[str, Any] = Field(default_factory=dict)
    enriched_data: Dict[str, Any] = Field(default_factory=dict)

    # Organization-level context (the "sender side")
    organization_name: Optional[str] = None
    sender_org: Optional[str] = None

    # CRM / history (pluggable extension point; empty until a CRM
    # integration exists -- see application.context.context_builder)
    crm_history: List[Dict[str, Any]] = Field(default_factory=list)

    # Business memory (see application.memory)
    previous_company_analysis: Optional[Dict[str, Any]] = None
    previous_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    previous_outreach: List[Dict[str, Any]] = Field(default_factory=list)

    def analysis_text(self, max_chars: int = 4000) -> str:
        """Flatten the context into a single text blob for LLM prompts."""
        parts = []
        if self.company_name:
            parts.append(f"Company: {self.company_name}")
        parts.append(f"Website: {self.website}")
        if self.industry:
            parts.append(f"Industry: {self.industry}")
        if self.employees:
            parts.append(f"Employees: {self.employees}")
        if self.about_text:
            parts.append(f"About: {self.about_text}")
        text_content = self.scraped_data.get("text_content")
        if text_content:
            parts.append(f"Website content: {text_content}")
        return "\n".join(parts)[:max_chars]


class DecisionContext(BaseModel):
    """Bundle passed to the Decision Agent."""

    lead_context: LeadContext
    company_intelligence: Optional[Dict[str, Any]] = None
    score: float = 0.0
    qualification_label: str = "Low Priority"
    scrape_confidence: float = 0.0
    enrichment_confidence: float = 0.0
