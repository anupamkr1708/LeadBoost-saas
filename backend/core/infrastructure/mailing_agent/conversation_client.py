"""
Mailer conversation read client (C9.3).

The one LeadBoost-side caller of Mailer's read-only conversation endpoint

    GET /integrations/leadboost/outreach-actions/{idempotency_key}/conversation?limit=N

Server-side only. It reuses the existing transport (`send_request`): the Mailer
base URL, the per-organization API key, HTTPS enforcement and "no redirects" all
live there and nowhere else, so nothing here -- and nothing the browser ever
receives -- carries a Mailer URL, key or path. It sends NO body and NO
credential (a GET), and it never mutates anything: it returns a value.

Strictness: the Mailer's response is parsed with closed models
(`extra="forbid"`, Literal enums, strict scalar types, bounded sizes). A field the
contract does not name, an unknown state, a wrong type or an oversized body is a
schema drift and becomes MailerStateErrorCode.INVALID_RESPONSE -- it is never
passed through and never "repaired". Response content is never logged: inbound
bodies are attacker-controlled third-party text.

Outcomes are a closed vocabulary (ConversationResult.status / error_code); raw
exception, HTTP and body text never escape this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError, model_validator

from core.infrastructure.logging import get_logger
from core.infrastructure.mailing_agent.client import DispatchErrorCode, send_request

logger = get_logger(__name__)

CONVERSATION_PATH_TEMPLATE = "/integrations/leadboost/outreach-actions/{key}/conversation"
DEFAULT_LIMIT = 20
MAX_LIMIT = 50
MAX_BODY_CHARS = 20_000  # Mailer's cap; a larger body is a contract violation, not data


class MailerStateErrorCode:
    """Closed, SAFE vocabulary for 'the Mailer could not tell us'. Transport and
    configuration codes are the existing DispatchErrorCode values (one vocabulary
    for 'talking to the Mailer failed'); SERVER_ERROR is the only addition."""

    NOT_CONFIGURED = DispatchErrorCode.NOT_CONFIGURED
    INSECURE_TRANSPORT = DispatchErrorCode.INSECURE_TRANSPORT
    AUTH_NOT_CONFIGURED = DispatchErrorCode.AUTH_NOT_CONFIGURED
    MISSING_IDEMPOTENCY_KEY = DispatchErrorCode.MISSING_IDEMPOTENCY_KEY
    UNREACHABLE = DispatchErrorCode.UNREACHABLE
    TIMEOUT = DispatchErrorCode.TIMEOUT
    REJECTED = DispatchErrorCode.REJECTED
    INVALID_RESPONSE = DispatchErrorCode.INVALID_RESPONSE
    SERVER_ERROR = "mailing_agent_server_error"


MAILER_STATE_ERROR_CODES = (
    MailerStateErrorCode.NOT_CONFIGURED,
    MailerStateErrorCode.INSECURE_TRANSPORT,
    MailerStateErrorCode.AUTH_NOT_CONFIGURED,
    MailerStateErrorCode.MISSING_IDEMPOTENCY_KEY,
    MailerStateErrorCode.UNREACHABLE,
    MailerStateErrorCode.TIMEOUT,
    MailerStateErrorCode.REJECTED,
    MailerStateErrorCode.INVALID_RESPONSE,
    MailerStateErrorCode.SERVER_ERROR,
)

MailerDeliveryState = Literal["queued", "sending", "sent", "failed", "unknown"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MailerConversationAction(_Strict):
    accepted: Literal[True]
    state: MailerDeliveryState
    mailing_agent_reference: StrictStr
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    mailbox_reference: Optional[StrictStr] = None


class MailerConversationMessage(_Strict):
    direction: Literal["outbound", "inbound"]
    message_type: Optional[Literal["initial_outreach", "follow_up", "reply", "closing"]] = None
    subject: Optional[StrictStr] = None
    body: StrictStr = Field(max_length=MAX_BODY_CHARS)
    body_truncated: StrictBool
    created_at: Optional[datetime] = None
    delivery_state: Optional[MailerDeliveryState] = None
    mailing_agent_reference: Optional[StrictStr] = None
    mailbox_reference: Optional[StrictStr] = None

    @model_validator(mode="after")
    def _delivery_state_only_on_outbound(self):
        # An outbound message always carries its dispatch's state; an inbound one
        # never does. Anything else is a contract violation -- never guessed at.
        if (self.direction == "outbound") != (self.delivery_state is not None):
            raise ValueError("delivery_state must be present exactly for outbound messages")
        return self


class MailerConversation(_Strict):
    action: MailerConversationAction
    messages: list[MailerConversationMessage] = Field(max_length=MAX_LIMIT)
    has_more: StrictBool


@dataclass(frozen=True)
class ConversationResult:
    """status: "ok" (conversation set) | "not_found" (Mailer has no such action
    for this organization) | "error" (error_code set; MAILER_STATE_ERROR_CODES)."""

    status: Literal["ok", "not_found", "error"]
    conversation: Optional[MailerConversation] = None
    error_code: Optional[str] = None


def conversation_path(idempotency_key: str, limit: int) -> str:
    """The key is caller-suppliable (it may contain '/', '?', '#', '%'), so it is
    percent-encoded with safe="" and can never change the path structure."""
    return CONVERSATION_PATH_TEMPLATE.format(key=quote(idempotency_key, safe="")) + f"?limit={limit}"


async def fetch_conversation(
    *, organization_id: int, idempotency_key: str, limit: int = DEFAULT_LIMIT
) -> ConversationResult:
    if not idempotency_key:
        return ConversationResult(status="error", error_code=MailerStateErrorCode.MISSING_IDEMPOTENCY_KEY)
    limit = max(1, min(int(limit), MAX_LIMIT))

    resp = await send_request(
        "GET", conversation_path(idempotency_key, limit), organization_id=organization_id
    )

    if resp.error_code:  # pre-flight / transport failure, already a safe code
        return ConversationResult(status="error", error_code=resp.error_code)

    status = resp.status_code
    if status == 404:
        return ConversationResult(status="not_found")
    if status is not None and status >= 500:
        return ConversationResult(status="error", error_code=MailerStateErrorCode.SERVER_ERROR)
    if status is None or not (200 <= status < 300):
        # 3xx (redirects are never followed), 401/403 (key rejected), 422, ...
        return ConversationResult(status="error", error_code=MailerStateErrorCode.REJECTED)

    try:
        conversation = MailerConversation.model_validate(resp.data)
    except ValidationError as exc:
        # Error locations and types only -- never `str(exc)` / input values (they contain
        # third-party message text).
        logger.warning(
            "Mailer conversation response failed schema validation",
            extra={
                "organization_id": organization_id,
                "error_count": exc.error_count(),
                "error_locations": [".".join(str(p) for p in e["loc"]) for e in exc.errors()][:5],
            },
        )
        return ConversationResult(status="error", error_code=MailerStateErrorCode.INVALID_RESPONSE)
    if len(conversation.messages) > limit:
        # The window is bounded by what we asked for; more than that is a contract violation.
        logger.warning(
            "Mailer conversation response exceeded the requested window",
            extra={
                "organization_id": organization_id,
                "requested": limit,
                "received": len(conversation.messages),
            },
        )
        return ConversationResult(status="error", error_code=MailerStateErrorCode.INVALID_RESPONSE)
    return ConversationResult(status="ok", conversation=conversation)
