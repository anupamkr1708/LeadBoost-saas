"""
Analytics endpoints.

Read-only endpoints exposing the metrics computed by
application.observability.metrics_service.AnalyticsService. Follows the
exact same router/auth/db-session conventions as the existing endpoint
modules (organizations.py, billing.py) -- no new auth mechanism, no new
router pattern.

All endpoints are scoped to the current user's organization, matching how
every other endpoint in this API already scopes data. discovery-metrics
extends this same router/module (per the Discovery Layer spec: "integrate
with the existing analytics endpoints, do not create another analytics
framework") rather than living in a separate file.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from application.dto.models import (
    DiscoveryMetricsSummary,
    EvaluationMetricsSummary,
    PipelineMetricsSummary,
)
from application.observability.metrics_service import AnalyticsService
from core.domain.models.user import User
from core.infrastructure.auth.security import get_current_user
from core.infrastructure.database import get_db

router = APIRouter(prefix="/analytics")

# Largest accepted `hours` window: one year. The frontend's widest explicit window is 30 days
# (720h; "All time" is expressed by omitting the parameter), so this leaves ample headroom
# while keeping `timedelta(hours=...)` far from OverflowError. Documented in
# docs/OBSERVABILITY_AND_METRICS.md.
MAX_ANALYTICS_HOURS = 24 * 365


def _hours_query(description: str) -> Optional[int]:
    """Optional positive integer window, 1..MAX_ANALYTICS_HOURS; omitted == all-time.
    Out-of-range or non-integer values are ordinary 422 validation errors."""
    return Query(
        default=None,
        ge=1,
        le=MAX_ANALYTICS_HOURS,
        description=f"{description} Integer 1-{MAX_ANALYTICS_HOURS}. Omit for all-time.",
    )


def require_organization_id(current_user: User = Depends(get_current_user)) -> int:
    """The authenticated user's organization id; 403 if the user has none.

    `AnalyticsService` treats `organization_id=None` as "no filter" (global aggregates --
    that is what the operator-only /metrics route relies on). A customer request must
    never reach that path, so a legacy or detached user row with a NULL organization is
    refused instead of being served global data.
    """
    if current_user.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization context required",
        )
    return current_user.organization_id


def _since_from_hours(hours: Optional[int]) -> Optional[datetime]:
    if hours is None:
        return None
    return datetime.now(timezone.utc) - timedelta(hours=hours)


@router.get("/pipeline-metrics", response_model=PipelineMetricsSummary)
async def get_pipeline_metrics(
    hours: Optional[int] = _hours_query("Only include pipeline runs from the last N hours."),
    organization_id: int = Depends(require_organization_id),
    db: Session = Depends(get_db),
) -> Any:
    """
    Pipeline Success Rate, Average/Median/P95 Processing Time, Total Runs,
    and Success/Partial/Failed counts for the current user's organization.
    """
    service = AnalyticsService(db)
    return service.get_pipeline_metrics(
        organization_id=organization_id,
        since=_since_from_hours(hours),
    )


@router.get("/evaluation-metrics", response_model=EvaluationMetricsSummary)
async def get_evaluation_metrics(
    hours: Optional[int] = _hours_query("Only include evaluations from the last N hours."),
    organization_id: int = Depends(require_organization_id),
    db: Session = Depends(get_db),
) -> Any:
    """
    Average Evaluation Score (and its confidence/completeness/grounding/
    consistency components) for the current user's organization.
    """
    service = AnalyticsService(db)
    return service.get_evaluation_metrics(
        organization_id=organization_id,
        since=_since_from_hours(hours),
    )


@router.get("/discovery-metrics", response_model=DiscoveryMetricsSummary)
async def get_discovery_metrics(
    hours: Optional[int] = _hours_query("Only include discovery runs from the last N hours."),
    organization_id: int = Depends(require_organization_id),
    db: Session = Depends(get_db),
) -> Any:
    """
    Discovery Success Rate, Website Resolution Rate, Duplicate Removal
    Rate, and Average Discovery Time for the current user's organization.
    See application.discovery and application.observability.metrics_service
    for exactly how each is calculated.
    """
    service = AnalyticsService(db)
    return service.get_discovery_metrics(
        organization_id=organization_id,
        since=_since_from_hours(hours),
    )
