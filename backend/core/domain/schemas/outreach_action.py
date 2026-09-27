"""
Pydantic schemas for OutreachAction (P1.4).

Response shapes never include anything sender-credential-shaped -- same
"there is no field to forget to redact" approach as
core/domain/schemas/email_account.py. `OutreachAction` only ever exposes
EmailAccount's *id*, never the account object itself, so a credential leak
here is structurally impossible, not just conventionally avoided.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from core.domain.models.outreach_action import OutreachMode


class OutreachModeEnum(str, Enum):
    manual = OutreachMode.MANUAL
    automatic = OutreachMode.AUTOMATIC


class OutreachActionCreate(BaseModel):
    lead_id: int
    email_account_id: int
    mode: OutreachModeEnum = OutreachModeEnum.manual
    # Optional client-supplied idempotency key (e.g. a request id the
    # caller already generates for retry-safety). If omitted, the service
    # layer derives one from (lead_id, email_account_id, message
    # content) -- see application/services/outreach_service.py.
    idempotency_key: Optional[str] = Field(default=None, min_length=1, max_length=200)


class OutreachAction(BaseModel):
    """Response shape. `state` includes the transient 'dispatching'
    value (see core/domain/models/outreach_action.py::OutreachState) --
    a client will only ever observe it via a concurrent GET while
    another request's dispatch is in flight, never as the result of its
    own POST .../dispatch call, which always resolves to a terminal-ish
    outcome (submitted or dispatch_failed) before returning."""

    id: int
    organization_id: int
    lead_id: int
    email_account_id: int
    mode: str
    state: str
    recipient_email: str
    recipient_name: Optional[str] = None
    subject: Optional[str] = None
    body: str
    correlation_id: Optional[str] = None
    idempotency_key: str
    reason: Optional[str] = None
    approved_by_user_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    mailing_agent_reference: Optional[str] = None
    dispatch_attempts: int
    last_dispatch_error: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class OutreachActionList(BaseModel):
    items: list[OutreachAction]
    total: int
