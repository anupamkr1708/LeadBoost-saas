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
from core.domain.schemas.lead import Lead as LeadSchema, LeadCreate, LeadUpdate
from pydantic import BaseModel
from core.infrastructure.database.crud import (
    create_lead,
    get_lead,
    get_leads_by_organization,
    update_lead,
    delete_lead,
)
from core.infrastructure.scraping.scraper import get_scraper, TieredScraper
from core.infrastructure.logging import get_logger, log_scraping_attempt
from core.infrastructure.workers.orchestrator import process_lead_async
from core.infrastructure.billing.subscription_service import SubscriptionService

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
    """Create leads from URLs and start processing in background"""
    urls = request.urls
    message_style = request.message_style

    if not urls:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No URLs provided"
        )

    # Check subscription limits
    subscription_service = SubscriptionService(db)
    if not subscription_service.can_create_lead(current_user.organization_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily lead limit exceeded for your subscription plan",
        )

    # Check if we can create the requested number of leads
    usage = subscription_service.get_organization_usage(current_user.organization_id)
    if len(urls) > usage.remaining_daily_leads:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Cannot create {len(urls)} leads. Only {usage.remaining_daily_leads} leads remaining for today.",
        )

    created_leads = []
    for url in urls:
        # Create lead record
        lead_create = LeadCreate(
            website=url,
            organization_id=current_user.organization_id,
            owner_id=current_user.id,
        )
        db_lead = create_lead(db, lead_create)
        created_leads.append(db_lead)

        # Process the lead in background
        background_tasks.add_task(process_lead_async, db_lead.id)

    return created_leads


@router.post("/single", response_model=LeadSchema)
async def create_lead_endpoint(
    lead: LeadCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Create a single lead and start processing in background"""
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

    # Check subscription limits
    subscription_service = SubscriptionService(db)
    if not subscription_service.can_create_lead(current_user.organization_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily lead limit exceeded for your subscription plan",
        )

    # Create the lead record
    db_lead = create_lead(db, lead)

    # Process the lead in background
    background_tasks.add_task(process_lead_async, db_lead.id)

    return db_lead


@router.get("/", response_model=List[LeadSchema])
async def read_leads(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Get leads for current user's organization"""
    leads = get_leads_by_organization(
        db, organization_id=current_user.organization_id, skip=skip, limit=limit
    )
    return leads


@router.get("/{lead_id}", response_model=LeadSchema)
async def read_lead(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Get a specific lead by ID"""
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

    return lead


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

    # Process the lead now
    await process_lead_async(lead_id)

    return {"message": "Lead processing started", "lead_id": lead_id}
