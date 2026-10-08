"""
Customer-facing shape of GET /api/v2/outreach-actions/{action_id}/mailer-state (C9.3).

This is a PRODUCT response, deliberately not a pass-through of the Mailer's wire
format (core/infrastructure/mailing_agent/conversation_client.py): the browser
never sees a Mailer reference, mailbox reference, URL, key, path or schema. Every
model forbids undeclared fields, so adding anything here is a conscious change.

`delivery_state` is the Mailer's authoritative delivery state; it sits NEXT TO,
and never replaces or rewrites, the OutreachAction's own `state` (LeadBoost's
authorization/handoff state). `unknown` means "delivery unconfirmed" -- the
consumer must not read it as failure or invite a resend.

Message `body`/`subject` are third-party-controlled text (inbound mail is
attacker-controlled). They are data: the consumer renders them as plain text.
"""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


MailerAvailability = Literal["available", "not_dispatched", "not_found_at_mailer", "mailer_unavailable"]

# Closed. Mirrors conversation_client.MAILER_STATE_ERROR_CODES (a test enforces parity).
MailerStateErrorCodeLiteral = Literal[
    "mailing_agent_not_configured",
    "mailing_agent_insecure_transport",
    "mailing_agent_auth_not_configured",
    "mailing_agent_missing_idempotency_key",
    "mailing_agent_unreachable",
    "mailing_agent_timeout",
    "mailing_agent_rejected",
    "mailing_agent_invalid_response",
    "mailing_agent_server_error",
]


class MailerConversationMessageView(_Strict):
    direction: Literal["outbound", "inbound"]
    subject: Optional[str] = None
    body: str
    body_truncated: bool
    created_at: Optional[datetime] = None
    # Outbound only: that message's own dispatch's delivery state. None for inbound.
    delivery_state: Optional[Literal["queued", "sending", "sent", "failed", "unknown"]] = None


class MailerStateView(_Strict):
    # Delivery state of the action this read was made for (Mailer's ExternalDispatch.state).
    delivery_state: Literal["queued", "sending", "sent", "failed", "unknown"]
    updated_at: Optional[datetime] = None
    # The recipient's conversation (one conversation per recipient, shared by every
    # action to that address): the most recent messages, oldest first.
    messages: List[MailerConversationMessageView]
    # True when older messages exist beyond this window.
    has_more: bool


class OutreachMailerState(_Strict):
    availability: MailerAvailability
    error_code: Optional[MailerStateErrorCodeLiteral] = None
    mailer: Optional[MailerStateView] = None
