"""
Discovery endpoint.

A single endpoint: natural-language business search in, validated Leads
(already run through the existing LeadPipeline) out. Follows the exact
same router/auth/db-session conventions as every other endpoint module.
"""

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from application.discovery.discovery_service import DiscoveryService
from application.discovery.dto import DiscoveryResponse
from application.discovery.exceptions import QueryParseError
from core.domain.models.user import User
from core.infrastructure.auth.security import get_current_user
from core.infrastructure.database import get_db
from core.infrastructure.logging import get_logger

logger = get_logger("api.discovery")

router = APIRouter(prefix="/discovery")


class DiscoverySearchRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=200, examples=["Top shoe stores in Mumbai"])
    # Capped lower than query_parser's own internal max: this endpoint runs
    # every validated business through the full, synchronous LeadPipeline
    # before responding, so very large batches would make for an
    # impractically long HTTP request.
    limit: Optional[int] = Field(default=None, ge=1, le=50)


@router.post("/search", response_model=DiscoveryResponse)
async def search_businesses(
    request: DiscoverySearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Turns a natural-language business search into validated Leads.

    Example:
        POST /api/v2/discovery/search
        {"query": "Top shoe stores in Mumbai", "limit": 20}

    Every business that resolves to a verified, reachable website is
    created as a Lead and run through the existing LeadPipeline
    synchronously, so the response reports each business's final
    pipeline_status (SUCCESS/PARTIAL_SUCCESS/FAILED) directly.
    """
    service = DiscoveryService(db)
    try:
        result = await service.discover_and_create_leads(
            query=request.query,
            organization_id=current_user.organization_id,
            owner_id=current_user.id,
            limit=request.limit,
        )
    except QueryParseError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.error(f"Discovery search failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Business discovery failed. Please try again shortly.",
        )

    return result
