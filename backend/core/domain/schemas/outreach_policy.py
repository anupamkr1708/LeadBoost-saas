"""
Pydantic schemas for OrganizationOutreachPolicy (P1.4).

Same partial-update convention as core/domain/schemas/qualification_settings.py
and core/domain/schemas/email_account.py's Update schema -- every field is
optional and only fields actually present in the request are applied
(exclude_unset), so an organization can flip `is_paused` without having to
resend every limit it already configured.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class OutreachPolicyUpdate(BaseModel):
    """All fields optional; only fields present in the request are
    applied (exclude_unset in the CRUD layer). Whether the *resulting*
    window (start+end merged with whatever is already persisted) is
    coherent -- both set or both null, start != end -- is validated in
    application/services/outreach_service.py against the merged row,
    not here, since a schema-only check cannot see the existing value of
    a field the caller didn't include in this particular request."""

    automatic_sending_enabled: Optional[bool] = None
    require_approval_for_automatic: Optional[bool] = None
    daily_send_limit: Optional[int] = Field(default=None, ge=0)
    hourly_send_limit: Optional[int] = Field(default=None, ge=0)
    sending_window_start_hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    sending_window_end_hour_utc: Optional[int] = Field(default=None, ge=0, le=23)
    is_paused: Optional[bool] = None


class OutreachPolicy(BaseModel):
    organization_id: int
    automatic_sending_enabled: bool
    require_approval_for_automatic: bool
    daily_send_limit: Optional[int] = None
    hourly_send_limit: Optional[int] = None
    sending_window_start_hour_utc: Optional[int] = None
    sending_window_end_hour_utc: Optional[int] = None
    is_paused: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
