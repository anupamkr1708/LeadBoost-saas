"""
FastAPI dependency-injection factories for the Application layer.

Reuses FastAPI's existing `Depends(get_db)` mechanism (see
core/infrastructure/database/get_db) rather than introducing a service
locator or module-level global pipeline instance.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from application.workflows.lead_pipeline import LeadPipeline
from core.infrastructure.database import get_db


def get_lead_pipeline(db: Session = Depends(get_db)) -> LeadPipeline:
    return LeadPipeline(db)
