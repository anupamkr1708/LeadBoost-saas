"""
Observability repository.

Plain functions over the observability models, mirroring the existing
`core.infrastructure.database.crud` style (flat functions taking a
Session, not a repository class hierarchy) -- consistent with the
"no unnecessary abstractions, no repository pattern" constraint.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from application.observability.models import (
    EvaluationReportRecord,
    PipelineExecutionRecord,
    PromptExecutionRecord,
)


def create_pipeline_execution_record(
    db: Session,
    *,
    pipeline_id: str,
    lead_id: int,
    organization_id: int,
    started_at: datetime,
    completed_at: datetime,
    duration_ms: int,
    final_status: str,
    stage_count: int = 0,
    error_count: int = 0,
) -> PipelineExecutionRecord:
    record = PipelineExecutionRecord(
        pipeline_id=pipeline_id,
        lead_id=lead_id,
        organization_id=organization_id,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=duration_ms,
        final_status=final_status,
        stage_count=stage_count,
        error_count=error_count,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def create_evaluation_report_record(
    db: Session,
    *,
    pipeline_id: str,
    lead_id: int,
    organization_id: int,
    confidence: float,
    completeness: float,
    grounding: float,
    consistency: float,
    overall: float,
    prompt_version: Optional[str] = None,
) -> EvaluationReportRecord:
    record = EvaluationReportRecord(
        pipeline_id=pipeline_id,
        lead_id=lead_id,
        organization_id=organization_id,
        prompt_version=prompt_version,
        confidence=confidence,
        completeness=completeness,
        grounding=grounding,
        consistency=consistency,
        overall=overall,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def create_prompt_execution_record(
    db: Session,
    *,
    pipeline_id: str,
    lead_id: Optional[int],
    organization_id: Optional[int],
    agent_name: str,
    prompt_name: str,
    prompt_version: str,
    retry_count: int = 0,
) -> PromptExecutionRecord:
    record = PromptExecutionRecord(
        pipeline_id=pipeline_id,
        lead_id=lead_id,
        organization_id=organization_id,
        agent_name=agent_name,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        retry_count=retry_count,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_pipeline_executions(
    db: Session,
    *,
    organization_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> List[PipelineExecutionRecord]:
    query = db.query(PipelineExecutionRecord)
    if organization_id is not None:
        query = query.filter(PipelineExecutionRecord.organization_id == organization_id)
    if since is not None:
        query = query.filter(PipelineExecutionRecord.started_at >= since)
    return query.all()


def get_evaluation_reports(
    db: Session,
    *,
    organization_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> List[EvaluationReportRecord]:
    query = db.query(EvaluationReportRecord)
    if organization_id is not None:
        query = query.filter(EvaluationReportRecord.organization_id == organization_id)
    if since is not None:
        query = query.filter(EvaluationReportRecord.evaluated_at >= since)
    return query.all()


def get_prompt_executions(
    db: Session,
    *,
    organization_id: Optional[int] = None,
    prompt_name: Optional[str] = None,
    since: Optional[datetime] = None,
) -> List[PromptExecutionRecord]:
    query = db.query(PromptExecutionRecord)
    if organization_id is not None:
        query = query.filter(PromptExecutionRecord.organization_id == organization_id)
    if prompt_name is not None:
        query = query.filter(PromptExecutionRecord.prompt_name == prompt_name)
    if since is not None:
        query = query.filter(PromptExecutionRecord.executed_at >= since)
    return query.all()
