"""
Lead model for the LeadBoost SaaS platform
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    Float,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from core.infrastructure.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Core company information
    company_name = Column(String, nullable=True)
    website = Column(String, nullable=False)
    industry = Column(String, nullable=True)
    about_text = Column(Text, nullable=True)

    # Contact information
    contact_name = Column(String, nullable=True)
    contact_title = Column(String, nullable=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)

    # Social profiles
    linkedin_url = Column(String, nullable=True)
    twitter_url = Column(String, nullable=True)
    facebook_url = Column(String, nullable=True)

    # Company metrics
    employees = Column(String, nullable=True)  # e.g., "1-10", "11-50", etc.
    revenue_band = Column(String, nullable=True)  # e.g., "$0-1M", "$1M-10M", etc.
    founded_year = Column(Integer, nullable=True)

    # Lead scoring
    score = Column(Float, default=0.0)
    qualification_label = Column(String, default="Low Priority")

    # Data quality metrics
    scrape_confidence = Column(Float, default=0.0)
    email_confidence = Column(Float, default=0.0)
    enrichment_confidence = Column(Float, default=0.0)

    # Sources and metadata
    enrichment_source = Column(String, default="none")  # heuristic, llm, external_api
    email_source = Column(String, default="none")
    scrape_source = Column(String, default="none")

    # Outreach
    outreach_message = Column(Text, nullable=True)
    outreach_sent = Column(Boolean, default=False)
    outreach_sent_at = Column(DateTime(timezone=True), nullable=True)

    # Status and tracking
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="leads")
    owner = relationship("User", back_populates="leads")
    enrichment_logs = relationship("LeadEnrichmentLog", back_populates="lead")
    scraping_logs = relationship("ScrapingLog", back_populates="lead")

    # Production-robustness hardening, Phase A5 (lead-creation race):
    # every existing creation call site (api/endpoints/leads.py x2,
    # application/discovery/discovery_service.py) already checks
    # get_lead_by_url(organization_id, website) before creating -- but
    # that check-then-insert is not itself atomic, so two concurrent
    # identical submissions (duplicate click, client retry racing the
    # original request) could previously both pass the check and both
    # insert, producing two Lead rows for the same site and two
    # redundant full pipeline runs. This constraint makes the guarantee
    # the existing dedup check was already assuming actually hold at the
    # database level; the losing insert raises IntegrityError, which
    # every creation call site now catches and treats exactly like an
    # ordinary pre-existing-lead result (see get_lead_by_url usage at
    # each call site).
    #
    # NOTE on rollout: this project creates its schema via
    # `Base.metadata.create_all()` (no Alembic migrations are wired up --
    # see other additive tables in this codebase, e.g.
    # PipelineExecutionRecord), which only creates *missing* tables/
    # constraints and will not retroactively ALTER an already-existing
    # `leads` table in a database that predates this change. A fresh
    # database gets this constraint automatically; an already-running
    # deployment needs a one-time manual migration (see the final report
    # for the exact statement and the required de-duplication step).
    __table_args__ = (
        UniqueConstraint("organization_id", "website", name="uq_leads_org_website"),
    )


class LeadEnrichmentLog(Base):
    __tablename__ = "lead_enrichment_logs"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    enrichment_type = Column(String, nullable=False)  # heuristic, llm, external_api
    enrichment_data = Column(Text)  # JSON string of enrichment results
    confidence_score = Column(Float, default=0.0)
    processing_time_ms = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    lead = relationship("Lead", back_populates="enrichment_logs")


class ScrapingLog(Base):
    __tablename__ = "scraping_logs"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    scraping_method = Column(
        String, nullable=False
    )  # json_ld, structured_data, playwright, etc.
    success = Column(Boolean, default=False)
    error_message = Column(Text, nullable=True)
    confidence_score = Column(Float, default=0.0)
    processing_time_ms = Column(Integer)
    scraped_data = Column(Text)  # JSON string of scraped data
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    lead = relationship("Lead", back_populates="scraping_logs")


class AIDecisionLog(Base):
    """
    Audit trail for every AI agent decision made by the Application layer.

    This is the single additive schema change introduced for the new
    AI Application layer (backend/application/). It follows the exact same
    pattern as ScrapingLog / LeadEnrichmentLog above (a lead-scoped,
    append-only log table) so it fits naturally alongside the existing
    logging tables rather than introducing a new data-access pattern.

    It serves three purposes simultaneously, by design, to avoid
    proliferating multiple near-identical tables:
      1. Explainability: every row is a self-contained record of what an
         agent decided, why (reasoning + evidence), and how confident it was.
      2. Business memory: application/memory reads this table to recall
         previous company analyses, decisions, and outreach for a lead.
      3. Evaluation/analytics: confidence/completeness/grounding scores are
         stored per stage to support future continuous evaluation.

    No existing table or column is modified. This table is only ever
    written to by backend/application/ and is additive-only.
    """

    __tablename__ = "ai_decision_logs"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False)

    # Which pipeline stage / agent produced this record
    stage = Column(
        String, nullable=False, index=True
    )  # company_intelligence, decision, review, messaging, evaluation
    agent_name = Column(String, nullable=False)

    # Structured agent output (JSON-serialized DTO)
    output_data = Column(Text, nullable=True)

    # Explainability
    reasoning = Column(Text, nullable=True)
    evidence = Column(Text, nullable=True)  # JSON list of supporting evidence strings
    confidence = Column(Float, default=0.0)

    # Evaluation metrics (see application/evaluation)
    completeness_score = Column(Float, nullable=True)
    grounding_score = Column(Float, nullable=True)
    consistency_score = Column(Float, nullable=True)

    # Review Agent routing outcome, when applicable
    review_status = Column(String, nullable=True)  # auto_approved, flagged, human_review

    # Provenance
    model_used = Column(String, nullable=True)
    prompt_name = Column(String, nullable=True)
    prompt_version = Column(String, nullable=True)
    processing_time_ms = Column(Integer, nullable=True)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    lead = relationship("Lead")
