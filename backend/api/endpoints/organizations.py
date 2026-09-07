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
from core.domain.schemas.qualification_settings import (
    QualificationSettings as QualificationSettingsSchema,
    QualificationSettingsUpdate,
)
from core.infrastructure.database.crud import (
    create_organization,
    get_organization,
    get_organization_by_name,
    update_organization,
    get_or_create_qualification_settings,
    update_qualification_settings,
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

    # NOTE: This endpoint must NOT reassign the calling user's
    # organization_id. The data model is strictly one-user-belongs-to-
    # exactly-one-organization (User.organization_id is a single FK, set
    # once at /register and treated as authoritative for every tenancy
    # check throughout the app -- see api/endpoints/leads.py,
    # organizations.py GET/PUT, discovery, analytics, and usage).
    #
    # This endpoint previously did `current_user.organization_id =
    # db_org.id` here, which silently moved the authenticated user into
    # the brand-new organization on every call. That had two confirmed
    # consequences:
    #   1. The user instantly and silently lost access to their original
    #      organization's leads/data, since every org-scoped query uses
    #      current_user.organization_id. This was the root cause of the
    #      previously-reported "POST /leads/single -> 403" runtime
    #      validation failure: the test script creates a "secondary org"
    #      here, which reassigned its organization_id out from under it,
    #      so the organization_id/owner_id it later submitted to
    #      /leads/single (captured at registration) no longer matched
    #      current_user.organization_id.
    #   2. Because crud.create_organization() does not assign a
    #      subscription/plan (only /register does that), the freshly
    #      created organization has no Subscription row and therefore
    #      defaults to the free plan with zero usage recorded today --
    #      letting a user who has exhausted FREE_MAX_LEADS_PER_DAY bypass
    #      the daily lead quota simply by calling this endpoint again.
    #
    # Creating an organization record is fine (e.g. for future
    # multi-org/invite flows); silently switching the caller's own
    # tenancy is not, so that side effect has been removed.

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


# P1.2: Organization Qualification Settings.
#
# Reuses the exact same "current_user.organization_id != org_id -> 403"
# ownership check as read_organization_by_id/update_org above -- no second
# authorization mechanism. get_or_create_qualification_settings() (see
# core/infrastructure/database/crud.py) guarantees a row always exists by
# the time a response is built, so there is no 404 case here the way there
# is for the organization itself.
@router.get("/{org_id}/qualification-settings", response_model=QualificationSettingsSchema)
async def read_qualification_settings(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Get the current organization's lead-qualification policy (the
    minimum Lead.score this organization considers qualified). Distinct
    from Lead.score/Lead.qualification_label, which are unaffected by
    this setting -- see core/domain/models/qualification_settings.py."""
    if current_user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this organization",
        )

    return get_or_create_qualification_settings(db, org_id)


@router.put("/{org_id}/qualification-settings", response_model=QualificationSettingsSchema)
async def update_qualification_settings_endpoint(
    org_id: int,
    settings_update: QualificationSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """Update the current organization's qualification threshold.

    This only ever changes what counts as "qualified" going forward for
    this organization -- it never rewrites Lead.score, never touches
    Lead.qualification_label, and never triggers AI reprocessing (see
    api/endpoints/leads.py's `is_qualified` derivation, computed at read
    time from this value)."""
    if current_user.organization_id != org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this organization",
        )

    return update_qualification_settings(db, org_id, settings_update)
