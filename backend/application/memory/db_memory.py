"""
Simple, SQL-backed implementation of BusinessMemory.

Reuses the AIDecisionLog table (see core/domain/models/lead.py) as the
single source of truth for both explainability and memory -- there is no
separate memory store to keep in sync. Implementation is intentionally
simple (plain queries, JSON text columns) per the spec's instruction that
memory implementation can remain simple as long as the interface is right.
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from application.memory.interfaces import BusinessMemory
from application.services.infra_adapters import get_recent_ai_decision_logs
from core.infrastructure.database.crud import create_ai_decision_log
from core.infrastructure.logging import get_logger

logger = get_logger("application.memory")


def _row_to_dict(row) -> Dict[str, Any]:
    output_data = None
    if row.output_data:
        try:
            output_data = json.loads(row.output_data)
        except (TypeError, ValueError):
            output_data = row.output_data

    evidence: List[str] = []
    if row.evidence:
        try:
            evidence = json.loads(row.evidence)
        except (TypeError, ValueError):
            evidence = [row.evidence]

    return {
        "id": row.id,
        "stage": row.stage,
        "agent_name": row.agent_name,
        "output": output_data,
        "reasoning": row.reasoning,
        "evidence": evidence,
        "confidence": row.confidence,
        "review_status": row.review_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class SQLBusinessMemory(BusinessMemory):
    def __init__(self, db: Session):
        self.db = db

    def get_previous_company_analysis(self, lead_id: int) -> Optional[Dict[str, Any]]:
        rows = get_recent_ai_decision_logs(
            self.db, lead_id, stage="company_intelligence", limit=1
        )
        return _row_to_dict(rows[0]) if rows else None

    def get_previous_decisions(self, lead_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        rows = get_recent_ai_decision_logs(self.db, lead_id, stage="decision", limit=limit)
        return [_row_to_dict(r) for r in rows]

    def get_previous_outreach(self, lead_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        rows = get_recent_ai_decision_logs(self.db, lead_id, stage="messaging", limit=limit)
        return [_row_to_dict(r) for r in rows]

    def store(
        self,
        lead_id: int,
        organization_id: int,
        stage: str,
        agent_name: str,
        **kwargs: Any,
    ) -> None:
        output_data = kwargs.get("output_data")
        if output_data is not None and not isinstance(output_data, str):
            output_data = json.dumps(output_data, default=str)

        evidence = kwargs.get("evidence")
        if evidence is not None and not isinstance(evidence, str):
            evidence = json.dumps(evidence, default=str)

        try:
            create_ai_decision_log(
                self.db,
                lead_id=lead_id,
                organization_id=organization_id,
                stage=stage,
                agent_name=agent_name,
                output_data=output_data,
                reasoning=kwargs.get("reasoning"),
                evidence=evidence,
                confidence=kwargs.get("confidence", 0.0),
                completeness_score=kwargs.get("completeness_score"),
                grounding_score=kwargs.get("grounding_score"),
                consistency_score=kwargs.get("consistency_score"),
                review_status=kwargs.get("review_status"),
                model_used=kwargs.get("model_used"),
                prompt_name=kwargs.get("prompt_name"),
                prompt_version=kwargs.get("prompt_version"),
                processing_time_ms=kwargs.get("processing_time_ms"),
                success=kwargs.get("success", True),
                error_message=kwargs.get("error_message"),
            )
        except Exception as e:
            # Memory persistence must never break the pipeline.
            logger.error(f"Failed to store AI decision log: {e}")
