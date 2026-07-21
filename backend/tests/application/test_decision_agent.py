"""
Tests for application.agents.decision_agent.DecisionAgent.

GROQ_API_KEY is unset in the test environment (see conftest.py), so
`run()` always exercises the deterministic rule-based fallback path here.
The LLM-reconciliation logic is tested directly against `_reconcile_action`
without requiring a live LLM.
"""

from application.agents.decision_agent import DecisionAgent
from application.state.lead_state import DecisionContext, LeadContext


def _make_decision_context(score: float, qualification_label: str) -> DecisionContext:
    lead_context = LeadContext(
        lead_id=1,
        organization_id=1,
        website="https://example.com",
        company_name="Example Co",
    )
    return DecisionContext(
        lead_context=lead_context,
        company_intelligence={"icp_alignment_score": 0.7, "pain_points": ["Scaling"]},
        score=score,
        qualification_label=qualification_label,
        scrape_confidence=0.8,
        enrichment_confidence=0.5,
    )


def test_hot_lead_maps_to_proceed():
    agent = DecisionAgent()
    ctx = _make_decision_context(score=90, qualification_label="Hot Lead")
    output = agent.run(ctx)

    assert output.qualification == "Hot Lead"
    assert output.recommended_action == "proceed"
    assert output.source == "rule_based"


def test_warm_lead_maps_to_proceed():
    agent = DecisionAgent()
    ctx = _make_decision_context(score=65, qualification_label="Warm Lead")
    output = agent.run(ctx)
    assert output.recommended_action == "proceed"


def test_cold_lead_maps_to_review():
    agent = DecisionAgent()
    ctx = _make_decision_context(score=45, qualification_label="Cold Lead")
    output = agent.run(ctx)
    assert output.recommended_action == "review"


def test_disqualified_maps_to_reject():
    agent = DecisionAgent()
    ctx = _make_decision_context(score=10, qualification_label="Disqualified")
    output = agent.run(ctx)
    assert output.recommended_action == "reject"


def test_output_always_includes_explanation_with_evidence():
    agent = DecisionAgent()
    ctx = _make_decision_context(score=90, qualification_label="Hot Lead")
    output = agent.run(ctx)

    assert output.explanation.reasoning
    assert len(output.explanation.evidence) > 0
    assert 0.0 <= output.explanation.confidence <= 1.0


def test_allow_llm_false_never_attempts_llm_path(monkeypatch):
    """Even if an LLM were configured, allow_llm=False must force the
    deterministic path (used for free-tier subscription gating)."""
    import application.agents.decision_agent as decision_module

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("LLM path should not have been attempted")

    monkeypatch.setattr(decision_module, "is_llm_available", lambda: True)
    monkeypatch.setattr(
        DecisionAgent, "_decide_with_llm", lambda self, ctx: _fail_if_called()
    )

    agent = DecisionAgent()
    ctx = _make_decision_context(score=90, qualification_label="Hot Lead")
    output = agent.run(ctx, allow_llm=False)

    assert output.source == "rule_based"


def test_reconcile_action_accepts_more_conservative_llm_action():
    # LLM proposes "review" for a Hot Lead (more conservative than the
    # deterministic "proceed") -- should be accepted.
    action = DecisionAgent._reconcile_action("Hot Lead", "review")
    assert action == "review"


def test_reconcile_action_rejects_less_conservative_llm_action():
    # LLM proposes "proceed" for a Disqualified lead (less conservative
    # than the deterministic "reject") -- should be overridden.
    action = DecisionAgent._reconcile_action("Disqualified", "proceed")
    assert action == "reject"


def test_reconcile_action_falls_back_on_invalid_action():
    action = DecisionAgent._reconcile_action("Warm Lead", "not_a_real_action")
    assert action == "proceed"
