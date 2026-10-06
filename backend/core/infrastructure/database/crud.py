"""
CRUD operations for database models
"""

from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from core.domain.models.user import User
from core.domain.models.organization import Organization
from core.domain.models.lead import Lead, LeadEnrichmentLog, ScrapingLog, AIDecisionLog
from core.domain.models.api_key import APIKey
from core.domain.models.billing import Subscription, UsageRecord, Invoice
from core.domain.models.qualification_settings import (
    OrganizationQualificationSettings,
    DEFAULT_QUALIFICATION_THRESHOLD,
)
from core.domain.models.email_account import EmailAccount, MailerSyncState, VerificationStatus
from core.domain.models.outreach_action import OutreachAction, OutreachState
from core.domain.models.outreach_policy import OrganizationOutreachPolicy
from core.domain.schemas.user import UserCreate, UserUpdate, UserInDB
from core.domain.schemas.organization import OrganizationCreate, OrganizationUpdate
from core.domain.schemas.qualification_settings import QualificationSettingsUpdate
from core.domain.schemas.lead import LeadCreate, LeadUpdate, LeadInDB
from core.domain.schemas.api_key import APIKeyCreate, APIKeyInDB
from core.domain.schemas.email_account import EmailAccountCreate, EmailAccountUpdate
from core.domain.schemas.outreach_policy import OutreachPolicyUpdate
from core.infrastructure.security.credential_crypto import encrypt_credential
from passlib.context import CryptContext
import uuid
from datetime import datetime, timezone

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


# Email Account CRUD operations (P1.3)
#
# SECURITY: these are the ONLY functions in the codebase that construct or
# mutate EmailAccount.encrypted_credential. Every function below takes an
# already-fetched, already organization-scope-checked `EmailAccount` ORM
# instance where the caller (api/endpoints/email_accounts.py) is
# responsible for the ownership check -- exactly like every other
# organization-scoped resource in this file (see e.g. update_organization
# above) -- and every *query* function filters by organization_id in the
# SQL itself (WHERE organization_id = ...), never "fetch globally, check
# in Python", per the P1.3 tenancy requirement.

# Config fields whose change invalidates a previous verification result
# (the connection this account was verified against no longer matches
# what's stored) -- deliberately does NOT include display_name or
# is_active, which are pure metadata/state changes that don't affect
# whether the stored credential still authenticates against this host.
_EMAIL_ACCOUNT_FIELDS_THAT_INVALIDATE_VERIFICATION = frozenset(
    {"smtp_host", "smtp_port", "security_mode", "username", "credential_type"}
)


def _enum_value(value):
    """EmailAccountCreate/Update fields like `security_mode` are Pydantic
    enums (see core/domain/schemas/email_account.py); the ORM column is a
    plain String. Unwraps `.value` when present, passes plain values
    (str, bool, int, None) through unchanged."""
    return value.value if hasattr(value, "value") else value


