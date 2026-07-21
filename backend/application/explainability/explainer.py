"""
Explainability helpers.

Every agent output embeds an `Explanation` (see application.dto.models).
This module standardizes how agents build one, whether the underlying
result came from an LLM call or a deterministic fallback, so "no
black-box outputs" is enforced consistently rather than reimplemented
per agent.
"""

from typing import Any, Dict, List, Optional

from application.dto.models import Explanation


def build_explanation(
    reasoning: str,
    evidence: Optional[List[str]] = None,
    confidence: float = 0.0,
) -> Explanation:
    return Explanation(
        reasoning=reasoning.strip() if reasoning else "",
        evidence=[e for e in (evidence or []) if e],
        confidence=max(0.0, min(1.0, confidence)),
    )


def explanation_from_llm_payload(
    payload: Dict[str, Any], default_confidence: float = 0.5
) -> Explanation:
    """Build an Explanation from a parsed LLM JSON payload that follows the
    `reasoning` / `evidence` / `confidence` convention used by every prompt
    template in application/prompts/templates."""
    return build_explanation(
        reasoning=str(payload.get("reasoning") or ""),
        evidence=payload.get("evidence") or [],
        confidence=float(payload.get("confidence", default_confidence) or default_confidence),
    )


def deterministic_explanation(reasoning: str, evidence: List[str]) -> Explanation:
    """For non-LLM (rule-based) agent paths. Confidence for deterministic
    logic is fixed and high, since there is no reasoning uncertainty --
    only data-availability uncertainty, which the caller reflects in the
    evidence list itself."""
    return build_explanation(reasoning=reasoning, evidence=evidence, confidence=0.85)
