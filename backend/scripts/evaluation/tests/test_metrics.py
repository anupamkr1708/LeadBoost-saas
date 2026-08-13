"""
Unit tests for `scripts.evaluation.metrics` -- pure functions only, no
network, no pipeline stage invoked. Fixtures are hand-built `SiteResult`
instances shaped exactly like what `run_full_pipeline_benchmark.py`
actually produces (see that module's `_serialize_scrape_result` /
`_serialize_enrichment_result` and `CompanyIntelligenceOutput.model_dump()`
for the real field names these mirror), matching
`discovery_eval/tests/test_discovery_eval.py`'s own convention of testing
the metrics layer with stubs rather than a live pipeline run.

    python -m pytest backend/scripts/evaluation/tests/test_metrics.py -q
"""

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.evaluation.metrics import (  # noqa: E402
    SiteResult,
    build_combined_metrics,
    compute_company_intelligence_metrics,
    compute_enricher_metrics,
    compute_lead_scoring_metrics,
    compute_normalizer_metrics,
    compute_pipeline_metrics,
    compute_scraper_metrics,
)


def _site(**overrides) -> SiteResult:
    defaults = dict(
        query_id="retail-01",
        query="shoe stores in Mumbai",
        domain_tag="Retail",
        difficulty_tag="standard",
        business_name="Test Co",
        website="https://example.com",
        rank_score=100.0,
        discovery_confidence=0.9,
    )
    defaults.update(overrides)
    return SiteResult(**defaults)


def _successful_site(**overrides) -> SiteResult:
    site = _site(
        scrape={
            "success": True, "method": "requests", "confidence": 0.8,
            "processing_time": 1.2, "pages_scraped": 2, "blocked_detected": False,
            "tiers_attempted": ["requests", "playwright"], "error_message": None,
            "data": {"company_name_source": "jsonld_organization", "jsonld_raw": {"name": "Test Co"}},
        },
        normalized={
            "organization_type": {"value": "LocalBusiness", "confidence": 0.7},
            "brand_name": "Test Co", "description": "A test company", "founded_year": 2010,
            "employee_count": None, "emails": [{"email": "a@x.com", "confidence": 0.9}],
            "phones": [], "address": "1 Main St", "operating_regions": ["Mumbai"],
            "products": [], "services": ["retail"], "social_profiles": {"linkedin_url": "https://linkedin.com/x"},
            "technologies": ["Shopify"],
            "field_confidence": {"email": 0.9, "brand_name": 0.85},
        },
        enrichment={
            "success": True, "method": "heuristic", "confidence": 0.75, "processing_time_ms": 12,
            "data": {"business_type": "retail", "technologies": ["Shopify"], "industry": "Retail"},
        },
        company_intelligence={
            "website_quality": "good", "technology_signals": ["Shopify"], "pain_points": [],
            "growth_indicators": [], "icp_alignment_score": 0.6,
            "explanation": {"evidence": ["Uses Shopify"]}, "source": "heuristic",
        },
        score={
            "total_score": 65.0, "qualification_label": "Qualified",
            "criteria_scores": {"industry_match": 10.0, "company_size": 5.0},
        },
        grounded_technology_precision=1.0,
        stage_timings_ms={"scrape": 500, "normalize": 5, "enrich": 20, "company_intelligence": 300, "scoring": 2},
        final_status="SUCCESS",
    )
    for k, v in overrides.items():
        setattr(site, k, v)
    return site


def test_scraper_metrics_success_rate_and_confidence():
    results = [_successful_site(), _site(scrape={
        "success": False, "method": "requests", "confidence": 0.1, "processing_time": 0.5,
        "pages_scraped": 1, "blocked_detected": True, "tiers_attempted": ["requests"],
        "error_message": "blocked", "data": {},
    }, stage_timings_ms={"scrape": 100})]

    metrics = compute_scraper_metrics(results)
    assert metrics["sites_attempted"] == 2
    assert metrics["scrape_success_rate"] == 0.5
    assert metrics["blocked_rate"] == 0.5
    assert metrics["confidence_distribution"]["n"] == 2
    assert metrics["structured_extraction_coverage"] == 1.0  # the one successful site had jsonld_raw


