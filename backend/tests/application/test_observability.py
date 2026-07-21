"""
Tests for application.observability (models, repository, metrics_service)
and its wiring into the pipeline (pipeline_id propagation, prompt-version
tracking, evaluation persistence).
"""

from datetime import datetime, timedelta, timezone

import pytest

from application.dto.models import PipelineStatus
from application.observability import repository
from application.observability.metrics_service import AnalyticsService


def _record_run(db_session, org_id, lead_id, status: str, duration_ms: int, pipeline_id: str):
    now = datetime.now(timezone.utc)
    return repository.create_pipeline_execution_record(
        db_session,
        pipeline_id=pipeline_id,
        lead_id=lead_id,
        organization_id=org_id,
        started_at=now - timedelta(milliseconds=duration_ms),
        completed_at=now,
        duration_ms=duration_ms,
        final_status=status,
        stage_count=9,
        error_count=0 if status == PipelineStatus.SUCCESS.value else 1,
    )


# -- Repository ---------------------------------------------------------------


def test_create_and_query_pipeline_execution_record(db_session, sample_org, sample_lead):
    _record_run(db_session, sample_org.id, sample_lead.id, PipelineStatus.SUCCESS.value, 500, "pid-1")

    records = repository.get_pipeline_executions(db_session, organization_id=sample_org.id)
    assert len(records) == 1
    assert records[0].pipeline_id == "pid-1"
    assert records[0].final_status == "SUCCESS"


def test_create_evaluation_report_record(db_session, sample_org, sample_lead):
    repository.create_evaluation_report_record(
        db_session,
        pipeline_id="pid-eval-1",
        lead_id=sample_lead.id,
        organization_id=sample_org.id,
        confidence=0.8,
        completeness=0.9,
        grounding=0.7,
        consistency=1.0,
        overall=0.85,
        prompt_version="v1",
    )

    records = repository.get_evaluation_reports(db_session, organization_id=sample_org.id)
    assert len(records) == 1
    assert records[0].overall == pytest.approx(0.85)
    assert records[0].prompt_version == "v1"


def test_create_prompt_execution_record(db_session, sample_org, sample_lead):
    repository.create_prompt_execution_record(
        db_session,
        pipeline_id="pid-prompt-1",
        lead_id=sample_lead.id,
        organization_id=sample_org.id,
        agent_name="decision_agent",
        prompt_name="decision",
        prompt_version="v1",
        retry_count=1,
    )

    records = repository.get_prompt_executions(db_session, organization_id=sample_org.id)
    assert len(records) == 1
    assert records[0].agent_name == "decision_agent"
    assert records[0].retry_count == 1


# -- AnalyticsService: pipeline metrics ---------------------------------------


def test_pipeline_metrics_empty_returns_zeroed_summary(db_session, sample_org):
    service = AnalyticsService(db_session)
    summary = service.get_pipeline_metrics(organization_id=sample_org.id)
    assert summary.total_runs == 0
    assert summary.success_rate_pct == 0.0


def test_pipeline_metrics_success_rate_counts_only_full_success(
    db_session, sample_org, sample_lead
):
    _record_run(db_session, sample_org.id, sample_lead.id, PipelineStatus.SUCCESS.value, 100, "p1")
    _record_run(db_session, sample_org.id, sample_lead.id, PipelineStatus.SUCCESS.value, 200, "p2")
    _record_run(
        db_session, sample_org.id, sample_lead.id, PipelineStatus.PARTIAL_SUCCESS.value, 300, "p3"
    )
    _record_run(db_session, sample_org.id, sample_lead.id, PipelineStatus.FAILED.value, 400, "p4")

    service = AnalyticsService(db_session)
    summary = service.get_pipeline_metrics(organization_id=sample_org.id)

    assert summary.total_runs == 4
    assert summary.success_count == 2
    assert summary.partial_success_count == 1
    assert summary.failed_count == 1
    # 2 full successes out of 4 total runs = 50%
    assert summary.success_rate_pct == 50.0


def test_pipeline_metrics_processing_time_stats(db_session, sample_org, sample_lead):
    for i, duration in enumerate([100, 200, 300, 400, 500]):
        _record_run(
            db_session, sample_org.id, sample_lead.id, PipelineStatus.SUCCESS.value, duration, f"pt-{i}"
        )

    service = AnalyticsService(db_session)
    summary = service.get_pipeline_metrics(organization_id=sample_org.id)

    assert summary.avg_processing_time_ms == 300.0
    assert summary.median_processing_time_ms == 300.0
    assert summary.p95_processing_time_ms >= summary.median_processing_time_ms


def test_pipeline_metrics_scoped_by_organization(db_session, sample_org, sample_lead):
    _record_run(db_session, sample_org.id, sample_lead.id, PipelineStatus.SUCCESS.value, 100, "org-a-1")

    other_org_id = sample_org.id + 999  # a different, non-existent org id
    service = AnalyticsService(db_session)
    summary = service.get_pipeline_metrics(organization_id=other_org_id)
    assert summary.total_runs == 0


def test_pipeline_metrics_since_filters_old_runs(db_session, sample_org, sample_lead):
    old_time = datetime.now(timezone.utc) - timedelta(days=10)
    repository.create_pipeline_execution_record(
        db_session,
        pipeline_id="old-run",
        lead_id=sample_lead.id,
        organization_id=sample_org.id,
        started_at=old_time,
        completed_at=old_time,
        duration_ms=100,
        final_status=PipelineStatus.SUCCESS.value,
    )
    _record_run(db_session, sample_org.id, sample_lead.id, PipelineStatus.SUCCESS.value, 200, "recent-run")

    service = AnalyticsService(db_session)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    summary = service.get_pipeline_metrics(organization_id=sample_org.id, since=since)
    assert summary.total_runs == 1


# -- AnalyticsService: evaluation metrics -------------------------------------


def test_evaluation_metrics_empty_returns_zeroed_summary(db_session, sample_org):
    service = AnalyticsService(db_session)
    summary = service.get_evaluation_metrics(organization_id=sample_org.id)
    assert summary.total_evaluations == 0
    assert summary.average_overall_score == 0.0


def test_evaluation_metrics_averages_across_reports(db_session, sample_org, sample_lead):
    repository.create_evaluation_report_record(
        db_session,
        pipeline_id="ev-1",
        lead_id=sample_lead.id,
        organization_id=sample_org.id,
        confidence=0.8,
        completeness=1.0,
        grounding=0.6,
        consistency=1.0,
        overall=0.8,
    )
    repository.create_evaluation_report_record(
        db_session,
        pipeline_id="ev-2",
        lead_id=sample_lead.id,
        organization_id=sample_org.id,
        confidence=0.4,
        completeness=0.5,
        grounding=0.2,
        consistency=0.5,
        overall=0.4,
    )

    service = AnalyticsService(db_session)
    summary = service.get_evaluation_metrics(organization_id=sample_org.id)
    assert summary.total_evaluations == 2
    assert summary.average_overall_score == 0.6