def create_email_account(
    db: Session, organization_id: int, payload: EmailAccountCreate
) -> EmailAccount:
    """Creates an EmailAccount for `organization_id`. If `payload.credential`
    is supplied, it is encrypted here -- and only here -- before the ORM
    object is ever constructed; the plaintext local variable
    (`payload.credential`) goes out of scope when this function returns."""
    encrypted_credential = None
    if payload.credential:
        encrypted_credential = encrypt_credential(payload.credential)

    account = EmailAccount(
        organization_id=organization_id,
        provider=payload.provider,
        email_address=payload.email_address,
        display_name=payload.display_name,
        smtp_host=payload.smtp_host,
        smtp_port=payload.smtp_port,
        security_mode=_enum_value(payload.security_mode),
        # Defaults to the mailbox's own address -- see
        # EmailAccountBase.username's docstring in the schema module.
        username=payload.username or payload.email_address,
        credential_type=_enum_value(payload.credential_type),
        encrypted_credential=encrypted_credential,
        verification_status=VerificationStatus.UNVERIFIED,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def get_email_account(db: Session, organization_id: int, account_id: int) -> Optional[EmailAccount]:
    """Organization-scoped lookup -- the WHERE clause itself enforces
    tenancy (see this section's module-level note); a row belonging to a
    different organization simply doesn't match and this returns None,
    which api/endpoints/email_accounts.py maps to 404 -- indistinguishable
    from "doesn't exist", which is the correct behavior for a
    cross-organization access attempt (never reveal that the id exists at
    all)."""
    return (
        db.query(EmailAccount)
        .filter(EmailAccount.id == account_id, EmailAccount.organization_id == organization_id)
        .first()
    )


def get_email_accounts_by_organization(db: Session, organization_id: int) -> List[EmailAccount]:
    return (
        db.query(EmailAccount)
        .filter(EmailAccount.organization_id == organization_id)
        .order_by(EmailAccount.created_at.desc())
        .all()
    )


def update_email_account(db: Session, account: EmailAccount, payload: EmailAccountUpdate) -> EmailAccount:
    """`account` must already be the organization-scope-checked instance
    from get_email_account -- this function does not re-check tenancy.

    Only fields the client actually set are touched (exclude_unset), so a
    request that only changes `display_name` never has to include, and
    never overwrites, smtp_host/port/credential/etc. -- see the P1.3
    brief's "do not require resubmitting credentials for unrelated
    metadata changes"."""
    data = payload.dict(exclude_unset=True)
    new_credential = data.pop("credential", None)

    invalidate_verification = False
    mailer_sync_relevant = False
    for field, raw_value in data.items():
        value = _enum_value(raw_value)
        if field in _EMAIL_ACCOUNT_FIELDS_THAT_INVALIDATE_VERIFICATION and getattr(account, field) != value:
            invalidate_verification = True
        if field == "is_active" and getattr(account, field) != value:
            mailer_sync_relevant = True
        setattr(account, field, value)

    if new_credential:
        account.encrypted_credential = encrypt_credential(new_credential)
        invalidate_verification = True

    if invalidate_verification:
        account.verification_status = VerificationStatus.UNVERIFIED
        account.verified_at = None
        account.verification_error_code = None
        mailer_sync_relevant = True

    # L1: the Mailer-owned mailbox must be reconciled with this change. Marked in
    # the SAME commit as the change itself, so a crash before the (separate,
    # non-transactional) Mailer call can never leave a stale 'synced' marker.
    if mailer_sync_relevant:
        account.mailer_sync_state = MailerSyncState.PENDING

    db.commit()
    db.refresh(account)
    return account


def disable_email_account(db: Session, account: EmailAccount) -> EmailAccount:
    """Soft delete -- same pattern as crud.delete_lead's `is_active = False`.
    A hard DELETE is deliberately not offered: P1.4 outreach records will
    need to keep referencing which mailbox a historical message was sent
    from, and a hard delete would either orphan that foreign key or force
    cascading deletes into send history, neither of which this phase
    should decide on P1.4's behalf. Disabling also excludes the account
    from P1.4's future sender-selection query without losing the row."""
    account.is_active = False
    account.mailer_sync_state = MailerSyncState.PENDING  # L1: Mailer mailbox must be disabled too
    db.commit()
    db.refresh(account)
    return account


def record_email_account_verification(
    db: Session, account: EmailAccount, status: str, error_code: Optional[str]
) -> EmailAccount:
    """Persists the outcome of core.infrastructure.email.smtp_verifier's
    VerificationResult. Never touches encrypted_credential -- verification
    reads the credential, it doesn't change it."""
    account.verification_status = status
    account.verification_error_code = error_code
    account.verified_at = datetime.now(timezone.utc) if status == VerificationStatus.VERIFIED else account.verified_at
    # L1: every verification outcome can change whether the Mailer mailbox may be ACTIVE.
    account.mailer_sync_state = MailerSyncState.PENDING
    db.commit()
    db.refresh(account)
    return account


def count_eligible_email_accounts(db: Session, organization_id: int) -> int:
    """L1: accounts that could send right now (active AND verified). Integrated
    outbound requires exactly one -- Mailer M2 selects its sole ACTIVE mailbox,
    so LeadBoost must never leave that choice ambiguous. Organization-scoped."""
    return (
        db.query(EmailAccount)
        .filter(
            EmailAccount.organization_id == organization_id,
            EmailAccount.is_active.is_(True),
            EmailAccount.verification_status == VerificationStatus.VERIFIED,
        )
        .count()
    )


def lock_email_account(db: Session, organization_id: int, account_id: int) -> Optional[EmailAccount]:
    """L1: organization-scoped fetch that takes a row lock on PostgreSQL
    (SELECT ... FOR UPDATE) so two Mailer reconciles of one account serialize,
    and a concurrent LeadBoost change waits for the in-flight reconcile
    instead of racing it. SQLite (the test DB) has no row locks; SQLAlchemy
    omits FOR UPDATE there. populate_existing refreshes a stale identity-map copy."""
    return (
        db.query(EmailAccount)
        .filter(EmailAccount.id == account_id, EmailAccount.organization_id == organization_id)
        .populate_existing()
        .with_for_update()
        .first()
    )


def record_mailer_sync_result(
    db: Session,
    account: EmailAccount,
    *,
    mailbox_ref: Optional[str],
    state: str,
    error_code: Optional[str],
) -> EmailAccount:
    """Persists the outcome of a Mailer reconcile. The ref is only ever written
    here, from a Mailer response -- no request schema can set it."""
    account.mailer_mailbox_ref = mailbox_ref
    account.mailer_sync_state = state
    account.mailer_sync_error_code = error_code
    db.commit()
    db.refresh(account)
    return account


# Organization Outreach Policy CRUD operations (P1.4)
#
# Same "no plain getter that can return None" shape as
# get_or_create_qualification_settings above, and for the identical
# reason: every organization -- including every one that existed before
# this table did -- must resolve to a well-defined (safest-default)
# policy rather than every call site having to remember to handle a
# missing row.
def get_or_create_outreach_policy(db: Session, organization_id: int) -> OrganizationOutreachPolicy:
    """Get an organization's outreach policy, creating a row with the
    safest defaults (automatic sending disabled, approval required, not
    paused, no limits configured) the first time it's read.

    Safe under concurrent first use: `organization_id` is UNIQUE on this
    table, so two concurrent requests for an organization that has never
    had a policy row before can both reach the `policy is None` branch
    and both attempt to INSERT. Exactly one INSERT succeeds; the other
    raises IntegrityError, which is caught here the same way
    create_outreach_action already handles the identical race on
    (organization_id, idempotency_key) -- roll back the failed insert
    and re-query for the row the other request just committed, rather
    than letting the exception propagate as an unhandled 500."""
    policy = (
        db.query(OrganizationOutreachPolicy)
        .filter(OrganizationOutreachPolicy.organization_id == organization_id)
        .first()
    )
    if policy is None:
        policy = OrganizationOutreachPolicy(organization_id=organization_id)
        db.add(policy)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            policy = (
                db.query(OrganizationOutreachPolicy)
                .filter(OrganizationOutreachPolicy.organization_id == organization_id)
                .first()
            )
            if policy is None:
                # Should be unreachable -- the IntegrityError means some
                # request's row exists -- but never silently return None
                # from a function whose whole contract is "always
                # returns a policy".
                raise
            return policy
        db.refresh(policy)
    return policy


def update_outreach_policy(
    db: Session, organization_id: int, policy_update: OutreachPolicyUpdate
) -> OrganizationOutreachPolicy:
    """Update (creating first if necessary) an organization's outreach
    policy. Field-level validation of the *merged* sending window
    (both-or-neither, start != end) happens in
    application/services/outreach_service.py, which calls this after
    checking the merged result -- this function itself just persists
    whatever it's given, matching update_qualification_settings above."""
    policy = get_or_create_outreach_policy(db, organization_id)
    for field, value in policy_update.dict(exclude_unset=True).items():
        setattr(policy, field, value)
    db.commit()
    db.refresh(policy)
    return policy


# OutreachAction CRUD operations (P1.4)
#
# Every lookup is organization-scoped in the query itself (never
# fetch-then-check only), matching the EmailAccount convention above --
# see get_email_account's docstring for why this matters for tenancy.
def create_outreach_action(db: Session, **fields) -> OutreachAction:
    """Thin insert -- all validation (lead/sender ownership, sender
    verification, message-readiness, idempotency, policy evaluation) has
    already happened in application/services/outreach_service.py before
    this is called. Raises sqlalchemy.exc.IntegrityError if
    (organization_id, idempotency_key) already exists -- the service
    layer catches this as a race with a concurrent identical request and
    treats it exactly like an ordinary pre-existing-action result,
    mirroring how core/domain/models/lead.py's uq_leads_org_website race
    is already handled."""
    action = OutreachAction(**fields)
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def get_outreach_action(db: Session, organization_id: int, action_id: int) -> Optional[OutreachAction]:
    return (
        db.query(OutreachAction)
        .filter(OutreachAction.id == action_id, OutreachAction.organization_id == organization_id)
        .first()
    )


def get_outreach_action_by_idempotency_key(
    db: Session, organization_id: int, idempotency_key: str
) -> Optional[OutreachAction]:
    return (
        db.query(OutreachAction)
        .filter(
            OutreachAction.organization_id == organization_id,
            OutreachAction.idempotency_key == idempotency_key,
        )
        .first()
    )


def list_outreach_actions(
    db: Session,
    organization_id: int,
    *,
    lead_id: Optional[int] = None,
    state: Optional[str] = None,
    limit: int = 100,
) -> List[OutreachAction]:
    query = db.query(OutreachAction).filter(OutreachAction.organization_id == organization_id)
    if lead_id is not None:
        query = query.filter(OutreachAction.lead_id == lead_id)
    if state is not None:
        query = query.filter(OutreachAction.state == state)
    return query.order_by(OutreachAction.created_at.desc()).limit(limit).all()


def count_outreach_actions_since(
    db: Session, organization_id: int, *, states: List[str], since: datetime, mode: Optional[str] = None
) -> int:
    """Counts this organization's own OutreachAction rows in `states`
    created at or after `since` -- the rolling send-limit check
    (application/services/outreach_service.py) queries this directly
    rather than maintaining a separate counters table, matching the
    brief's "a simple database uniqueness/idempotency key is preferable
    to a complex distributed architecture" philosophy applied to rate
    limiting too: at this scale a COUNT(*) is the smallest correct
    mechanism, not a new subsystem.

    `mode`, when given, further restricts the count to that mode --
    daily_send_limit/hourly_send_limit are documented (see
    core/domain/models/outreach_policy.py) as gating AUTOMATIC-mode
    authorization specifically, not an organization-wide cap across both
    modes, so application/services/outreach_service.py's automatic-policy
    evaluation always passes mode=OutreachMode.AUTOMATIC here -- a
    manually-approved action must never consume an organization's
    automatic-sending quota."""
    query = db.query(OutreachAction).filter(
        OutreachAction.organization_id == organization_id,
        OutreachAction.state.in_(states),
        OutreachAction.created_at >= since,
    )
    if mode is not None:
        query = query.filter(OutreachAction.mode == mode)
    return query.count()


def claim_outreach_action_for_dispatch(db: Session, *, organization_id: int, action_id: int) -> int:
    """THE atomic dispatch claim (see
    core/domain/models/outreach_action.py::OutreachState's docstring and
    application/services/outreach_service.py::dispatch_action's
    "DISPATCH CLAIMING" note). A single UPDATE that both filters on and
    changes `state` in one statement -- the database's own row-level
    write serialization is what makes this safe under concurrency, not
    application-level reasoning about who read what first.

    Returns the number of rows updated: 1 means this call won the claim
    and MUST proceed to actually contact the Mailing Agent; 0 means
    either the action doesn't belong to this organization, doesn't
    exist, or (the concurrent case this exists to prevent) another
    request already claimed it first -- the caller must NOT contact the
    Mailing Agent in that case.
    """
    return (
        db.query(OutreachAction)
        .filter(
            OutreachAction.id == action_id,
            OutreachAction.organization_id == organization_id,
            OutreachAction.state.in_(OutreachState.DISPATCHABLE_FROM),
        )
        .update(
            {
                "state": OutreachState.DISPATCHING,
                "dispatch_attempts": OutreachAction.dispatch_attempts + 1,
            },
            synchronize_session=False,
        )
    )


def claim_outreach_action_for_approval(
    db: Session, *, organization_id: int, action_id: int, approved_by_user_id: int, approved_at: datetime
) -> int:
    """Atomic approval claim -- the same "UPDATE that both filters on and
    changes state in one statement" idea as claim_outreach_action_for_dispatch
    above, applied to PENDING_REVIEW -> APPROVED. Fixes a real race: the
    previous implementation read the row, checked its state in Python,
    then wrote it back -- which allowed a concurrent approve and cancel
    (or two concurrent approvals) on the same PENDING_REVIEW row to both
    believe they were operating on a still-pending action and have the
    second write silently overwrite the first (last-write-wins
    corruption). With this UPDATE's WHERE clause re-checking `state`
    itself, only whichever request's statement actually executes first
    can match a still-`pending_review` row; by the time the loser's
    statement runs, the row's state has already changed underneath it,
    so its WHERE clause matches nothing.

    Returns the number of rows updated: 1 means this call won and the
    action is now APPROVED; 0 means it was not (any longer, or ever) in
    PENDING_REVIEW when this ran -- the caller must treat that as
    INVALID_STATE_TRANSITION, not silently succeed.
    """
    return (
        db.query(OutreachAction)
        .filter(
            OutreachAction.id == action_id,
            OutreachAction.organization_id == organization_id,
            OutreachAction.state == OutreachState.PENDING_REVIEW,
        )
        .update(
            {
                "state": OutreachState.APPROVED,
                "approved_by_user_id": approved_by_user_id,
                "approved_at": approved_at,
            },
            synchronize_session=False,
        )
    )


def claim_outreach_action_for_cancellation(
    db: Session, *, organization_id: int, action_id: int, cancelled_at: datetime, reason: Optional[str] = None
) -> int:
    """Atomic cancellation claim -- same idea as
    claim_outreach_action_for_approval above, for
    OutreachState.CANCELLABLE_FROM -> CANCELLED. This is what makes
    "concurrent approve + cancel" and "concurrent dispatch-claim +
    cancel" both resolve to exactly one winner instead of a race:
    DISPATCHING is not in CANCELLABLE_FROM, so once a dispatch attempt
    has claimed the row (see claim_outreach_action_for_dispatch above),
    this UPDATE's WHERE clause simply won't match it any more -- there is
    no window where a cancel request can silently undo an in-flight
    dispatch, because the two claims are racing for the same single
    `state` column via the same atomic-UPDATE mechanism, not via
    separate read-then-write steps that could interleave.

    Returns the number of rows updated: 1 means this call won and the
    action is now CANCELLED; 0 means it was not in a cancellable state
    when this ran.
    """
    values = {"state": OutreachState.CANCELLED, "cancelled_at": cancelled_at}
    if reason:
        values["reason"] = reason
    return (
        db.query(OutreachAction)
        .filter(
            OutreachAction.id == action_id,
            OutreachAction.organization_id == organization_id,
            OutreachAction.state.in_(OutreachState.CANCELLABLE_FROM),
        )
        .update(values, synchronize_session=False)
    )


def lock_outreach_policy(db: Session, organization_id: int) -> OrganizationOutreachPolicy:
    """Acquires a write-lock on this organization's outreach policy row,
    held for the remainder of the caller's current transaction (until
    the next db.commit(), or until db.rollback()/session-close on an
    early-exit error path). Used ONLY by
    application/services/outreach_service.py::create_action's
    AUTOMATIC-mode quota-decision branch, to serialize "count this
    organization's existing actions, then decide, then insert" against a
    concurrent AUTOMATIC-mode create request for the same organization --
    see that function's docstring for the exact race this closes (two
    concurrent requests both observing the same not-yet-incremented
    count and both passing a daily/hourly limit check that should only
    have let one of them through).

    Implemented as a real (if trivial) UPDATE rather than
    `SELECT ... FOR UPDATE`: SQLite -- used throughout this project's
    test suite -- has no row-level FOR UPDATE support at all, so relying
    on it here would mean the serialization this function exists for
    could never actually be exercised by a test, only hoped to work once
    deployed to PostgreSQL. A real UPDATE takes an actual write lock on
    both SQLite (serializing at the whole-database-file level -- a
    stricter guarantee than strictly required, but a correct one for a
    single-row critical section like this) and PostgreSQL (a real
    per-row lock, exactly what production needs) -- the same code path
    is what is tested here and what ships.

    Creates the row first (with the safest defaults) if it doesn't exist
    yet, exactly like get_or_create_outreach_policy -- a lock on a
    nonexistent row is meaningless. Callers on the fast "automatic
    sending isn't even enabled for this organization" rejection path
    should keep using plain get_or_create_outreach_policy instead of
    this function: holding a row lock is not free, and unnecessary once
    no quota decision remains to be raced.
    """
    policy = get_or_create_outreach_policy(db, organization_id)
    db.query(OrganizationOutreachPolicy).filter(OrganizationOutreachPolicy.id == policy.id).update(
        {"updated_at": datetime.now(timezone.utc)}, synchronize_session=False
    )
    db.refresh(policy)
    return policy
