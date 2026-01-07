"""
Pydantic schemas for Lead model
"""

from pydantic import BaseModel
from typing import Optional
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