def test_normalizer_field_completeness():
    results = [_successful_site(), _site(normalized=None)]
    metrics = compute_normalizer_metrics(results)
    assert metrics["sites_normalized"] == 1
    assert metrics["field_completeness_pct"]["brand_name"] == 100.0
    assert metrics["field_completeness_pct"]["phones"] == 0.0
    assert metrics["technology_coverage"] == 100.0


def test_enricher_method_distribution():
    heuristic = _successful_site()
    llm_site = _successful_site(enrichment={
        "success": True, "method": "llm", "confidence": 0.5, "processing_time_ms": 900, "data": {},
    })
    metrics = compute_enricher_metrics([heuristic, llm_site])
    assert metrics["sites_eligible"] == 2
    assert metrics["enrichment_coverage"] == 1.0
    assert metrics["deterministic_contribution_rate"] == 0.5
    assert metrics["llm_contribution_rate"] == 0.5
    assert metrics["llm_fallback_rate_upper_bound"] == 0.5


def test_company_intelligence_hallucination_check_passes_when_clean():
    metrics = compute_company_intelligence_metrics([_successful_site()])
    assert metrics["hallucination_check"]["status"] == "PASS"
    assert metrics["heuristic_usage_rate"] == 1.0
    assert metrics["explainability_coverage"] == 1.0


def test_company_intelligence_hallucination_check_flags_violation():
    bad = _successful_site(company_intelligence={
        "website_quality": "good", "technology_signals": [], "pain_points": ["should never appear"],
        "growth_indicators": [], "icp_alignment_score": 0.6,
        "explanation": {"evidence": []}, "source": "heuristic",
    })
    metrics = compute_company_intelligence_metrics([bad])
    assert metrics["hallucination_check"]["status"] == "FAIL"
    assert len(metrics["hallucination_check"]["invariant_violations"]) == 1


def test_lead_scoring_distribution_and_criteria():
    results = [_successful_site(), _successful_site(score={
        "total_score": 40.0, "qualification_label": "Disqualified",
        "criteria_scores": {"industry_match": 2.0, "company_size": 1.0},
    })]
    metrics = compute_lead_scoring_metrics(results)
    assert metrics["leads_scored"] == 2
    assert metrics["score_distribution"]["avg"] == 52.5
    assert metrics["qualification_distribution_pct"]["Qualified"] == 50.0
    assert metrics["criterion_avg_contribution"]["industry_match"] == 6.0


def test_pipeline_metrics_end_to_end_and_stage_completion():
    ok = _successful_site()
    failed = _site(scrape={
        "success": False, "method": "requests", "confidence": 0.0, "processing_time": 0.1,
        "pages_scraped": 0, "blocked_detected": False, "tiers_attempted": ["requests"],
        "error_message": "timeout", "data": {},
    }, final_status="FAILED", stage_timings_ms={"scrape": 50})
    partial = _successful_site(
        errors=[{"stage": "scoring", "error": "ValueError: boom"}],
        final_status="PARTIAL_SUCCESS",
        score=None,
    )

    metrics = compute_pipeline_metrics([ok, failed, partial])
    assert metrics["sites_processed"] == 3
    assert metrics["end_to_end_success_rate"] == round(1 / 3, 4)
    assert metrics["failure_rate"] == round(1 / 3, 4)
    assert metrics["partial_success_rate"] == round(1 / 3, 4)
    assert metrics["stage_completion_rate"]["scrape"] == round(2 / 3, 4)
    assert metrics["failures_by_stage"]["scoring"] == [f"{partial.query_id}:{partial.website}"]
    assert metrics["evidence_preservation_rate"] == 1.0  # both normalized sites carried evidence to CI


def test_pipeline_metrics_empty_results_does_not_crash():
    metrics = compute_pipeline_metrics([])
    assert metrics["sites_processed"] == 0
    assert metrics["end_to_end_success_rate"] == 0.0
    assert metrics["evidence_preservation_rate"] == 0.0


def test_build_combined_metrics_derives_official_domain_precision():
    discovery_aggregate = {
        "total_validated": 10,
        "directory_marketplace_selected_count": 1,
        "social_profile_selected_count": 1,
    }
    combined = build_combined_metrics(discovery_aggregate, [_successful_site()])
    assert combined["discovery"]["official_domain_precision"] == 0.8
    assert "scraper" in combined and "pipeline" in combined
