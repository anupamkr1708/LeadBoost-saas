"""
Lead management endpoints
"""

import asyncio
from typing import Any, List, Dict
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy.orm import Session

from core.infrastructure.database import get_db
from core.infrastructure.auth.security import get_current_user
from core.domain.models.user import User
from core.domain.models.lead import Lead
from core.domain.schemas.lead import (
    Lead as LeadSchema,
    LeadCreate,
    LeadDetail,
    LeadUpdate,
)
from pydantic import BaseModel
from core.infrastructure.database.crud import (
    create_lead,
    get_lead,
    get_leads_by_organization,
    update_lead,
    delete_lead,
    get_lead_by_url,
)
from core.infrastructure.scraping.scraper import get_scraper, TieredScraper
from core.infrastructure.logging import get_logger, log_scraping_attempt
from application.workflows.lead_pipeline import run_lead_pipeline
from application.services.infra_adapters import get_lead_ai_insights
from core.infrastructure.billing.subscription_service import SubscriptionService
from core.infrastructure.billing.quota_guard import reserve_daily_lead_quota
from sqlalchemy.exc import IntegrityError

logger = get_logger(__name__)

router = APIRouter(prefix="/leads")


class LeadProcessRequest(BaseModel):
    urls: List[str]
    message_style: str = "professional"


