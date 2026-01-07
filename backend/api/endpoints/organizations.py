"""
Organization endpoints
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, List

from core.infrastructure.database import get_db
from core.infrastructure.auth.security import get_current_user
from core.domain.models.user import User
from core.domain.models.organization import Organization
from core.domain.schemas.organization import (
    Organization as OrganizationSchema,
    OrganizationCreate,
    OrganizationUpdate,
)
from core.infrastructure.database.crud import (
    create_organization,
    get_organization,
    get_organization_by_name,
    update_organization,
)

router = APIRouter(prefix="/organizations")


@router.post("/", response_model=OrganizationSchema)
async def create_org(
    organization: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Create a new organization"""
    # Check if organization already exists
    db_org = get_organization_by_name(db, name=organization.name)
    if db_org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization with this name already exists",
        )

    # Create organization
    db_org = create_organization(db, organization)

    # Add current user to the organization
    current_user.organization_id = db_org.id
    db.commit()

    return db_org


@router.get("/", response_model=OrganizationSchema)
async def read_organization(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Any:
    """Get current user's organization"""
    if not current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    organization = get_organization(db, current_user.organization_id)
    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return organization


@router.get("/{org_id}", response_model=OrganizationSchema)
async def read_organization_by_id(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Get organization by ID (only if user belongs to it)"""
    if current_user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this organization",
        )

    organization = get_organization(db, org_id)
    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return organization


@router.put("/{org_id}", response_model=OrganizationSchema)
async def update_org(
    org_id: int,
    organization_update: OrganizationUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Update organization (only if user belongs to it)"""
    if current_user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this organization",
        )

    organization = update_organization(db, org_id, organization_update)
    if not organization:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )

    return organization
