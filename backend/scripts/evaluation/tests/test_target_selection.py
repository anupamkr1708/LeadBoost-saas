"""
Unit tests for `scripts.run_full_pipeline_benchmark.select_targets` -- pure
selection logic over already-computed Discovery output
(`discovery_eval.metrics.QueryRecord` / `BusinessRecord`). No network, no
pipeline stage invoked.

    python -m pytest backend/scripts/evaluation/tests/test_target_selection.py -q
"""

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from discovery_eval.metrics import BusinessRecord, QueryRecord  # noqa: E402

from scripts.run_full_pipeline_benchmark import select_targets  # noqa: E402


def _query(qid: str) -> QueryRecord:
    return QueryRecord(id=qid, query=f"query {qid}", domain_tag="Retail", difficulty_tag="standard")


def _business(query_id: str, website: str, rank_score: float, outcome: str = "selected",
              validated: bool = True) -> BusinessRecord:
    return BusinessRecord(
        query_id=query_id, query=f"query {query_id}", business_name=website, provider="test",
        address=None, category="retail", website_candidate=website, final_website=website,
        website_validated=validated, validation_reason=None, evidence_count=1, feature_count=1,
        verification_count=1, confidence=0.8, identity_stability=0.8, provider_agreement=0.8,
        competition_score=0.5, rank_score=rank_score, selection_reason="test", rejected_reason=None,
        false_positive_flags="", outcome=outcome,
    )


def test_selects_top_n_per_query_by_rank_score():
    queries = [_query("q1")]
    businesses = [
        _business("q1", "https://low.com", 10.0),
        _business("q1", "https://high.com", 90.0),
        _business("q1", "https://mid.com", 50.0),
    ]
    targets = select_targets(queries, businesses, top_n_per_query=2)
    websites = [b.final_website for _, b in targets]
    assert websites == ["https://high.com", "https://mid.com"]


def test_zero_means_no_cap():
    queries = [_query("q1")]
    businesses = [_business("q1", f"https://site{i}.com", float(i)) for i in range(5)]
    targets = select_targets(queries, businesses, top_n_per_query=0)
    assert len(targets) == 5


def test_only_selected_and_validated_outcomes_are_targets():
    queries = [_query("q1")]
    businesses = [
        _business("q1", "https://good.com", 50.0, outcome="selected", validated=True),
        _business("q1", "https://not-selected.com", 99.0, outcome="not_selected", validated=True),
        _business("q1", "https://invalid.com", 99.0, outcome="selected", validated=False),
    ]
    targets = select_targets(queries, businesses, top_n_per_query=0)
    assert [b.final_website for _, b in targets] == ["https://good.com"]


def test_skips_business_records_for_unknown_query_id():
    # Defensive: a BusinessRecord referencing a query_id not present in
    # query_records should never crash target selection.
    queries = [_query("q1")]
    businesses = [_business("q_missing", "https://orphan.com", 50.0)]
    targets = select_targets(queries, businesses, top_n_per_query=1)
    assert targets == []


def test_multiple_queries_each_get_their_own_cap():
    queries = [_query("q1"), _query("q2")]
    businesses = [
        _business("q1", "https://a1.com", 10.0),
        _business("q1", "https://a2.com", 20.0),
        _business("q2", "https://b1.com", 5.0),
    ]
    targets = select_targets(queries, businesses, top_n_per_query=1)
    websites = sorted(b.final_website for _, b in targets)
    assert websites == ["https://a2.com", "https://b1.com"]