@router.post("/", response_model=List[LeadSchema])
async def create_leads_from_urls(
    request: LeadProcessRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Create leads from URLs with deduplication and atomic quota checking
    """
    urls = request.urls
    message_style = request.message_style

    # Validate input
    if not urls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No URLs provided"
        )
    
    if len(urls) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 URLs per request"
        )

    # Deduplicate and normalize URLs
    unique_urls = list(set(url.strip().lower() for url in urls if url.strip()))
    
    if not unique_urls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid URLs provided"
        )

    subscription_service = SubscriptionService(db)
    
    try:
        # Start transaction for atomic quota check + lead creation
        # Filter out already existing leads
        new_urls = []
        existing_leads = []
        
        for url in unique_urls:
            existing = get_lead_by_url(db, url, current_user.organization_id)
            if existing:
                existing_leads.append(existing)
                logger.info(
                    "Lead already exists for URL",
                    extra={
                        "url": url,
                        "lead_id": existing.id,
                        "organization_id": current_user.organization_id,
                    }
                )
            else:
                new_urls.append(url)
        
        # Check if we need to create any new leads
        if not new_urls:
            logger.info(
                "All URLs already exist as leads",
                extra={
                    "organization_id": current_user.organization_id,
                    "user_id": current_user.id,
                    "urls_count": len(urls),
                }
            )
            return existing_leads
        
        # Check quota (fast-path rejection with a friendly, accurate
        # message for the common case -- this alone is not sufficient
        # under concurrency, see the atomic per-lead reservation below).
        usage = subscription_service.get_organization_usage(current_user.organization_id)
        
        if len(new_urls) > usage.remaining_daily_leads:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily lead limit exceeded. Remaining: {usage.remaining_daily_leads}, Requested: {len(new_urls)}"
            )
        
        # Create all new leads.
        #
        # Production-robustness hardening (Phase A5): the check above is
        # a single COUNT(*)-based snapshot and is NOT atomic against a
        # concurrent request for the same organization (see
        # core/infrastructure/billing/quota_guard.py) -- so each lead is
        # additionally gated here by an atomic reservation immediately
        # before it is created. If a concurrent request has consumed the
        # remaining quota since the check above, reservation fails and
        # this stops creating further leads for *this* request rather
        # than exceeding the daily limit; leads already created earlier
        # in this same loop are kept (each `create_lead()` call commits
        # individually, matching this codebase's existing per-row commit
        # convention -- see core/infrastructure/database/crud.py), so the
        # response is a partial-success list rather than an error.
        #
        # Production-robustness hardening (Phase A3, lead-creation race):
        # `create_lead()` can now raise IntegrityError if a concurrent
        # request created a lead for the same URL between the dedup
        # check above and this call (see the UniqueConstraint on
        # core/domain/models/lead.py::Lead) -- treated exactly like the
        # existing "lead already exists" path.
        created_leads = []
        quota_exhausted_mid_batch = False
        for url in new_urls:
            if not reserve_daily_lead_quota(db, current_user.organization_id, usage.max_leads_per_day):
                quota_exhausted_mid_batch = True
                logger.warning(
                    "Daily lead quota exhausted mid-batch by a concurrent request",
                    extra={
                        "organization_id": current_user.organization_id,
                        "user_id": current_user.id,
                        "created_so_far": len(created_leads),
                        "remaining_urls_skipped": len(new_urls) - len(created_leads),
                    }
                )
                break

            lead_create = LeadCreate(
                website=url,
                organization_id=current_user.organization_id,
                owner_id=current_user.id,
            )
            try:
                db_lead = create_lead(db, lead_create)
            except IntegrityError:
                db.rollback()
                existing = get_lead_by_url(db, url, current_user.organization_id)
                if existing:
                    existing_leads.append(existing)
                    continue
                raise
            created_leads.append(db_lead)
        
        # Refresh all leads to get updated data
        for lead in created_leads:
            db.refresh(lead)
        
        # Schedule processing after successful commit
        for lead in created_leads:
            # Runs the LangGraph-based Application-layer pipeline
            # (application/workflows/lead_pipeline.py) as a FastAPI
            # background task, opening its own DB session.
            background_tasks.add_task(run_lead_pipeline, lead.id)
        
        logger.info(
            "Leads created successfully",
            extra={
                "organization_id": current_user.organization_id,
                "user_id": current_user.id,
                "new_leads_count": len(created_leads),
                "existing_leads_count": len(existing_leads),
                "lead_ids": [lead.id for lead in created_leads],
                "quota_exhausted_mid_batch": quota_exhausted_mid_batch,
            }
        )
        
        # Return both new and existing leads
        all_leads = created_leads + existing_leads
        return all_leads

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "Failed to create leads",
            exc_info=True,
            extra={
                "organization_id": current_user.organization_id,
                "user_id": current_user.id,
                "urls_count": len(urls),
                "error_type": type(e).__name__,
            }
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create leads. Please try again."
        )


@router.post("/single", response_model=LeadSchema)
async def create_lead_endpoint(
    lead: LeadCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Create a single lead with quota checking and deduplication"""
    # Verify user has access to the organization
    if current_user.organization_id != lead.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create leads for this organization",
        )

    # Verify user belongs to the specified owner
    if current_user.id != lead.owner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create leads for this owner",
        )

    # Normalize URL
    normalized_url = lead.website.strip().lower()
    
    try:
        # Check for existing lead
        existing = get_lead_by_url(db, normalized_url, current_user.organization_id)
        if existing:
            logger.info(
                "Lead already exists",
                extra={
                    "url": normalized_url,
                    "lead_id": existing.id,
                    "organization_id": current_user.organization_id,
                }
            )
            return existing

        # Check subscription limits (fast-path rejection; see the atomic
        # reservation immediately below for the actual concurrency-safe
        # enforcement -- Phase A5).
        subscription_service = SubscriptionService(db)
        if not subscription_service.can_create_lead(current_user.organization_id):
            usage = subscription_service.get_organization_usage(current_user.organization_id)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Daily lead limit exceeded. Remaining: {usage.remaining_daily_leads}"
            )

        usage = subscription_service.get_organization_usage(current_user.organization_id)
        if not reserve_daily_lead_quota(db, current_user.organization_id, usage.max_leads_per_day):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Daily lead limit exceeded. Remaining: 0",
            )

        # Create the lead record
        #
        # Production-robustness hardening (Phase A3, lead-creation race):
        # a concurrent request may have created a lead for this same URL
        # between the dedup check above and this call (see the
        # UniqueConstraint on core/domain/models/lead.py::Lead) --
        # treated exactly like the existing "lead already exists" path.
        lead.website = normalized_url
        try:
            db_lead = create_lead(db, lead)
        except IntegrityError:
            db.rollback()
            existing = get_lead_by_url(db, normalized_url, current_user.organization_id)
            if existing:
                return existing
            raise
        db.refresh(db_lead)

        # Process the lead in background via the LangGraph LeadPipeline
        background_tasks.add_task(run_lead_pipeline, db_lead.id)

        logger.info(
            "Lead created successfully",
            extra={
                "lead_id": db_lead.id,
                "url": normalized_url,
                "organization_id": current_user.organization_id,
                "user_id": current_user.id,
            }
        )

        return db_lead

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(
            "Failed to create lead",
            exc_info=True,
            extra={
                "url": lead.website,
                "organization_id": current_user.organization_id,
                "error_type": type(e).__name__,
            }
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create lead. Please try again."
        )


