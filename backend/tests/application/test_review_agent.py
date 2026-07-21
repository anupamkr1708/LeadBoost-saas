"""
Tests for application.agents.review_agent.ReviewAgent.
"""

from application.agents.review_agent import ReviewAgent
from application.dto.models import EvaluationReport


def test_high_confidence_auto_approves():
    agent = ReviewAgent()
    evaluation = EvaluationReport(overall=0.9)
    result = agent.run(evaluation)
    assert result.decision == "auto_approved"


def test_low_confidence_routes_to_human_review():
    agent = ReviewAgent()
    evaluation = EvaluationReport(overall=0.2)
    result = agent.run(evaluation)
    assert result.decision == "human_review"


def test_borderline_confidence_is_flagged():
    agent = ReviewAgent()
    evaluation = EvaluationReport(overall=0.6)
    result = agent.run(evaluation)
    assert result.decision == "flagged"


def test_thresholds_are_configurable_via_env(monkeypatch):
    monkeypatch.setenv("REVIEW_AUTO_APPROVE_THRESHOLD", "0.5")
    monkeypatch.setenv("REVIEW_HUMAN_REVIEW_THRESHOLD", "0.3")
    agent = ReviewAgent()
    assert agent.auto_approve_threshold == 0.5
    assert agent.human_review_threshold == 0.3

    result = agent.run(EvaluationReport(overall=0.55))
    assert result.decision == "auto_approved"
