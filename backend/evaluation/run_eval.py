"""
P0-C: AI evaluation / regression harness.

Standalone, offline, side-effect-free (mirrors discovery_eval/'s own
design principle: zero database writes, zero LLM quota spend). Loads
evaluation/datasets/*.jsonl, re-executes the REAL agent/scoring code
(application.agents.company_intelligence_agent.CompanyIntelligenceAgent,
application.agents.decision_agent.DecisionAgent,
application.agents.messaging_agent.MessagingAgent,
core.domain.services.scoring.LeadScoringService) via evaluation/
runners.py -- the exact same functions evaluation/generate_datasets.py
used to capture the golden baseline -- and scores each case with
deterministic checks only (evaluation/deterministic_checks.py plus
reused functions from application/evaluation/evaluators.py). No LLM
judge, no network calls, no external API quota consumed by running this.

Usage:
    python -m evaluation.run_eval                  # full report
    python -m evaluation.run_eval --agent decision  # one agent only
    python -m evaluation.run_eval --slice sparse_evidence

Exit code is nonzero if a hard threshold (schema_validity < 100%,
forbidden_claim_rate > 0%) is violated for any agent -- see
THRESHOLDS below and C5's instruction to only set explicit thresholds
where justified, marking anything else UNVERIFIED rather than inventing
a number.
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from application.evaluation.evaluators import evaluate_grounding
from evaluation.deterministic_checks import (
    check_confidence_in_range,
    check_contains_all,
    check_forbidden_claims,
    check_label_match,
    check_schema_validity,
)
from evaluation.runners import AGENT_RUNNERS
from evaluation.schemas import CheckResult, EvalCase, EvalCaseResult

DATASETS_DIR = Path(__file__).resolve().parent / "datasets"

# C5: explicit thresholds set only where the property being measured is
# unambiguous and safety-relevant (a forbidden claim appearing at all, or
# a required field missing at all, is never acceptable regardless of
# sample size). Anything else (e.g. an "AI quality" composite score) is
# NOT given an invented threshold here -- see the UNVERIFIED section of
# the printed report instead.
THRESHOLDS = {
    "schema_validity_rate": 1.0,   # must be exactly 100%
    "forbidden_claim_rate": 0.0,   # must be exactly 0%
}

REQUIRED_FIELDS = {
    "company_intelligence": ["technology_signals", "website_quality", "icp_alignment_score", "source"],
    "qualification": ["qualification_label", "total_score"],
    "decision": ["qualification", "recommended_action", "source"],
    "messaging": ["source"],
}


def load_dataset(agent: str) -> List[EvalCase]:
    path = DATASETS_DIR / f"{agent}.jsonl"
    cases = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(EvalCase.from_dict(json.loads(line)))
    return cases


def evaluate_case(case: EvalCase) -> EvalCaseResult:
    runner = AGENT_RUNNERS[case.agent]
    try:
        output = runner(case.input_context)
    except Exception as e:
        return EvalCaseResult(case_id=case.case_id, agent=case.agent, tags=case.tags, checks=[], error=str(e))

    checks: List[CheckResult] = []
    checks.append(check_schema_validity(output, REQUIRED_FIELDS.get(case.agent, [])))
    checks.append(check_forbidden_claims(output, case.must_not_claim))

    ep = case.expected_properties
    if "qualification_label" in ep:
        checks.append(check_label_match(output, "qualification_label", ep["qualification_label"]))
    if "total_score_min" in ep and "total_score_max" in ep:
        score = output.get("total_score")
        in_range = score is not None and ep["total_score_min"] <= score <= ep["total_score_max"]
        checks.append(CheckResult(
            "score_regression", in_range,
            "" if in_range else f"total_score={score} outside expected [{ep['total_score_min']}, {ep['total_score_max']}]",
        ))
    if "qualification" in ep:
        checks.append(check_label_match(output, "qualification", ep["qualification"]))
    if "source" in ep:
        checks.append(check_label_match(output, "source", ep["source"]))
    if "recommended_action" in ep:
        checks.append(check_label_match(output, "recommended_action", ep["recommended_action"]))
    if "technology_signals" in ep:
        checks.append(check_contains_all(output, "technology_signals", ep["technology_signals"]))
    if "message_nonempty" in ep:
        actual = output.get("message_nonempty")
        checks.append(CheckResult("message_nonempty", actual == ep["message_nonempty"]))

    # Grounding: reuses application/evaluation/evaluators.py's existing
    # word-overlap check for the Decision agent specifically, whose
    # evidence strings are either numeric ("Deterministic score:
    # X/100") or directly restate an upstream text field -- both of
    # which literally recur in a rendering of their own input_context,
    # making a text-overlap comparison meaningful (this mirrors the real
    # production check in application/workflows/graph_nodes.py's
    # confidence_evaluation, which grounds Decision's evidence the same
    # way). This is deliberately NOT applied to Company Intelligence's
    # evidence: its heuristic phrasing ("N technology signal(s)
    # detected: X") is English templating around a raw value, not a
    # restatement of it -- checking those sentences' words against a
    # rendering of the structured input produced a ~50% false-failure
    # rate in early testing of this harness (the connector words
    # "technology"/"signal(s)"/"detected" cannot appear in structured
    # JSON input no matter how it's rendered), which would be a
    # misleading, invented metric, not a real measurement. Company
    # Intelligence's grounding is instead verified by the
    # forbidden_claims check above (no unlisted fact appears) plus the
    # technology_signals contains_all check (every claimed technology is
    # exactly one the input actually provided) -- together a more
    # precise, deterministic pair of checks than a general-purpose
    # text-overlap heuristic designed for different evidence phrasing.
    if case.agent == "decision" and case.evidence:
        source_text = json.dumps(case.input_context)
        grounding = evaluate_grounding(case.evidence, source_text)
        checks.append(CheckResult(
            "grounding_nonzero", grounding > 0.0,
            f"grounding={grounding} for declared evidence {case.evidence}",
        ))

    return EvalCaseResult(case_id=case.case_id, agent=case.agent, tags=case.tags, checks=checks, raw_output=output)


def run_all(agent_filter: str = None, slice_filter: str = None) -> Dict[str, Any]:
    agents = [agent_filter] if agent_filter else list(AGENT_RUNNERS.keys())
    all_results: List[EvalCaseResult] = []
    for agent in agents:
        for case in load_dataset(agent):
            if slice_filter and slice_filter not in case.tags:
                continue
            all_results.append(evaluate_case(case))
    return summarize(all_results)


def summarize(results: List[EvalCaseResult]) -> Dict[str, Any]:
    by_agent: Dict[str, List[EvalCaseResult]] = defaultdict(list)
    by_slice: Dict[str, List[EvalCaseResult]] = defaultdict(list)
    for r in results:
        by_agent[r.agent].append(r)
        for tag in r.tags:
            by_slice[tag].append(r)

    def _agg(rs: List[EvalCaseResult]) -> Dict[str, Any]:
        n = len(rs)
        schema_ok = sum(1 for r in rs for c in r.checks if c.name == "schema_validity" and c.passed)
        schema_total = sum(1 for r in rs for c in r.checks if c.name == "schema_validity")
        forbidden_hits = sum(1 for r in rs for c in r.checks if c.name == "forbidden_claims" and not c.passed)
        return {
            "n": n,
            "pass_rate": round(sum(1 for r in rs if r.passed) / n, 3) if n else None,
            "schema_validity_rate": round(schema_ok / schema_total, 3) if schema_total else None,
            "forbidden_claim_rate": round(forbidden_hits / n, 3) if n else None,
            "errors": [r.case_id for r in rs if r.error],
            "failures": [
                {"case_id": r.case_id, "failed_checks": [c.name + ": " + c.detail for c in r.checks if not c.passed]}
                for r in rs if not r.passed
            ],
        }

    return {
        "total_cases": len(results),
        "by_agent": {agent: _agg(rs) for agent, rs in sorted(by_agent.items())},
        "by_slice": {tag: _agg(rs) for tag, rs in sorted(by_slice.items())},
        "overall": _agg(results),
    }


def check_thresholds(summary: Dict[str, Any]) -> List[str]:
    violations = []
    for agent, agg in summary["by_agent"].items():
        if agg["schema_validity_rate"] is not None and agg["schema_validity_rate"] < THRESHOLDS["schema_validity_rate"]:
            violations.append(f"{agent}: schema_validity_rate={agg['schema_validity_rate']} < {THRESHOLDS['schema_validity_rate']}")
        if agg["forbidden_claim_rate"] is not None and agg["forbidden_claim_rate"] > THRESHOLDS["forbidden_claim_rate"]:
            violations.append(f"{agent}: forbidden_claim_rate={agg['forbidden_claim_rate']} > {THRESHOLDS['forbidden_claim_rate']}")
    return violations


def print_report(summary: Dict[str, Any]) -> None:
    print(f"\n=== AI Evaluation Report ({summary['total_cases']} cases) ===\n")
    print("-- By agent --")
    for agent, agg in summary["by_agent"].items():
        print(f"  {agent:22s} n={agg['n']:3d}  pass_rate={agg['pass_rate']}  "
              f"schema_validity={agg['schema_validity_rate']}  forbidden_claim_rate={agg['forbidden_claim_rate']}")
        for f in agg["failures"]:
            print(f"      FAIL {f['case_id']}: {f['failed_checks']}")
    print("\n-- By slice (tag) --")
    for tag, agg in summary["by_slice"].items():
        print(f"  {tag:22s} n={agg['n']:3d}  pass_rate={agg['pass_rate']}")
    print("\n-- Confidence calibration --")
    print("  UNVERIFIED: not enough independently-labeled real-world outcome "
          "data exists in this environment to compute a meaningful "
          "confidence-calibration curve (confidence_bucket -> actual "
          "correctness) -- see C7's instruction not to manufacture one. "
          "The dataset/runner structure supports adding this once "
          "labeled outcome data exists (bucket each case's self-reported "
          "confidence, join against an actual-outcome field once one is "
          "tracked).")
    violations = check_thresholds(summary)
    print("\n-- Threshold check --")
    if violations:
        for v in violations:
            print(f"  VIOLATION: {v}")
    else:
        print("  All hard thresholds met (schema_validity=100%, forbidden_claim_rate=0%).")


def main():
    parser = argparse.ArgumentParser(description="LeadBoost AI evaluation harness")
    parser.add_argument("--agent", choices=list(AGENT_RUNNERS.keys()), default=None)
    parser.add_argument("--slice", dest="slice_filter", default=None)
    parser.add_argument("--json", action="store_true", help="print raw JSON summary instead of a text report")
    args = parser.parse_args()

    summary = run_all(agent_filter=args.agent, slice_filter=args.slice_filter)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_report(summary)

    violations = check_thresholds(summary)
    sys.exit(1 if violations else 0)


if __name__ == "__main__":
    main()
