"""
Decision Agent.

Consumes: deterministic lead score + qualification label (from the existing
core.domain.services.scoring.LeadScoringService, reused unchanged),
Company Intelligence output, enrichment data, and simple business rules.

Produces: DecisionOutput (qualification, confidence, recommended next
action, reasoning, supporting evidence).

Never calls the LLM more than once. The deterministic score is always
treated as ground truth -- the LLM (when available) is used to add
reasoning/evidence and to sanity-check the recommended action, not to
re-derive the score itself.
"""

from typing import Optional

from application.agents.base import BaseAgent
from application.dto.models import DecisionOutput
from application.explainability.explainer import (
    deterministic_explanation,
    explanation_from_llm_payload,
)
from application.prompts.registry import get_prompt_registry
from application.services.llm_provider import is_llm_available, safe_invoke_json
from application.state.lead_state import DecisionContext
from core.infrastructure.logging import get_logger

logger = get_logger("application.agents.decision")

# Deterministic action mapping, used both as the fallback path and as a
# sanity bound on whatever the LLM proposes (see _reconcile_action).
_ACTION_BY_LABEL = {
    "Hot Lead": "proceed",
    "Warm Lead": "proceed",
    "Cold Lead": "review",
    "Disqualified": "reject",
}


class DecisionAgent(BaseAgent):
    name = "decision_agent"

    def run(self, decision_context: DecisionContext, allow_llm: bool = True) -> DecisionOutput:
        if allow_llm and is_llm_available():
            llm_result = self._decide_with_llm(decision_context)
            if llm_result is not None:
                return llm_result
            logger.info("Decision Agent: LLM call unavailable/failed, using rule-based fallback")

        return self._decide_deterministically(decision_context)

    # -- LLM path (single call) ------------------------------------------

    def _decide_with_llm(self, ctx: DecisionContext) -> Optional[DecisionOutput]:
        ci = ctx.company_intelligence or {}
        registry = get_prompt_registry()

        inputs = {
            "company_name": ctx.lead_context.company_name or "Unknown",
            "website": ctx.lead_context.website,
            "score": str(round(ctx.score, 1)),
            "qualification_label": ctx.qualification_label,
            "industry_analysis": str(ci.get("industry_analysis") or "Unknown"),
            "pain_points": ", ".join(ci.get("pain_points") or []) or "None identified",
            "growth_indicators": ", ".join(ci.get("growth_indicators") or [])
            or "None identified",
            "icp_alignment_score": str(ci.get("icp_alignment_score", 0.0)),
        }

        try:
            messages = registry.render("decision", **inputs)
        except Exception as e:
            logger.warning(f"Failed to render decision prompt: {e}")
            return None

        payload, retry_count = safe_invoke_json(
            messages, inputs=inputs, temperature=0.1, max_tokens=500
        )
        if payload is None:
            return None

        try:
            qualification = payload.get("qualification") or ctx.qualification_label
            action = self._reconcile_action(
                qualification, payload.get("recommended_action")
            )
            explanation = explanation_from_llm_payload(payload)
            resolved_version = registry.get("decision").version
            return DecisionOutput(
                qualification=qualification,
                recommended_action=action,
                explanation=explanation,
                source="llm",
                prompt_name="decision",
                prompt_version=resolved_version,
                retry_count=retry_count,
            )
        except Exception as e:
            logger.warning(f"Failed to parse decision LLM payload: {e}")
            return None

    @staticmethod
    def _reconcile_action(qualification: str, proposed_action: Optional[str]) -> str:
        """The LLM's proposed action is only accepted if it agrees with the
        deterministic mapping for that qualification label, or is more
        conservative (e.g. 'review' instead of 'proceed'). This bounds LLM
        variance without discarding its judgement entirely."""
        expected = _ACTION_BY_LABEL.get(qualification, "review")
        conservative_order = {"proceed": 0, "review": 1, "reject": 2}
        if proposed_action not in conservative_order:
            return expected
        if conservative_order[proposed_action] >= conservative_order.get(expected, 1):
            return proposed_action
        return expected

    # -- Deterministic fallback -------------------------------------------

    def _decide_deterministically(self, ctx: DecisionContext) -> DecisionOutput:
        qualification = ctx.qualification_label
        action = _ACTION_BY_LABEL.get(qualification, "review")

        evidence = [f"Deterministic score: {round(ctx.score, 1)}/100"]
        ci = ctx.company_intelligence or {}
        if ci.get("icp_alignment_score") is not None:
            evidence.append(f"ICP alignment score: {ci.get('icp_alignment_score')}")
        if ctx.scrape_confidence:
            evidence.append(f"Scrape confidence: {round(ctx.scrape_confidence, 2)}")
        if ctx.enrichment_confidence:
            evidence.append(f"Enrichment confidence: {round(ctx.enrichment_confidence, 2)}")

        explanation = deterministic_explanation(
            reasoning=(
                f"Rule-based mapping from deterministic score/label "
                f"('{qualification}') to action '{action}'; LLM reasoning "
                f"unavailable for this run."
            ),
            evidence=evidence,
        )

        return DecisionOutput(
            qualification=qualification,
            recommended_action=action,
            explanation=explanation,
            source="rule_based",
        )
