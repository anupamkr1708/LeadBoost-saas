"""
Full-pipeline local validation harness:
Scraper -> Normalizer -> Enricher -> Company Intelligence -> Lead Scoring.

Runs entirely in-process against real URLs -- no database, no HTTP server,
no Celery/LangGraph runtime. Produces the same three files a release-
candidate check should produce:

    pipeline_results.json   -- full per-stage output for every URL
    pipeline_metrics.json   -- aggregated numbers computed from that run
    pipeline_summary.md     -- human-readable report

WHY NO DATABASE
----------------
core.domain.models.lead.Lead is a SQLAlchemy model with
`relationship("Organization", ...)` / `relationship("User", ...)` --
importing it standalone (without your app's full model registry already
loaded) raises `InvalidRequestError` the first time a mapper configures
(see the "SQLAlchemy mapper" issue from earlier validation runs). This
script never imports that class. `_LocalLead` below is a plain dataclass
carrying only the attributes WaterfallEnricher.enrich_lead_data() and
LeadScoringService.score_lead() actually read (checked against both
files directly, not guessed). `_LocalLeadContext` does the same for
whatever CompanyIntelligenceAgent reads off a LeadContext. Neither class
is imported from your app -- swap in the real ones if you want to
exercise the exact production types, but then you'll need a DB session
and full model registry.

DECISION STAGE IS OUT OF SCOPE
-------------------------------
This script stops at Lead Scoring. DecisionContext / decision_agent.py
were never reviewed in this conversation, so extending the script to
cover Decision would mean guessing at an unseen contract -- add that
stage yourself once you're ready, following the same pattern as the
company_intelligence/scoring stages below.

WHERE TO PUT THIS
-------------------
backend/scripts/test_pipeline_full.py

HOW TO RUN
-----------
    python -m scripts.test_pipeline_full
    python -m scripts.test_pipeline_full https://example.com https://another.com
"""

import asyncio
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from core.infrastructure.scraping.scraper import (  # noqa: E402
    ScrapingResult,
    TieredScraper,
    close_scraper_resources,
)
from core.infrastructure.normalization.normalizer import normalize_scraped_fields  # noqa: E402
from core.infrastructure.enrichment.enricher import (  # noqa: E402
    EnrichmentResult,
    WaterfallEnricher,
)
from application.agents.company_intelligence_agent import CompanyIntelligenceAgent  # noqa: E402
from core.domain.services.scoring import LeadScoringService  # noqa: E402

# ============================================================================
# EDIT THIS: sites to test.
# ============================================================================
TEST_URLS = [
    "https://mochishoes.com", 
    "https://www.zappos.com", 
    "https://www.allbirds.com", 
    "https://stripe.com", 
    "https://www.notion.so", 
    "https://slack.com", 
    "https://www.mayoclinic.org", 
    "https://www.clevelandclinic.org", 
    "https://www.harvard.edu", 
    "https://www.mit.edu", 
    "https://www.wwf.org", 
    "https://www.redcross.org", 
    "https://www.nasa.gov", 
    "https://www.usa.gov", 
    "https://www.dlapiper.com", 
    "https://www.zillow.com", 
    "https://www.marriott.com", 
    "https://www.hilton.com", 
    "https://www.3m.com", 
    "https://www.caterpillar.com", 
    "https://www.fedex.com", 
    "https://www.chase.com", 
    "https://www.cloudflare.com", 
    "https://www.airbnb.com", 
    "https://example.com", 
    "https://www.python.org", 
]

OUTPUT_DIR = _BACKEND_ROOT / "scripts"
RESULTS_PATH = OUTPUT_DIR / "pipeline_results.json"
METRICS_PATH = OUTPUT_DIR / "pipeline_metrics.json"
SUMMARY_PATH = OUTPUT_DIR / "pipeline_summary.md"

