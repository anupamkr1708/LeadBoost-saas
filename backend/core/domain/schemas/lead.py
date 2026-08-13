"""
Pydantic schemas for Lead model
"""

from pydantic import BaseModel
from typing import Any, Dict, Optional
from datetime import datetime


class LeadBase(BaseModel):
    website: str
    organization_id: int
    owner_id: int


class LeadCreate(LeadBase):
    pass


class LeadUpdate(BaseModel):
    company_name: Optional[str] = None
    industry: Optional[str] = None
    about_text: Optional[str] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    facebook_url: Optional[str] = None
    employees: Optional[str] = None
    # NOTE: the six fields below were previously missing from this schema
    # even though they are real columns on the Lead model (see
    # core/domain/models/lead.py) and core/infrastructure/workers/
    # orchestrator.py already attempted to set them via LeadUpdate(**dict).
    # Because Pydantic v2 silently drops unrecognized constructor kwargs
    # (default extra="ignore") and update_lead() uses
    # `.dict(exclude_unset=True)`, those update calls were a silent no-op:
    # scrape/enrichment confidence, source, score, and qualification_label
    # were never actually persisted. Adding them here is a schema-validation
    # fix, not a database change -- the underlying columns already exist.
    score: Optional[float] = None
    qualification_label: Optional[str] = None
    scrape_confidence: Optional[float] = None
    scrape_source: Optional[str] = None
    enrichment_confidence: Optional[float] = None
    enrichment_source: Optional[str] = None
    revenue_band: Optional[str] = None
    founded_year: Optional[int] = None
    outreach_message: Optional[str] = None
    is_active: Optional[bool] = None


class LeadInDBBase(LeadBase):
    id: int
    company_name: Optional[str] = None
    industry: Optional[str] = None
    about_text: Optional[str] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    linkedin_url: Optional[str] = None
    twitter_url: Optional[str] = None
    facebook_url: Optional[str] = None
    employees: Optional[str] = None
    revenue_band: Optional[str] = None
    founded_year: Optional[int] = None
    score: float
    qualification_label: str
    scrape_confidence: float
    email_confidence: float
    enrichment_confidence: float
    enrichment_source: str
    email_source: str
    scrape_source: str
    outreach_message: Optional[str] = None
    outreach_sent: bool
    outreach_sent_at: Optional[datetime] = None
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class Lead(LeadInDBBase):
    pass


class LeadInDB(LeadInDBBase):
    pass


class LeadDetail(Lead):
    """Response schema for GET /api/v2/leads/{id} only (integration audit
    items #3-#7). Purely additive on top of `Lead`: every existing field
    and its type is unchanged, so this is backward-compatible for any
    existing consumer of the plain `Lead` shape.

    `ai_insights` surfaces the AI outputs that were already being
    computed and persisted (to AIDecisionLog) but never reached this
    response -- Company Intelligence, Decision reasoning/action, the
    Evaluation confidence/completeness/grounding/consistency breakdown,
    Review routing, and full Messaging output (subject/body/LinkedIn
    opener/follow-up strategy, not just the stored `outreach_message`
    body). See application.services.infra_adapters.get_lead_ai_insights.

    Left as `Dict[str, Any]` per stage (rather than importing the
    application-layer DTOs here) so this schema stays a pure read model:
    it always mirrors exactly what the agent produced, with no risk of
    the two shapes drifting apart. Any stage that has not run yet for
    this lead (e.g. `messaging` when routed to human_review) is `None`,
    not an error.
    """

    ai_insights: Optional[Dict[str, Optional[Dict[str, Any]]]] = None


class LeadEnrichmentLogBase(BaseModel):
    lead_id: int
    enrichment_type: str
    enrichment_data: Optional[str] = None
    confidence_score: float
    processing_time_ms: Optional[int] = None


class LeadEnrichmentLogCreate(LeadEnrichmentLogBase):
    pass


class LeadEnrichmentLog(LeadEnrichmentLogBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ScrapingLogBase(BaseModel):
    lead_id: int
    scraping_method: str
    success: bool
    error_message: Optional[str] = None
    confidence_score: float
    processing_time_ms: Optional[int] = None
    scraped_data: Optional[str] = None


class ScrapingLogCreate(ScrapingLogBase):
    pass


class ScrapingLog(ScrapingLogBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
