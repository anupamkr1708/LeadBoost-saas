"""
Evaluation utilities.

Deterministic (non-LLM) checks that score an agent's output on four axes:
  - confidence:    the agent's own self-reported confidence
  - completeness:  fraction of expected fields that were actually populated
  - grounding:     fraction of cited evidence strings that actually appear
                    in the source text the agent was given (a simple but
                    effective hallucination check)
  - consistency:   whether the decision aligns with the deterministic score
                    that fed into it

Kept deterministic and cheap on purpose: this is the "Confidence
Evaluation" workflow stage, and per the spec agents should never call the
LLM more than necessary -- evaluation should not cost a second LLM call
just to check the first one.
"""

from typing import Any, Dict, List, Optional

# P0-D (AI provenance hardening): bump this whenever evaluate_completeness/
# evaluate_grounding/evaluate_consistency/build_evaluation_report's
# methodology changes, so a persisted EvaluationReportRecord/AIDecisionLog
# row is always traceable to which evaluation formula produced its score --
# comparing scores across a methodology change without this would silently
# mix incomparable numbers into one historical series.
EVALUATION_VERSION = "eval_v1"

from application.dto.models import EvaluationReport


def evaluate_completeness(fields: Dict[str, Any], expected_fields: List[str]) -> float:
    if not expected_fields:
        return 1.0
    populated = sum(
        1
        for f in expected_fields
        if fields.get(f) not in (None, "", [], {})
    )
    return round(populated / len(expected_fields), 3)


def evaluate_grounding(evidence: List[str], source_text: str) -> float:
    """Fraction of evidence strings that are (approximately) substantiated
    by the source text. A cheap, explainable proxy for hallucination risk
    -- not a semantic similarity model, intentionally, to keep this stage
    fast and dependency-free."""
    if not evidence:
        return 0.5  # neutral: no claims made, so nothing to ground
    if not source_text:
        return 0.0

    source_lower = source_text.lower()
    hits = 0
    for claim in evidence:
        claim_lower = str(claim).lower().strip()
        if not claim_lower:
            continue
        # A claim is "grounded" if a meaningful fragment of it appears in
        # the source text (handles minor LLM paraphrasing of exact quotes).
        words = [w for w in claim_lower.split() if len(w) > 3]
        if not words:
            continue
        fragment_hits = sum(1 for w in words if w in source_lower)
        if fragment_hits / len(words) >= 0.5:
            hits += 1

    return round(hits / len(evidence), 3)


def evaluate_consistency(
    qualification_label: str, decision_qualification: Optional[str]
) -> float:
    """Checks whether the Decision Agent's qualification agrees with the
    deterministic scoring label it was given."""
    if not decision_qualification:
        return 0.5
    return 1.0 if decision_qualification == qualification_label else 0.4


def build_evaluation_report(
    *,
    self_reported_confidence: float,
    fields: Dict[str, Any],
    expected_fields: List[str],
    evidence: List[str],
    source_text: str,
    qualification_label: str,
    decision_qualification: Optional[str],
) -> EvaluationReport:
    completeness = evaluate_completeness(fields, expected_fields)
    grounding = evaluate_grounding(evidence, source_text)
    consistency = evaluate_consistency(qualification_label, decision_qualification)

    overall = round(
        0.4 * self_reported_confidence
        + 0.2 * completeness
        + 0.2 * grounding
        + 0.2 * consistency,
        3,
    )

    notes = []
    if completeness < 0.5:
        notes.append("Low field completeness")
    if grounding < 0.4:
        notes.append("Weak evidence grounding")
    if consistency < 0.5:
        notes.append("Decision disagrees with deterministic score")

    return EvaluationReport(
        confidence=round(self_reported_confidence, 3),
        completeness=completeness,
        grounding=grounding,
        consistency=consistency,
        overall=overall,
        notes=notes,
    )