@router.get("/", response_model=List[LeadSchema])
async def read_leads(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Get leads for current user's organization with pagination"""
    # Validate pagination params
    if skip < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Skip parameter must be >= 0"
        )
    
    if limit < 1 or limit > 1000:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Limit must be between 1 and 1000"
        )
    
    leads = get_leads_by_organization(
        db, organization_id=current_user.organization_id, skip=skip, limit=limit
    )
    
    logger.info(
        "Leads retrieved",
        extra={
            "organization_id": current_user.organization_id,
            "user_id": current_user.id,
            "count": len(leads),
            "skip": skip,
            "limit": limit,
        }
    )
    
    return leads


@router.get("/{lead_id}", response_model=LeadDetail)
async def read_lead(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Get a specific lead by ID, including its AI insights (Company
    Intelligence, Decision, Evaluation, Review, Messaging), if the
    pipeline has produced them yet."""
    lead = get_lead(db, lead_id)

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Verify user has access to this lead
    if lead.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this lead",
        )

    ai_insights = get_lead_ai_insights(db, lead_id)
    return LeadDetail(**LeadSchema.model_validate(lead).model_dump(), ai_insights=ai_insights)


@router.put("/{lead_id}", response_model=LeadSchema)
async def update_lead_endpoint(
    lead_id: int,
    lead_update: LeadUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Update a lead"""
    lead = get_lead(db, lead_id)

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Verify user has access to this lead
    if lead.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this lead",
        )

    updated_lead = update_lead(db, lead_id, lead_update)
    return updated_lead


@router.delete("/{lead_id}")
async def delete_lead_endpoint(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Delete a lead (soft delete)"""
    lead = get_lead(db, lead_id)

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Verify user has access to this lead
    if lead.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this lead",
        )

    success = delete_lead(db, lead_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete lead",
        )

    return {"message": "Lead deleted successfully"}


@router.post("/{lead_id}/process")
async def process_lead_now(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Manually trigger processing for a lead"""
    lead = get_lead(db, lead_id)

    if not lead:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found"
        )

    # Verify user has access to this lead
    if lead.organization_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this lead",
        )

    # Check if AI features are available for this organization
    subscription_service = SubscriptionService(db)
    if not subscription_service.can_use_ai_features(current_user.organization_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI features are not available on your subscription plan",
        )

    # Process the lead now via the LangGraph LeadPipeline.
    #
    # Bug fix (integration audit #10 / API contract): `run_lead_pipeline`
    # already computes the full PipelineResult -- scraping/enrichment
    # success flags, company_intelligence, decision, evaluation, review,
    # message, timings -- but this endpoint previously discarded that
    # return value entirely and always responded with a static
    # "processing started" placeholder, even though the call above is
    # awaited synchronously and the result was available. `result` here
    # is exactly the dict `PipelineResult.model_dump()` produces (see
    # application/workflows/lead_pipeline.py); the placeholder
    # `message`/`lead_id` fields are kept for any existing caller that
    # only reads those two keys.
    result = await run_lead_pipeline(lead_id)

    return {"message": "Lead processing complete", "lead_id": lead_id, "result": result}
