"""
Pydantic schemas for the managed-reply draft (C9.5).

`ReplyDraft` is the browser-facing projection of the Mailer's persisted draft. It carries ONLY product data:
the state, the exact subject and body the Mailer will transmit, the opaque digest that identifies that exact
content, and a version. There is deliberately no field for a Mailer URL, key, database id, RFC header, grounding
context or raw error, so leaking one is structurally impossible rather than conventionally avoided.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

ReplyDraftState = Literal[
    "not_started",        # LeadBoost has not asked the Mailer to generate a draft yet
    "generating",
    "awaiting_approval",  # the draft exists and is held: subject/body/content_sha256 are present
    "queued",             # released to the Mailer's send queue, not yet claimed
    "sending",
    "sent",               # SMTP accepted the message -- NOT proof the recipient received it
    "failed",
    "unknown",            # the send's outcome could not be established; never resent automatically
    "cancelled",
    "legacy",             # a reply created before exact-content review existed: no draft was ever reviewed for it
]


class ReplyDraft(BaseModel):
    state: ReplyDraftState
    subject: Optional[str] = None
    body: Optional[str] = None
    content_sha256: Optional[str] = None
    draft_version: Optional[int] = None
    updated_at: Optional[datetime] = None


class ReplyApproveRequest(BaseModel):
    """The digest of the draft the user reviewed. A claim, not proof: LeadBoost re-fetches the Mailer's current
    draft and approves only if it carries this exact digest."""

    model_config = ConfigDict(extra="forbid")

    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