# Same 6 keys graph_nodes.py's real enrich() stage copies from
# EnrichmentResult.data onto the Lead row -- copied verbatim so this
# script's Lead ends up in the same state a real pipeline run would leave it.
_ENRICHMENT_LEAD_FIELDS = (
    "industry", "employees", "revenue_band", "founded_year", "contact_name", "contact_title",
)


@dataclass
class _LocalLead:
    """See module docstring. Fields match what WaterfallEnricher and
    LeadScoringService actually read off a Lead -- not the full ORM
    schema."""
    company_name: Optional[str] = None
    website: Optional[str] = None
    about_text: Optional[str] = None
    industry: Optional[str] = None
    employees: Optional[str] = None
    revenue_band: Optional[str] = None
    founded_year: Optional[int] = None
    contact_name: Optional[str] = None
    contact_title: Optional[str] = None
    linkedin_url: Optional[str] = None
    scrape_confidence: float = 0.0
    email_confidence: float = 0.0
    enrichment_confidence: float = 0.0


@dataclass
class _LocalLeadContext:
    """See module docstring. Fields match what
    company_intelligence_agent.py actually reads off a LeadContext."""
    company_name: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    employees: Optional[str] = None
    about_text: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    scraped_data: Dict[str, Any] = field(default_factory=dict)
    enriched_data: Dict[str, Any] = field(default_factory=dict)


def _print_section(title: str) -> None:
    print("\n" + "-" * 80)
    print(title)
    print("-" * 80)


def _serialize_scrape_result(result: ScrapingResult) -> Dict[str, Any]:
    return {
        "success": result.success,
        "method": result.method.value if hasattr(result.method, "value") else str(result.method),
        "confidence": result.confidence,
        "processing_time": result.processing_time,
        "pages_scraped": result.pages_scraped,
        "blocked_detected": result.blocked_detected,
        "tiers_attempted": result.tiers_attempted,
        "error_message": result.error_message,
        "data": result.data,
    }


def _serialize_enrichment_result(result: Optional[EnrichmentResult]) -> Optional[Dict[str, Any]]:
    if result is None:
        return None
    return {
        "success": result.success,
        "method": result.method.value if hasattr(result.method, "value") else str(result.method),
        "confidence": result.confidence,
        "processing_time_ms": result.processing_time,
        "data": result.data,
    }


def _best_email_confidence(normalized: Dict[str, Any]) -> float:
    """Best-effort local proxy for Lead.email_confidence -- the real app's
    population point for that column wasn't available to review, so this
    takes the highest per-email confidence normalize_scraped_fields already
    computed, defaulting to 0.0. Swap in the real logic if it differs."""
    emails = normalized.get("emails") or []
    confidences = [e.get("confidence", 0.0) for e in emails if isinstance(e, dict)]
    return max(confidences) if confidences else 0.0


