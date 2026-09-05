"""
Deterministic evaluation checks -- P0-C3 ("prefer deterministic
evaluation whenever possible... do not use an LLM judge where a
deterministic rule is sufficient").

These are new, narrow checks that application/evaluation/evaluators.py
does not already provide (that module's evaluate_grounding/
evaluate_completeness/evaluate_consistency are reused as-is by
run_eval.py, not duplicated here):

  - schema_validity: are the fields expected_properties asks for present
    and of a sane type/shape?
  - forbidden_claims: does the output (as flattened text) contain any of
    the case's must_not_claim phrases?
  - label_match: does an exact field equal an expected value?
  - confidence_in_range: is a confidence-shaped float within [0, 1] and,
    optionally, within a case-specific expected band?
  - contains_all / contains_any: does a list field contain the expected
    item(s) (case-insensitive substring match, since real agent output
    phrasing varies even when the underlying fact is correct)?
"""

from typing import Any, Dict, List

from evaluation.schemas import CheckResult


def _flatten_text(value: Any) -> str:
    """Flattens any JSON-shaped value into one lowercase string for a
    simple, conservative substring-based forbidden-claims check."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, (int, float, bool)):
        return str(value).lower()
    if isinstance(value, dict):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten_text(v) for v in value)
    return str(value).lower()


def check_schema_validity(output: Dict[str, Any], required_fields: List[str]) -> CheckResult:
    missing = [f for f in required_fields if f not in output or output[f] is None]
    if missing:
        return CheckResult("schema_validity", False, f"missing required fields: {missing}")
    return CheckResult("schema_validity", True)


def check_forbidden_claims(output: Dict[str, Any], must_not_claim: List[str]) -> CheckResult:
    if not must_not_claim:
        return CheckResult("forbidden_claims", True, "no forbidden claims declared for this case")
    flattened = _flatten_text(output)
    hits = [phrase for phrase in must_not_claim if phrase.lower() in flattened]
    if hits:
        return CheckResult("forbidden_claims", False, f"output contains forbidden claim(s): {hits}")
    return CheckResult("forbidden_claims", True)


def check_label_match(output: Dict[str, Any], field: str, expected: Any) -> CheckResult:
    actual = output.get(field)
    if actual != expected:
        return CheckResult("label_match", False, f"{field}: expected {expected!r}, got {actual!r}")
    return CheckResult("label_match", True)


def check_confidence_in_range(
    output: Dict[str, Any], field: str, low: float = 0.0, high: float = 1.0
) -> CheckResult:
    value = output.get(field)
    if value is None:
        return CheckResult("confidence_in_range", False, f"{field} is missing")
    if not isinstance(value, (int, float)):
        return CheckResult("confidence_in_range", False, f"{field} is not numeric: {value!r}")
    if not (low <= value <= high):
        return CheckResult(
            "confidence_in_range", False, f"{field}={value} outside expected range [{low}, {high}]"
        )
    return CheckResult("confidence_in_range", True)


def check_contains_all(output: Dict[str, Any], field: str, expected_items: List[str]) -> CheckResult:
    actual_list = output.get(field) or []
    flattened = _flatten_text(actual_list)
    missing = [item for item in expected_items if item.lower() not in flattened]
    if missing:
        return CheckResult(
            "contains_all", False, f"{field} is missing expected item(s): {missing} (actual: {actual_list})"
        )
    return CheckResult("contains_all", True)


def check_contains_any(output: Dict[str, Any], field: str, expected_items: List[str]) -> CheckResult:
    actual_list = output.get(field) or []
    flattened = _flatten_text(actual_list)
    if not expected_items:
        return CheckResult("contains_any", True)
    hit = any(item.lower() in flattened for item in expected_items)
    if not hit:
        return CheckResult(
            "contains_any", False, f"{field} contains none of {expected_items} (actual: {actual_list})"
        )
    return CheckResult("contains_any", True)
