"""
Mailer reply-draft client (C9.5): read, release and cancel a managed reply's held draft.

    GET  /integrations/leadboost/reply-drafts/{idempotency_key}
    POST /integrations/leadboost/reply-drafts/{idempotency_key}/release     {"content_sha256": "<64 hex>"}
    POST /integrations/leadboost/reply-drafts/{idempotency_key}/cancel

Generation of the draft is NOT here: it is the existing `submit_generated_outreach` call (with the reply
reference), which Mailer now holds for approval. That call is already idempotent on the action's key, so a
timeout after Mailer accepted it is recovered by simply repeating it -- no second dispatch, no second draft.

Server-side only, over the existing transport (`send_request`): the Mailer base URL, the per-organization API
key, HTTPS enforcement and "no redirects" live there and nowhere else, so nothing here -- and nothing the
browser ever receives -- carries a Mailer URL, key or path.

Strictness, as in conversation_client: the response is parsed with closed models (`extra="forbid"`, Literal
states, strict scalars, bounded sizes). A field the contract does not name, an unknown state, a wrong type, an
oversized body, or content that is present when it should not be (or missing when it must be) is schema drift ->
INVALID_RESPONSE. It is never passed through and never "repaired".

Outcomes are a closed vocabulary. Raw exception text, HTTP bodies and -- above all -- draft content never reach a
log line or an error. The draft body is Mailer-generated text derived from attacker-controlled inbound mail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError, model_validator

from core.infrastructure.logging import get_logger
from core.infrastructure.mailing_agent.client import DispatchErrorCode, send_request

logger = get_logger(__name__)

DRAFT_PATH_TEMPLATE = "/integrations/leadboost/reply-drafts/{key}"
MAX_SUBJECT_CHARS = 998
MAX_BODY_CHARS = 20_000  # Mailer's preview cap: a larger draft is refused there (422), so it is a contract violation here


class ReplyDraftErrorCode:
    """Transport/config codes are the existing DispatchErrorCode values (one vocabulary for 'talking to the Mailer
    failed'); the two additions are specific to drafts."""

    NOT_CONFIGURED = DispatchErrorCode.NOT_CONFIGURED
    INSECURE_TRANSPORT = DispatchErrorCode.INSECURE_TRANSPORT
    AUTH_NOT_CONFIGURED = DispatchErrorCode.AUTH_NOT_CONFIGURED
    MISSING_IDEMPOTENCY_KEY = DispatchErrorCode.MISSING_IDEMPOTENCY_KEY
    UNREACHABLE = DispatchErrorCode.UNREACHABLE
    TIMEOUT = DispatchErrorCode.TIMEOUT
    REJECTED = DispatchErrorCode.REJECTED
    INVALID_RESPONSE = DispatchErrorCode.INVALID_RESPONSE
    SERVER_ERROR = "mailing_agent_server_error"
    # Mailer refuses (422) to show or release a draft it cannot present in full.
    DRAFT_TOO_LARGE = "mailing_agent_draft_too_large"


# The only 409 reasons Mailer emits. Anything else is collapsed to DRAFT_CONFLICT so an unexpected string is
# never propagated into LeadBoost state.
CONFLICT_CODES = (
    "draft_changed",
    "draft_not_ready",
    "draft_generation_failed",
    "draft_cancelled",
    "draft_already_released_with_different_content",
    "draft_already_released",
    "draft_not_releasable",
    # A reply accepted by the pre-approval (C9.4) Mailer: it EXISTS and may be queued, sending, sent or ambiguous.
    # Distinct from "not_found" on purpose: it must never be read as "the Mailer has no such operation".
    "legacy_reply_requires_reconciliation",
)
DRAFT_CONFLICT = "draft_conflict"

ReplyDraftState = Literal[
    "generating", "awaiting_approval", "queued", "sending", "sent", "failed", "unknown", "cancelled"
]
_CONTENT_REQUIRED = ("awaiting_approval", "queued", "sending", "sent", "unknown")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MailerReplyDraft(_Strict):
    state: ReplyDraftState
    subject: Optional[StrictStr] = Field(default=None, max_length=MAX_SUBJECT_CHARS)
    body: Optional[StrictStr] = Field(default=None, max_length=MAX_BODY_CHARS)
    content_sha256: Optional[StrictStr] = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    draft_version: Optional[StrictInt] = Field(default=None, ge=1)
    updated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _content_all_or_none(self):
        present = [self.subject is not None, self.body is not None, self.content_sha256 is not None,
                   self.draft_version is not None]
        if any(present) and not all(present):
            raise ValueError("subject/body/content_sha256/draft_version must be present together")
        if self.state in _CONTENT_REQUIRED and not all(present):
            raise ValueError(f"a '{self.state}' draft must carry its content")
        if self.state == "generating" and any(present):
            raise ValueError("a draft that is still generating must not carry content")
        return self


@dataclass(frozen=True)
class DraftResult:
    """status: "ok" (draft set) | "not_found" | "conflict" (conflict_code set, a CONFLICT_CODES value or
    DRAFT_CONFLICT) | "error" (error_code set; ReplyDraftErrorCode)."""

    status: Literal["ok", "not_found", "conflict", "error"]
    draft: Optional[MailerReplyDraft] = None
    error_code: Optional[str] = None
    conflict_code: Optional[str] = None


def draft_path(idempotency_key: str, suffix: str = "") -> str:
    """The key is caller-suppliable, so it is percent-encoded with safe="" and can never alter the path."""
    return DRAFT_PATH_TEMPLATE.format(key=quote(idempotency_key, safe="")) + suffix


def _detail(data) -> Optional[str]:
    detail = data.get("detail") if isinstance(data, dict) else None
    return detail if isinstance(detail, str) else None


async def _call(
    method: str, path: str, *, organization_id: int, idempotency_key: str, json_body: Optional[dict] = None
) -> DraftResult:
    if not idempotency_key:
        return DraftResult(status="error", error_code=ReplyDraftErrorCode.MISSING_IDEMPOTENCY_KEY)

    resp = await send_request(method, path, organization_id=organization_id, json_body=json_body)
    if resp.error_code:
        return DraftResult(status="error", error_code=resp.error_code)

    status = resp.status_code
    if status == 404:
        return DraftResult(status="not_found")
    if status == 409:
        detail = _detail(resp.data)
        return DraftResult(status="conflict", conflict_code=detail if detail in CONFLICT_CODES else DRAFT_CONFLICT)
    if status == 422 and _detail(resp.data) in ("draft_too_large", "draft_unverifiable"):
        return DraftResult(status="error", error_code=ReplyDraftErrorCode.DRAFT_TOO_LARGE)
    if status is not None and status >= 500:
        return DraftResult(status="error", error_code=ReplyDraftErrorCode.SERVER_ERROR)
    if status is None or not (200 <= status < 300):
        # 3xx (never followed), 401/403 (key rejected), other 422 ...
        return DraftResult(status="error", error_code=ReplyDraftErrorCode.REJECTED)

    try:
        draft = MailerReplyDraft.model_validate(resp.data)
    except ValidationError as exc:
        # Locations and types only -- never `str(exc)` or input values (they contain draft/third-party text).
        logger.warning(
            "Mailer reply-draft response failed schema validation",
            extra={
                "organization_id": organization_id,
                "error_count": exc.error_count(),
                "error_locations": [".".join(str(p) for p in e["loc"]) for e in exc.errors()][:5],
            },
        )
        return DraftResult(status="error", error_code=ReplyDraftErrorCode.INVALID_RESPONSE)
    return DraftResult(status="ok", draft=draft)


async def fetch_reply_draft(*, organization_id: int, idempotency_key: str) -> DraftResult:
    """Read-only. Sends no body."""
    return await _call("GET", draft_path(idempotency_key), organization_id=organization_id,
                       idempotency_key=idempotency_key)


async def release_reply_draft(*, organization_id: int, idempotency_key: str, content_sha256: str) -> DraftResult:
    """Ask Mailer to release the held draft for sending IF its stored content still hashes to `content_sha256`.
    Idempotent on the Mailer side for the same digest, so repeating it after an ambiguous result is safe and is
    how that ambiguity is reconciled. Never regenerates and never creates another message."""
    if not content_sha256:
        return DraftResult(status="error", error_code=ReplyDraftErrorCode.REJECTED)
    return await _call(
        "POST", draft_path(idempotency_key, "/release"), organization_id=organization_id,
        idempotency_key=idempotency_key, json_body={"content_sha256": content_sha256},
    )


async def cancel_reply_draft(*, organization_id: int, idempotency_key: str) -> DraftResult:
    """Revoke an unreleased draft so it can never be sent. Idempotent. A draft already released is a conflict
    (`draft_already_released`), not an error: the caller must not report it as cancelled."""
    return await _call("POST", draft_path(idempotency_key, "/cancel"), organization_id=organization_id,
                       idempotency_key=idempotency_key)
