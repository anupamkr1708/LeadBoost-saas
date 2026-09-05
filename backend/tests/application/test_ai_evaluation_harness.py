"""
Pytest wrapper around evaluation/run_eval.py -- P0-C's requirement that
"the standard regression suite MUST remain runnable without external API
access" is enforced here by simply never touching GROQ_API_KEY: this
runs in the exact same offline environment as the rest of the suite (see
tests/application/conftest.py), driving each agent's real deterministic
fallback path, zero live LLM calls, zero external API quota spent.

Hard thresholds (C5): schema_validity must be 100% and forbidden_claim_rate
must be 0% for every agent -- anything else is reported but not gated
into a pass/fail, per C5's instruction not to invent thresholds without
justification.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from evaluation.run_eval import check_thresholds, run_all


@pytest.fixture(scope="module")
def eval_summary():
    return run_all()


class TestEvaluationHarnessThresholds:
    def test_no_hard_threshold_violations(self, eval_summary):
        violations = check_thresholds(eval_summary)
        assert violations == [], f"AI evaluation hard thresholds violated: {violations}"

    @pytest.mark.parametrize("agent", ["company_intelligence", "qualification", "decision", "messaging"])
    def test_schema_validity_is_100_percent(self, eval_summary, agent):
        agg = eval_summary["by_agent"][agent]
        assert agg["schema_validity_rate"] == 1.0, f"{agent}: {agg}"

    @pytest.mark.parametrize("agent", ["company_intelligence", "qualification", "decision", "messaging"])
    def test_forbidden_claim_rate_is_zero(self, eval_summary, agent):
        agg = eval_summary["by_agent"][agent]
        assert agg["forbidden_claim_rate"] == 0.0, f"{agent}: {agg}"

    def test_dataset_size_meets_the_spec_minimum(self, eval_summary):
        # C1: ~15-20 cases per agent, 60-80 total.
        assert eval_summary["total_cases"] >= 60
        for agent, agg in eval_summary["by_agent"].items():
            assert agg["n"] >= 15, f"{agent} has only {agg['n']} cases, expected >= 15"


class TestEvaluationHarnessCatchesRealRegressions:
    """Guardrail: prove the harness actually detects a real regression,
    not just that hand-picked golden cases pass -- a corrupted/broken
    agent output must fail the appropriate check."""

    def test_missing_required_field_fails_schema_validity(self):
        from evaluation.deterministic_checks import check_schema_validity

        result = check_schema_validity({"qualification_label": "Hot Lead"}, ["qualification_label", "total_score"])
        assert result.passed is False

    def test_forbidden_claim_present_fails_the_check(self):
        from evaluation.deterministic_checks import check_forbidden_claims

        result = check_forbidden_claims(
            {"explanation": {"evidence": ["This company just secured a Series A funding round"]}},
            ["funding round"],
        )
        assert result.passed is False

    def test_wrong_label_fails_label_match(self):
        from evaluation.deterministic_checks import check_label_match

        result = check_label_match({"qualification_label": "Cold Lead"}, "qualification_label", "Hot Lead")
        assert result.passed is False
