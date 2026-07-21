"""
Tests for application.evaluation.evaluators.
"""

from application.evaluation.evaluators import (
    build_evaluation_report,
    evaluate_completeness,
    evaluate_consistency,
    evaluate_grounding,
)


def test_evaluate_completeness_full():
    fields = {"a": "x", "b": "y"}
    assert evaluate_completeness(fields, ["a", "b"]) == 1.0


def test_evaluate_completeness_partial():
    fields = {"a": "x", "b": None}
    assert evaluate_completeness(fields, ["a", "b"]) == 0.5


def test_evaluate_completeness_no_expected_fields_is_perfect():
    assert evaluate_completeness({}, []) == 1.0


def test_evaluate_grounding_no_evidence_is_neutral():
    assert evaluate_grounding([], "some source text") == 0.5


def test_evaluate_grounding_no_source_text_is_zero():
    assert evaluate_grounding(["a claim"], "") == 0.0


def test_evaluate_grounding_matching_evidence_scores_high():
    source = "Acme Robotics recently raised a Series B funding round and is hiring engineers."
    evidence = ["Acme Robotics raised a Series B funding round"]
    assert evaluate_grounding(evidence, source) == 1.0


def test_evaluate_grounding_unrelated_evidence_scores_low():
    source = "Acme Robotics builds industrial automation hardware."
    evidence = ["The company recently filed for bankruptcy in another country"]
    assert evaluate_grounding(evidence, source) < 0.5


def test_evaluate_consistency_matching_labels():
    assert evaluate_consistency("Hot Lead", "Hot Lead") == 1.0


def test_evaluate_consistency_mismatched_labels():
    assert evaluate_consistency("Hot Lead", "Cold Lead") == 0.4


def test_evaluate_consistency_no_decision_is_neutral():
    assert evaluate_consistency("Hot Lead", None) == 0.5


def test_build_evaluation_report_combines_all_axes():
    report = build_evaluation_report(
        self_reported_confidence=0.8,
        fields={"qualification": "Hot Lead", "recommended_action": "proceed"},
        expected_fields=["qualification", "recommended_action"],
        evidence=["Hot Lead score was high"],
        source_text="The Hot Lead score was high based on strong signals.",
        qualification_label="Hot Lead",
        decision_qualification="Hot Lead",
    )
    assert 0.0 <= report.overall <= 1.0
    assert report.completeness == 1.0
    assert report.consistency == 1.0
    assert report.notes == []  # nothing to flag in this clean case
