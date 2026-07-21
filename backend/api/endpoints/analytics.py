"""
Analytics endpoints.

Read-only endpoints exposing the metrics computed by
application.observability.metrics_service.AnalyticsService. Follows the
exact same router/auth/db-session conventions as the existing endpoint
modules (organizations.py, billing.py) -- no new auth mechanism, no new
router pattern.

Both endpoints are scoped to the current user's organization, matching how
every other endpoint in this API already scopes data.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from application.dto.models import EvaluationMetricsSummary, PipelineMetricsSummary
from application.observability.metrics_service import AnalyticsService
from core.domain.models.user import User
from core.infrastructure.auth.security import get_current_user
from core.infrastructure.database import get_db

router = APIRouter(prefix="/analytics")


def _since_from_hours(hours: Optional[int]) -> Optional[datetime]:
    if hours is None:
        return None
    return datetime.now(timezone.utc) - timedelta(hours=hours)


@router.get("/pipeline-metrics", response_model=PipelineMetricsSummary)
async def get_pipeline_metrics(
    hours: Optional[int] = Query(
        default=None,
        description="Only include pipeline runs from the last N hours. Omit for all-time.",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Pipeline Success Rate, Average/Median/P95 Processing Time, Total Runs,
    and Success/Partial/Failed counts for the current user's organization.
    """
    service = AnalyticsService(db)
    return service.get_pipeline_metrics(
        organization_id=current_user.organization_id,
        since=_since_from_hours(hours),
    )


@router.get("/evaluation-metrics", response_model=EvaluationMetricsSummary)
async def get_evaluation_metrics(
    hours: Optional[int] = Query(
        default=None,
        description="Only include evaluations from the last N hours. Omit for all-time.",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Any:
    """
    Average Evaluation Score (and its confidence/completeness/grounding/
    consistency components) for the current user's organization.
    """
    service = AnalyticsService(db)
    return service.get_evaluation_metrics(
        organization_id=current_user.organization_id,
        since=_since_from_hours(hours),
    )
