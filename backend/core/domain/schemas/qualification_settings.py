"""
Pydantic schemas for OrganizationQualificationSettings.

See core/domain/models/qualification_settings.py for the domain design
rationale (why this is a separate table, why 0-100, why the default is
60.0).
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class QualificationSettingsBase(BaseModel):
    # Same 0-100 scale as Lead.score (core/domain/models/lead.py) -- see
    # that model's docstring for why this is not 0-1.
    qualification_threshold: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "Minimum Lead.score (0-100) this organization considers "
            "qualified. Does not affect Lead.score or Lead.qualification_label."
        ),
    )


class QualificationSettingsUpdate(BaseModel):
    qualification_threshold: Optional[float] = Field(default=None, ge=0.0, le=100.0)


class QualificationSettingsInDBBase(QualificationSettingsBase):
    id: int
    organization_id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class QualificationSettings(QualificationSettingsInDBBase):
    pass
