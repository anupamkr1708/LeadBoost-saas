"""
Analytics service.

Computes the metrics required by the production-polish spec directly from
`application.observability` records. Aggregation is done in plain Python
(stdlib `statistics`) over the queried rows rather than database-specific
SQL (e.g. Postgres's `percentile_cont`), so this works identically on
SQLite (dev) and Postgres (prod) -- consistent with "reuse existing
infrastructure" and "do not add caching / unnecessary abstractions".

This is intentionally a simple, synchronous, request-time computation.
Given lead-processing volumes (not high-frequency event-stream volumes),
loading the matching rows into memory per request is appropriate; the
`since` parameter lets callers bound the query window if a deployment's
history grows large enough for that to matter.
"""

import statistics
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from application.dto.models import EvaluationMetricsSummary, PipelineMetricsSummary, PipelineStatus
from application.observability import repository


class AnalyticsService:
    """Read-only aggregation over observability records. Construct with a
    request-scoped db session (see application.dependencies), mirroring
    every other Application-layer service."""

    def __init__(self, db: Session):
        self.db = db

    def get_pipeline_metrics(
        self, organization_id: Optional[int] = None, since: Optional[datetime] = None
    ) -> PipelineMetricsSummary:
        records = repository.get_pipeline_executions(
            self.db, organization_id=organization_id, since=since
        )
        total = len(records)
        if total == 0:
            return PipelineMetricsSummary()

        success_count = sum(1 for r in records if r.final_status == PipelineStatus.SUCCESS)
        partial_count = sum(
            1 for r in records if r.final_status == PipelineStatus.PARTIAL_SUCCESS
        )
        failed_count = sum(1 for r in records if r.final_status == PipelineStatus.FAILED)

        durations = [r.duration_ms for r in records if r.duration_ms is not None]

        return PipelineMetricsSummary(
            total_runs=total,
            success_count=success_count,
            partial_success_count=partial_count,
            failed_count=failed_count,
            # Pipeline Success Rate = fully-successful runs / total runs.
            # PARTIAL_SUCCESS and FAILED are reported as their own counts
            # above rather than folded into "success", so this rate
            # reflects genuinely error-free executions.
            success_rate_pct=round((success_count / total) * 100, 2),
            avg_processing_time_ms=self._avg(durations),
            median_processing_time_ms=self._median(durations),
            p95_processing_time_ms=self._p95(durations),
        )

    def get_evaluation_metrics(
        self, organization_id: Optional[int] = None, since: Optional[datetime] = None
    ) -> EvaluationMetricsSummary:
        records = repository.get_evaluation_reports(
            self.db, organization_id=organization_id, since=since
        )
        total = len(records)
        if total == 0:
            return EvaluationMetricsSummary()

        return EvaluationMetricsSummary(
            total_evaluations=total,
            average_overall_score=self._avg([r.overall for r in records]),
            average_confidence=self._avg([r.confidence for r in records]),
            average_completeness=self._avg([r.completeness for r in records]),
            average_grounding=self._avg([r.grounding for r in records]),
            average_consistency=self._avg([r.consistency for r in records]),
        )

    @staticmethod
    def _avg(values: List[float]) -> float:
        return round(statistics.mean(values), 2) if values else 0.0

    @staticmethod
    def _median(values: List[float]) -> float:
        return round(statistics.median(values), 2) if values else 0.0

    @staticmethod
    def _p95(values: List[float]) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return round(values[0], 2)
        # Nearest-rank P95 via stdlib statistics.quantiles (n=100 buckets;
        # index 94 is the boundary between the 94th and 95th percentile).
        quantiles = statistics.quantiles(values, n=100, method="inclusive")
        return round(quantiles[94], 2)
