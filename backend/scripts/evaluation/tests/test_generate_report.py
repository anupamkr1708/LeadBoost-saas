"""
Unit tests for `scripts.evaluation.generate_report` -- pure formatting/I-O,
no network, no pipeline stage invoked.

    python -m pytest backend/scripts/evaluation/tests/test_generate_report.py -q
"""

import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from discovery_eval.metrics import QueryRecord  # noqa: E402

from scripts.evaluation.generate_report import _recommendations, write_all  # noqa: E402
from scripts.evaluation.metrics import SiteResult, build_combined_metrics  # noqa: E402


def _empty_combined_metrics():
    return build_combined_metrics(
        {"total_businesses_found": 0, "total_validated": 0,
         "directory_marketplace_selected_count": 0, "social_profile_selected_count": 0},
        [],
    )


def test_no_recommendations_fire_on_zero_sites_except_the_zero_sites_notice():
    recs = _recommendations(_empty_combined_metrics())
    assert len(recs) == 1
    assert "no sites were pushed" in recs[0].lower()


def test_hallucination_failure_recommendation_fires_regardless_of_sample_size():
    site = SiteResult(
        query_id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
        business_name="x", website="https://x.com", rank_score=1.0, discovery_confidence=0.5,
        company_intelligence={
            "website_quality": "good", "technology_signals": [], "pain_points": ["bad"],
            "growth_indicators": [], "icp_alignment_score": 0.1,
            "explanation": {"evidence": []}, "source": "heuristic",
        },
    )
    combined = build_combined_metrics({"total_businesses_found": 0, "total_validated": 0,
                                        "directory_marketplace_selected_count": 0,
                                        "social_profile_selected_count": 0}, [site])
    recs = _recommendations(combined)
    assert any("hallucination check FAILED" in r for r in recs)


def test_write_all_produces_five_files(tmp_path):
    query_records = [QueryRecord(id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
                                  businesses_found=1, validated_businesses=1)]
    site = SiteResult(
        query_id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
        business_name="x", website="https://x.com", rank_score=1.0, discovery_confidence=0.5,
        score={"total_score": 50.0, "qualification_label": "Qualified", "criteria_scores": {}},
        final_status="SUCCESS",
    )
    combined = build_combined_metrics({"total_businesses_found": 1, "total_validated": 1,
                                        "directory_marketplace_selected_count": 0,
                                        "social_profile_selected_count": 0}, [site])

    write_all(combined, query_records, [site], tmp_path)

    expected = {
        "pipeline_metrics.json", "pipeline_summary.md", "pipeline_results.json",
        "stage_metrics.csv", "benchmark_summary.csv",
    }
    assert expected.issubset({p.name for p in tmp_path.iterdir()})

    metrics_payload = json.loads((tmp_path / "pipeline_metrics.json").read_text())
    assert metrics_payload["pipeline"]["sites_processed"] == 1

    results_payload = json.loads((tmp_path / "pipeline_results.json").read_text())
    assert results_payload["sites_tested"] == 1
