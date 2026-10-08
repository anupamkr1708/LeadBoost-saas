"""
Outreach Mailer-state read-through (C9.3).

    "What happened to my outreach, and what is the conversation?"

The Mailer is the source of truth for generation, delivery and conversation;
LeadBoost owns authorization and the customer dashboard. This service lets the
dashboard DISPLAY the Mailer's answer. It is strictly READ-ONLY:

  * it never writes an OutreachAction (or anything else) -- not its state, not
    last_dispatch_error, not mailing_agent_reference, not a "healed" DISPATCH_FAILED.
    If LeadBoost says DISPATCH_FAILED and the Mailer says sent, both are shown,
    side by side; nothing is reconciled here (that is a separate, later concern);
  * it adds no state machine, store, cache, queue or polling.

Flow: organization-scoped lookup of the LeadBoost action (404 if it is missing OR
belongs to another organization -- indistinguishable) -> decide whether the Mailer
can know anything -> one server-side authenticated GET -> map the outcome to a safe
product response. The Mailer is asked only when `dispatch_attempts > 0`: before the
first dispatch attempt there is nothing at the Mailer to ask about.

Outcomes (`availability`) -- nothing is ever fabricated:
  available            the Mailer answered and validated: its state + conversation
  not_dispatched       LeadBoost has never attempted a dispatch; Mailer not called
  not_found_at_mailer  the Mailer has no record of this action for this organization
                       (e.g. the dispatch never reached it)
  mailer_unavailable   timeout / unreachable / 5xx / rejected / not configured /
                       schema drift -> a CLOSED error_code, never raw error text

The idempotency_key sent to the Mailer is the action's own (the same one
dispatch forwarded); the organization -- and with it the Mailer API key -- comes
only from the authenticated caller's organization, never from the request.
"""

from sqlalchemy.orm import Session

from application.services.outreach_service import OutreachError, OutreachErrorCode
from core.domain.schemas.outreach_mailer_state import (
    MailerConversationMessageView,
    MailerStateView,
    OutreachMailerState,
)
from core.infrastructure.database.crud import get_outreach_action
from core.infrastructure.logging import get_logger
from core.infrastructure.mailing_agent.conversation_client import (
    DEFAULT_LIMIT,
    MailerConversation,
    fetch_conversation,
)

logger = get_logger(__name__)

MAX_SUBJECT_CHARS = 998  # RFC 5322 line limit; a longer "subject" is not a subject


def _to_view(conversation: MailerConversation) -> MailerStateView:
    """Mailer wire model -> customer product model. Mailer references, mailbox
    references and message_type are deliberately dropped: they are internal."""
    return MailerStateView(
        delivery_state=conversation.action.state,
        updated_at=conversation.action.updated_at,
        has_more=conversation.has_more,
        messages=[
            MailerConversationMessageView(
                direction=m.direction,
                subject=m.subject[:MAX_SUBJECT_CHARS] if m.subject is not None else None,
                body=m.body,
                body_truncated=m.body_truncated,
                created_at=m.created_at,
                delivery_state=m.delivery_state,
            )
            for m in conversation.messages
        ],
    )


async def get_outreach_mailer_state(
    db: Session, *, organization_id: int, action_id: int, limit: int = DEFAULT_LIMIT
) -> OutreachMailerState:
    action = get_outreach_action(db, organization_id, action_id)
    if action is None:
        raise OutreachError(OutreachErrorCode.ACTION_NOT_FOUND, "Outreach action not found.")

    # Copy what is needed, then end the read transaction: no pooled DB connection
    # is held across the network call (which may take up to the Mailer timeout).
    idempotency_key = action.idempotency_key
    dispatch_attempts = action.dispatch_attempts or 0
    db.rollback()

    if dispatch_attempts <= 0:
        return OutreachMailerState(availability="not_dispatched")

    result = await fetch_conversation(
        organization_id=organization_id, idempotency_key=idempotency_key, limit=limit
    )

    if result.status == "ok":
        return OutreachMailerState(availability="available", mailer=_to_view(result.conversation))
    if result.status == "not_found":
        return OutreachMailerState(availability="not_found_at_mailer")

    logger.warning(
        "Mailer state unavailable",
        extra={
            "organization_id": organization_id,
            "outreach_action_id": action_id,
            "error_code": result.error_code,
        },
    )
    return OutreachMailerState(availability="mailer_unavailable", error_code=result.error_code)