async def run_pipeline_for_url(scraper: TieredScraper, url: str) -> Dict[str, Any]:
    print("\n" + "=" * 80)
    print(f"URL: {url}")
    print("=" * 80)
    stage_timings_ms: Dict[str, float] = {}

    # -- Stage 1: Scraper -------------------------------------------------
    t0 = time.time()
    scrape_result: ScrapingResult = await scraper.scrape(url)
    stage_timings_ms["scrape"] = (time.time() - t0) * 1000

    _print_section("STAGE 1 -- SCRAPER")
    print(f"Success: {scrape_result.success}  Confidence: {scrape_result.confidence:.3f}  "
          f"Pages: {scrape_result.pages_scraped}  Blocked: {scrape_result.blocked_detected}")
    if scrape_result.error_message:
        print(f"Error: {scrape_result.error_message}")

    record: Dict[str, Any] = {
        "url": url,
        "scrape": _serialize_scrape_result(scrape_result),
        "normalized": None,
        "enrichment": None,
        "company_intelligence": None,
        "score": None,
        "stage_timings_ms": stage_timings_ms,
    }
    if not scrape_result.data:
        print("\n(no data extracted -- stopping this URL here)")
        return record

    # -- Stage 2: Normalizer -----------------------------------------------
    t0 = time.time()
    normalized = normalize_scraped_fields(scrape_result.data)
    stage_timings_ms["normalize"] = (time.time() - t0) * 1000
    record["normalized"] = normalized

    _print_section("STAGE 2 -- NORMALIZER")
    org_type = normalized.get("organization_type")
    print(f"Organization type: {org_type['value'] if org_type else None}")
    print(f"Emails: {len(normalized.get('emails') or [])}  Phones: {len(normalized.get('phones') or [])}  "
          f"Address: {'yes' if normalized.get('address') else 'no'}")

    # -- Stage 3: Enricher ---------------------------------------------------
    lead = _LocalLead(
        company_name=normalized.get("brand_name") or scrape_result.data.get("company_name"),
        website=url,
        about_text=normalized.get("description"),
        linkedin_url=(normalized.get("social_profiles") or {}).get("linkedin_url")
        or scrape_result.data.get("linkedin_url"),
        scrape_confidence=scrape_result.confidence,
        email_confidence=_best_email_confidence(normalized),
    )
    t0 = time.time()
    enrichment_result: Optional[EnrichmentResult] = WaterfallEnricher().enrich_lead_data(
        lead, scrape_result.data
    )
    stage_timings_ms["enrich"] = (time.time() - t0) * 1000
    record["enrichment"] = _serialize_enrichment_result(enrichment_result)

    _print_section("STAGE 3 -- ENRICHER")
    if enrichment_result is None:
        print("(no result)")
    else:
        print(f"Method: {enrichment_result.method}  Confidence: {enrichment_result.confidence:.3f}  "
              f"Fields: {sorted(enrichment_result.data.keys())}")
        # Mirrors graph_nodes.py's real enrich() stage: copy these 6 fields
        # from enrichment output onto the Lead, same as production does.
        for key in _ENRICHMENT_LEAD_FIELDS:
            if key in enrichment_result.data:
                setattr(lead, key, enrichment_result.data[key])
        lead.enrichment_confidence = enrichment_result.confidence

    enriched_data = enrichment_result.data if enrichment_result else {}

    # -- Stage 4: Company Intelligence ---------------------------------------
    context = _LocalLeadContext(
        company_name=lead.company_name,
        website=url,
        industry=lead.industry,
        employees=lead.employees,
        about_text=lead.about_text,
        email=(normalized.get("emails") or [{}])[0].get("email") if normalized.get("emails") else None,
        linkedin_url=lead.linkedin_url,
        scraped_data=scrape_result.data,
        enriched_data=enriched_data,
    )
    t0 = time.time()
    ci_output = await asyncio.to_thread(CompanyIntelligenceAgent().run, context, True)
    stage_timings_ms["company_intelligence"] = (time.time() - t0) * 1000
    record["company_intelligence"] = ci_output.model_dump() if hasattr(ci_output, "model_dump") else vars(ci_output)

    _print_section("STAGE 4 -- COMPANY INTELLIGENCE")
    print(f"Source: {ci_output.source}  ICP alignment: {ci_output.icp_alignment_score}  "
          f"Website quality: {ci_output.website_quality}")
    print(f"Technology signals: {ci_output.technology_signals}")
    print(f"Pain points: {ci_output.pain_points}  Growth indicators: {ci_output.growth_indicators}")

    # -- Stage 5: Lead Scoring -----------------------------------------------
    t0 = time.time()
    score_result = LeadScoringService().score_lead(lead, company_intelligence=ci_output)
    stage_timings_ms["scoring"] = (time.time() - t0) * 1000
    record["score"] = {
        "total_score": score_result.total_score,
        "qualification_label": score_result.qualification_label,
        "criteria_scores": score_result.criteria_scores,
    }

    _print_section("STAGE 5 -- LEAD SCORING")
    print(f"Total: {score_result.total_score:.1f}  Label: {score_result.qualification_label}")
    for name, val in score_result.criteria_scores.items():
        print(f"  {name:<20} {val:.2f}")

    return record


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _latency_stats(values: List[float]) -> Dict[str, float]:
    if not values:
        return {"avg_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0}
    sorted_vals = sorted(values)
    p95_index = min(len(sorted_vals) - 1, int(round(0.95 * (len(sorted_vals) - 1))))
    return {
        "avg_ms": round(sum(values) / len(values), 1),
        "median_ms": round(statistics.median(values), 1),
        "p95_ms": round(sorted_vals[p95_index], 1),
    }


def _build_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(results)
    scraped = [r for r in results if r.get("scrape")]
    scrape_success = [r for r in scraped if r["scrape"]["success"]]
    normalized = [r for r in results if r.get("normalized")]
    enriched = [r for r in results if r.get("enrichment")]
    ci_results = [r for r in results if r.get("company_intelligence")]
    ci_llm = [r for r in ci_results if r["company_intelligence"].get("source") == "llm"]
    scored = [r for r in results if r.get("score")]

    stage_latencies = {}
    for stage in ("scrape", "normalize", "enrich", "company_intelligence", "scoring"):
        vals = [r["stage_timings_ms"].get(stage) for r in results if r["stage_timings_ms"].get(stage) is not None]
        stage_latencies[stage] = _latency_stats(vals)

    # Field completeness -- from normalizer output
    field_names = ["organization_type", "brand_name", "description", "founded_year",
                   "employee_count", "emails", "phones", "address", "operating_regions",
                   "products", "services", "social_profiles", "technologies"]
    field_completeness = {
        f: _pct(sum(1 for r in normalized if r["normalized"].get(f)), len(normalized))
        for f in field_names
    }

    # Evidence completeness -- from enrichment output
    evidence_fields = ["business_type", "technologies", "operating_regions", "offerings", "primary_contact"]
    evidence_completeness = {
        f: _pct(sum(1 for r in enriched if r["enrichment"]["data"].get(f)), len(enriched))
        for f in evidence_fields
    }

    # Hallucination proxy: heuristic-sourced CI output must NEVER have
    # non-empty pain_points/growth_indicators (see company_intelligence_agent.py
    # -- deterministic path always returns [] for both by design). Any
    # violation here means that invariant broke.
    heuristic_ci = [r for r in ci_results if r["company_intelligence"].get("source") == "heuristic"]
    heuristic_violations = [
        r["url"] for r in heuristic_ci
        if r["company_intelligence"].get("pain_points") or r["company_intelligence"].get("growth_indicators")
    ]

    scores = [r["score"]["total_score"] for r in scored]
    labels = [r["score"]["qualification_label"] for r in scored]

    # company_size hardcoded-band-gap exposure: how many real leads in this
    # run fall in an employee band the current scoring config gives zero
    # credit to regardless of fit (see validation report from last turn).
    excluded_bands = {"1-10", "500+"}
    band_gap_hits = [r["url"] for r in scored if r["score"]["criteria_scores"].get("company_size", -1) == 0.0]

    return {
        "sites_tested": n,
        "stage_success_rate_pct": {
            "scrape": _pct(len(scrape_success), n),
            "normalize": _pct(len(normalized), n),
            "enrich": _pct(len(enriched), n),
            "company_intelligence": _pct(len(ci_results), n),
            "scoring": _pct(len(scored), n),
        },
        "stage_latency_ms": stage_latencies,
        "company_intelligence_llm_usage_rate_pct": _pct(len(ci_llm), len(ci_results)),
        "field_completeness_pct": field_completeness,
        "evidence_completeness_pct": evidence_completeness,
        "explainability_coverage_pct": {
            "company_intelligence": _pct(
                sum(1 for r in ci_results if (r["company_intelligence"].get("explanation") or {}).get("evidence")),
                len(ci_results),
            ),
            "lead_scoring": 0.0,  # ScoreResult carries no per-criterion reason/evidence -- known gap, see prior report
        },
        "hallucination_check": {
            "heuristic_results_checked": len(heuristic_ci),
            "invariant_violations": heuristic_violations,
            "status": "PASS" if not heuristic_violations else "FAIL",
        },
        "scoring_distribution": {
            "n": len(scores),
            "mean": round(sum(scores) / len(scores), 1) if scores else None,
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
            "label_counts": {label: labels.count(label) for label in set(labels)},
        },
        "company_size_band_gap_hits": band_gap_hits,
        "failures_by_stage": {
            "scrape": [r["url"] for r in results if not r.get("scrape") or not r["scrape"]["success"]],
            "enrich": [r["url"] for r in normalized if not r.get("enrichment")],
            "company_intelligence": [r["url"] for r in enriched if not r.get("company_intelligence")],
            "scoring": [r["url"] for r in ci_results if not r.get("score")],
        },
    }


def _build_summary_md(results: List[Dict[str, Any]], metrics: Dict[str, Any]) -> str:
    lines = [
        "# Pipeline Validation Report (real run)",
        "",
        f"Sites tested: {metrics['sites_tested']}",
        "",
        "## Stage success rate",
        "",
        "| Stage | Success % |",
        "|---|---|",
    ]
    for stage, pct in metrics["stage_success_rate_pct"].items():
        lines.append(f"| {stage} | {pct}% |")

    lines += ["", "## Stage latency (ms)", "", "| Stage | Avg | Median | P95 |", "|---|---|---|---|"]
    for stage, stats in metrics["stage_latency_ms"].items():
        lines.append(f"| {stage} | {stats['avg_ms']} | {stats['median_ms']} | {stats['p95_ms']} |")

    lines += [
        "",
        "## Hallucination check",
        "",
        f"Status: **{metrics['hallucination_check']['status']}** "
        f"({metrics['hallucination_check']['heuristic_results_checked']} heuristic results checked)",
    ]
    if metrics["hallucination_check"]["invariant_violations"]:
        lines.append(f"Violations: {metrics['hallucination_check']['invariant_violations']}")

    lines += [
        "",
        "## Scoring distribution",
        "",
        f"n={metrics['scoring_distribution']['n']}  "
        f"mean={metrics['scoring_distribution']['mean']}  "
        f"min={metrics['scoring_distribution']['min']}  "
        f"max={metrics['scoring_distribution']['max']}",
        f"Labels: {metrics['scoring_distribution']['label_counts']}",
        "",
        "## Known, previously-flagged gap this run can quantify",
        "",
        f"Leads scoring 0 on company_size (hardcoded band list excludes '1-10' and "
        f"'500+' regardless of fit): {metrics['company_size_band_gap_hits']}",
        "",
        "## Field / evidence completeness",
        "",
        "Field completeness (normalizer): " + json.dumps(metrics["field_completeness_pct"]),
        "",
        "Evidence completeness (enricher): " + json.dumps(metrics["evidence_completeness_pct"]),
        "",
        "## Failures by stage",
        "",
        json.dumps(metrics["failures_by_stage"], indent=2),
    ]
    return "\n".join(lines)


async def main() -> None:
    urls = sys.argv[1:] if len(sys.argv) > 1 else TEST_URLS
    results: List[Dict[str, Any]] = []

    try:
        async with TieredScraper() as scraper:
            for url in urls:
                try:
                    results.append(await run_pipeline_for_url(scraper, url))
                except Exception as e:
                    print(f"\n[FATAL] Unhandled exception for {url}: {e!r}")
                    results.append({"url": url, "fatal_error": repr(e)})
    finally:
        await close_scraper_resources()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    valid_results = [r for r in results if "fatal_error" not in r]
    metrics = _build_metrics(valid_results)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2, ensure_ascii=False, default=str))
    SUMMARY_PATH.write_text(_build_summary_md(valid_results, metrics))

    print(f"\nWrote: {RESULTS_PATH}\nWrote: {METRICS_PATH}\nWrote: {SUMMARY_PATH}")


if __name__ == "__main__":
    asyncio.run(main())