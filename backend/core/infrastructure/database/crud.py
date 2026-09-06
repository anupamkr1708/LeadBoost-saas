"""
CRUD operations for database models
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from core.domain.models.user import User
from core.domain.models.organization import Organization
from core.domain.models.lead import Lead, LeadEnrichmentLog, ScrapingLog, AIDecisionLog
from core.domain.models.api_key import APIKey
from core.domain.models.billing import Subscription, UsageRecord, Invoice
from core.domain.models.qualification_settings import (
    OrganizationQualificationSettings,
    DEFAULT_QUALIFICATION_THRESHOLD,
)
from core.domain.schemas.user import UserCreate, UserUpdate, UserInDB
from core.domain.schemas.organization import OrganizationCreate, OrganizationUpdate
from core.domain.schemas.qualification_settings import QualificationSettingsUpdate
from core.domain.schemas.lead import LeadCreate, LeadUpdate, LeadInDB
from core.domain.schemas.api_key import APIKeyCreate, APIKeyInDB
from passlib.context import CryptContext
import uuid

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# User CRUD operations
def create_user(db: Session, user: UserCreate, organization_id: int = None) -> UserInDB:
    """Create a new user with optional organization_id"""
    db_user = User(
        email=user.email,
        hashed_password=get_password_hash(user.password),
        first_name=user.first_name,
        last_name=user.last_name,
        organization_id=organization_id,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    """Get a user by email"""
    return db.query(User).filter(User.email == email).first()


def get_user(db: Session, user_id: int) -> Optional[User]:
    """Get a user by ID"""
    return db.query(User).filter(User.id == user_id).first()


def update_user(db: Session, user_id: int, user_update: UserUpdate) -> Optional[User]:
    """Update a user"""
    db_user = get_user(db, user_id)
    if db_user:
        for field, value in user_update.dict(exclude_unset=True).items():
            setattr(db_user, field, value)
        db.commit()
        db.refresh(db_user)
    return db_user


# Organization CRUD operations
def create_organization(db: Session, org: OrganizationCreate) -> Organization:
    """Create a new organization"""
    db_org = Organization(name=org.name, description=org.description)
    db.add(db_org)
    db.commit()
    db.refresh(db_org)
    return db_org


def get_organization(db: Session, org_id: int) -> Optional[Organization]:
    """Get an organization by ID"""
    return db.query(Organization).filter(Organization.id == org_id).first()


def get_organization_by_name(db: Session, name: str) -> Optional[Organization]:
    """Get an organization by name"""
    return db.query(Organization).filter(Organization.name == name).first()


def update_organization(
    db: Session, org_id: int, org_update: OrganizationUpdate
) -> Optional[Organization]:
    """Update an organization"""
    db_org = get_organization(db, org_id)
    if db_org:
        for field, value in org_update.dict(exclude_unset=True).items():
            setattr(db_org, field, value)
        db.commit()
        db.refresh(db_org)
    return db_org


# Lead CRUD operations
def create_lead(db: Session, lead: LeadCreate) -> Lead:
    """Create a new lead"""
    db_lead = Lead(
        website=lead.website,
        organization_id=lead.organization_id,
        owner_id=lead.owner_id,
        score=0.0,
        qualification_label="Low Priority",
        scrape_confidence=0.0,
        email_confidence=0.0,
        enrichment_confidence=0.0,
        enrichment_source="none",
        email_source="none",
        scrape_source="none",
        outreach_sent=False,
        is_active=True,
        is_verified=False,
    )
    db.add(db_lead)
    db.commit()
    db.refresh(db_lead)
    return db_lead


def get_lead(db: Session, lead_id: int) -> Optional[Lead]:
    """Get a lead by ID"""
    return db.query(Lead).filter(Lead.id == lead_id).first()


def get_lead_by_url(db: Session, url: str, organization_id: int) -> Optional[Lead]:
    """Get a lead by website URL and organization (for deduplication)"""
    return db.query(Lead).filter(
        Lead.website == url,
        Lead.organization_id == organization_id
    ).first()


def get_leads_by_organization(
    db: Session,
    organization_id: int,
    skip: int = 0,
    limit: int = 100,
    qualified: Optional[bool] = None,
    qualification_threshold: Optional[float] = None,
) -> List[Lead]:
    """Get leads for an organization with pagination.

    P1.2: `qualified` is an optional, organization-authoritative filter --
    when set, `qualification_threshold` (the caller's resolved
    OrganizationQualificationSettings.qualification_threshold; see
    get_or_create_qualification_settings) must also be given. The
    predicate is applied in this SQL query, before `.offset()/.limit()`,
    so pagination is always computed over the already-filtered set rather
    than filtering a page in Python after the fact.

    A NULL `Lead.score` (not expected in practice -- every creation/update
    path writes a float -- but the column has no NOT NULL constraint)
    matches neither `qualified=True` nor `qualified=False`: SQL's
    `NULL >= x` and `NULL < x` are both NULL/false, so an unscored lead is
    correctly treated as "not yet known to be qualified either way" rather
    than being guessed into either bucket.
    """
    query = db.query(Lead).filter(Lead.organization_id == organization_id)

    if qualified is not None:
        if qualification_threshold is None:
            raise ValueError(
                "qualification_threshold is required when 'qualified' filter is set"
            )
        if qualified:
            query = query.filter(Lead.score >= qualification_threshold)
        else:
            query = query.filter(Lead.score < qualification_threshold)

    return query.offset(skip).limit(limit).all()


def get_leads_by_owner(
    db: Session, owner_id: int, skip: int = 0, limit: int = 100
) -> List[Lead]:
    """Get leads for a specific user with pagination"""
    return (
        db.query(Lead).filter(Lead.owner_id == owner_id).offset(skip).limit(limit).all()
    )


def update_lead(db: Session, lead_id: int, lead_update: LeadUpdate) -> Optional[Lead]:
    """Update a lead"""
    db_lead = get_lead(db, lead_id)
    if db_lead:
        for field, value in lead_update.dict(exclude_unset=True).items():
            setattr(db_lead, field, value)
        db.commit()
        db.refresh(db_lead)
    return db_lead


def delete_lead(db: Session, lead_id: int) -> bool:
    """Delete a lead (soft delete)"""
    db_lead = get_lead(db, lead_id)
    if db_lead:
        db_lead.is_active = False
        db.commit()
        return True
    return False


# API Key CRUD operations
def create_api_key(db: Session, api_key: APIKeyCreate) -> APIKey:
    """Create a new API key"""
    db_api_key = APIKey(
        name=api_key.name,
        organization_id=api_key.organization_id,
        user_id=api_key.user_id,
        rate_limit=api_key.rate_limit,
    )
    # Generate the actual key
    key = db_api_key.generate_key()
    # Store the hash (in a real app, you'd hash it properly)
    db_api_key.key_hash = get_password_hash(
        key
    )  # Using password hash function for simplicity
    db.add(db_api_key)
    db.commit()
    db.refresh(db_api_key)
    return db_api_key, key  # Return both the model and the actual key


def get_api_key_by_prefix(db: Session, prefix: str) -> Optional[APIKey]:
    """Get an API key by its prefix"""
    return db.query(APIKey).filter(APIKey.key_prefix == prefix).first()


def get_api_keys_by_organization(db: Session, organization_id: int) -> List[APIKey]:
    """Get all API keys for an organization"""
    return db.query(APIKey).filter(APIKey.organization_id == organization_id).all()


# Subscription CRUD operations
def create_subscription(
    db: Session, organization_id: int, stripe_subscription_id: str, plan_name: str
) -> Subscription:
    """Create a new subscription"""
    db_subscription = Subscription(
        organization_id=organization_id,
        stripe_subscription_id=stripe_subscription_id,
        plan_name=plan_name,
    )
    db.add(db_subscription)
    db.commit()
    db.refresh(db_subscription)
    return db_subscription


def get_subscription_by_organization(
    db: Session, organization_id: int
) -> Optional[Subscription]:
    """Get subscription by organization ID"""
    return (
        db.query(Subscription)
        .filter(Subscription.organization_id == organization_id)
        .first()
    )


def get_subscription_by_stripe_id(
    db: Session, stripe_subscription_id: str
) -> Optional[Subscription]:
    """Get subscription by Stripe subscription ID"""
    return (
        db.query(Subscription)
        .filter(Subscription.stripe_subscription_id == stripe_subscription_id)
        .first()
    )


# Usage record CRUD operations
def create_usage_record(
    db: Session, organization_id: int, action: str, quantity: int = 1
) -> UsageRecord:
    """Create a new usage record"""
    db_usage_record = UsageRecord(
        organization_id=organization_id, action=action, quantity=quantity
    )
    db.add(db_usage_record)
    db.commit()
    db.refresh(db_usage_record)

    # Update organization usage count
    org = get_organization(db, organization_id)
    if org:
        org.usage_count += quantity
        db.commit()

    return db_usage_record


def get_usage_records_by_organization(
    db: Session,
    organization_id: int,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[UsageRecord]:
    """Get usage records for an organization, optionally filtered by date range"""
    query = db.query(UsageRecord).filter(UsageRecord.organization_id == organization_id)

    if start_date:
        from datetime import datetime

        start_dt = datetime.fromisoformat(start_date)
        query = query.filter(UsageRecord.timestamp >= start_dt)

    if end_date:
        from datetime import datetime

        end_dt = datetime.fromisoformat(end_date)
        query = query.filter(UsageRecord.timestamp <= end_dt)

    return query.all()


# Lead enrichment log CRUD operations
def create_lead_enrichment_log(
    db: Session,
    lead_id: int,
    enrichment_type: str,
    enrichment_data: str,
    confidence_score: float,
    processing_time_ms: Optional[int] = None,
    pipeline_id: Optional[str] = None,
    organization_id: Optional[int] = None,
    success: bool = True,
) -> LeadEnrichmentLog:
    """Create a new lead enrichment log entry"""
    db_log = LeadEnrichmentLog(
        lead_id=lead_id,
        enrichment_type=enrichment_type,
        enrichment_data=enrichment_data,
        confidence_score=confidence_score,
        processing_time_ms=processing_time_ms,
        pipeline_id=pipeline_id,
        organization_id=organization_id,
        success=success,
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


# AI decision log CRUD operations (used by backend/application for
# explainability, business memory, and evaluation persistence)
def create_ai_decision_log(
    db: Session,
    lead_id: int,
    organization_id: int,
    stage: str,
    agent_name: str,
    output_data: Optional[str] = None,
    reasoning: Optional[str] = None,
    evidence: Optional[str] = None,
    confidence: float = 0.0,
    completeness_score: Optional[float] = None,
    grounding_score: Optional[float] = None,
    consistency_score: Optional[float] = None,
    review_status: Optional[str] = None,
    model_used: Optional[str] = None,
    prompt_name: Optional[str] = None,
    prompt_version: Optional[str] = None,
    processing_time_ms: Optional[int] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    source: Optional[str] = None,
    evaluation_version: Optional[str] = None,
) -> AIDecisionLog:
    """Create a new AI decision log entry (one row per agent stage per lead)."""
    db_log = AIDecisionLog(
        lead_id=lead_id,
        organization_id=organization_id,
        stage=stage,
        agent_name=agent_name,
        output_data=output_data,
        reasoning=reasoning,
        evidence=evidence,
        confidence=confidence,
        completeness_score=completeness_score,
        grounding_score=grounding_score,
        consistency_score=consistency_score,
        review_status=review_status,
        model_used=model_used,
        pipeline_id=pipeline_id,
        source=source,
        evaluation_version=evaluation_version,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        processing_time_ms=processing_time_ms,
        success=success,
        error_message=error_message,
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


def get_ai_decision_logs_by_lead(
    db: Session, lead_id: int, stage: Optional[str] = None, limit: int = 50
) -> List[AIDecisionLog]:
    """Get AI decision logs for a lead, optionally filtered by stage, most recent first."""
    query = db.query(AIDecisionLog).filter(AIDecisionLog.lead_id == lead_id)
    if stage:
        query = query.filter(AIDecisionLog.stage == stage)
    return query.order_by(AIDecisionLog.created_at.desc()).limit(limit).all()


def get_ai_decision_logs_by_organization(
    db: Session, organization_id: int, stage: Optional[str] = None, limit: int = 50
) -> List[AIDecisionLog]:
    """Get recent AI decision logs across an organization, optionally filtered by stage."""
    query = db.query(AIDecisionLog).filter(
        AIDecisionLog.organization_id == organization_id
    )
    if stage:
        query = query.filter(AIDecisionLog.stage == stage)
    return query.order_by(AIDecisionLog.created_at.desc()).limit(limit).all()


# Scraping log CRUD operations
def create_scraping_log(
    db: Session,
    lead_id: int,
    scraping_method: str,
    success: bool,
    confidence_score: float,
    error_message: Optional[str] = None,
    processing_time_ms: Optional[int] = None,
    scraped_data: Optional[str] = None,
    pipeline_id: Optional[str] = None,
    organization_id: Optional[int] = None,
) -> ScrapingLog:
    """Create a new scraping log entry"""
    db_log = ScrapingLog(
        lead_id=lead_id,
        scraping_method=scraping_method,
        success=success,
        error_message=error_message,
        confidence_score=confidence_score,
        processing_time_ms=processing_time_ms,
        scraped_data=scraped_data,
        pipeline_id=pipeline_id,
        organization_id=organization_id,
    )
    db.add(db_log)
    db.commit()
    db.refresh(db_log)
    return db_log


# Organization Qualification Settings CRUD operations (P1.2)
#
# There is deliberately no plain `get_qualification_settings()` that can
# return None: every existing organization (created before this table
# existed) and every organization created after it (create_organization()
# above is intentionally NOT modified to also insert a settings row --
# see the P1.2 migration's docstring for why an eager write doesn't fit
# here) must still resolve to a well-defined threshold. Routing every
# caller through this single get-or-create function is what makes that
# guarantee hold everywhere, instead of relying on every call site to
# remember to handle a missing row the same way.
def get_or_create_qualification_settings(
    db: Session, organization_id: int
) -> OrganizationQualificationSettings:
    """Get an organization's qualification settings, creating a row with
    the backward-compatible default threshold (DEFAULT_QUALIFICATION_THRESHOLD,
    matching the existing "Warm Lead" cutoff) the first time it's read."""
    settings = (
        db.query(OrganizationQualificationSettings)
        .filter(OrganizationQualificationSettings.organization_id == organization_id)
        .first()
    )
    if settings is None:
        settings = OrganizationQualificationSettings(
            organization_id=organization_id,
            qualification_threshold=DEFAULT_QUALIFICATION_THRESHOLD,
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def update_qualification_settings(
    db: Session, organization_id: int, settings_update: QualificationSettingsUpdate
) -> OrganizationQualificationSettings:
    """Update (creating first if necessary) an organization's qualification
    settings. Never touches Lead.score or Lead.qualification_label -- see
    core/domain/models/qualification_settings.py."""
    settings = get_or_create_qualification_settings(db, organization_id)
    for field, value in settings_update.dict(exclude_unset=True).items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    return settings
