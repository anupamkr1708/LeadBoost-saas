"""
Messaging Agent.

Consumes: Decision Agent output + LeadContext.
Produces: MessagingOutput (email subject/body, LinkedIn opener, follow-up
strategy).

Responsibility boundary: communication only. Never performs qualification
-- the qualification/action it receives from the Decision Agent is treated
as given.

Single LLM call produces every artifact (email + LinkedIn opener + follow
-up strategy) in one structured response, rather than three separate
calls. Falls back to the existing, unmodified, data-locked
core.infrastructure.messaging.messenger.Messenger for the email body when
the LLM is unavailable -- reusing that infrastructure rather than
duplicating its anti-hallucination template logic.
"""

from typing import Optional

from application.agents.base import BaseAgent
from application.dto.models import DecisionOutput, MessagingOutput
from application.explainability.explainer import (
    deterministic_explanation,
    explanation_from_llm_payload,
)
from application.prompts.registry import get_prompt_registry
from application.services import infra_adapters
from application.services.llm_provider import is_llm_available, safe_invoke_json
from application.state.lead_state import LeadContext
from core.domain.models.lead import Lead
from core.infrastructure.logging import get_logger

logger = get_logger("application.agents.messaging")


class MessagingAgent(BaseAgent):
    name = "messaging_agent"

    def run(
        self,
        lead: Lead,
        context: LeadContext,
        decision: DecisionOutput,
        allow_llm: bool = True,
    ) -> MessagingOutput:
        if allow_llm and is_llm_available():
            llm_result = self._generate_with_llm(context, decision)
            if llm_result is not None:
                return llm_result
            logger.info("Messaging Agent: LLM call unavailable/failed, using template fallback")

        return self._generate_from_template(lead)

    # -- LLM path (single call for all artifacts) --------------------------

    def _generate_with_llm(
        self, context: LeadContext, decision: DecisionOutput
    ) -> Optional[MessagingOutput]:
        registry = get_prompt_registry()
        inputs = {
            "company_name": context.company_name or "your company",
            "contact_name": context.contact_name or "the team",
            "industry": context.industry or "your industry",
            "about_text": (context.about_text or "")[:600],
            "sender_org": context.sender_org or "Our Company",
            "qualification": decision.qualification,
            "recommended_action": decision.recommended_action,
        }

        try:
            messages = registry.render("messaging", **inputs)
        except Exception as e:
            logger.warning(f"Failed to render messaging prompt: {e}")
            return None

        payload, retry_count = safe_invoke_json(
            messages, inputs=inputs, temperature=0.3, max_tokens=500
        )
        if payload is None:
            return None

        try:
            explanation = explanation_from_llm_payload(payload, default_confidence=0.6)
            resolved_version = registry.get("messaging").version
            return MessagingOutput(
                email_subject=payload.get("email_subject"),
                email_body=payload.get("email_body"),
                linkedin_opener=payload.get("linkedin_opener"),
                follow_up_strategy=payload.get("follow_up_strategy"),
                channel_notes=None,
                explanation=explanation,
                source="llm",
                prompt_name="messaging",
                prompt_version=resolved_version,
                retry_count=retry_count,
            )
        except Exception as e:
            logger.warning(f"Failed to parse messaging LLM payload: {e}")
            return None

    # -- Deterministic fallback (reuses existing Messenger) -----------------

    def _generate_from_template(self, lead: Lead) -> MessagingOutput:
        email_body = infra_adapters.generate_template_message(lead)

        explanation = deterministic_explanation(
            reasoning=(
                "Generated via the existing data-locked template system "
                "(core.infrastructure.messaging.messenger.Messenger); "
                "LLM path unavailable for this run."
            ),
            evidence=[f"Template selected based on available lead fields for lead {lead.id}"],
        )

        return MessagingOutput(
            email_subject=None,
            email_body=email_body,
            linkedin_opener=None,
            follow_up_strategy=None,
            channel_notes="Generated via template fallback; LinkedIn/follow-up not available without LLM.",
            explanation=explanation,
            source="template",
        )
