"""
Tests for the review->routing conditional edge in
application.workflows.lead_pipeline.
"""

from application.workflows.lead_pipeline import _route_after_review


def test_human_review_routes_to_skip_message():
    state = {"review": {"decision": "human_review"}}
    assert _route_after_review(state) == "skip_message"


def test_flagged_routes_to_generate_message():
    state = {"review": {"decision": "flagged"}}
    assert _route_after_review(state) == "generate_message"


def test_auto_approved_routes_to_generate_message():
    state = {"review": {"decision": "auto_approved"}}
    assert _route_after_review(state) == "generate_message"


def test_missing_review_defaults_to_generate_message():
    # Defensive default: if the review stage failed entirely and the state
    # has no "review" key, we should not silently skip messaging forever.
    assert _route_after_review({}) == "generate_message"
