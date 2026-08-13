"""
Full-Pipeline Evaluation — Metrics.

Pure, reusable metric computation only. No pipeline orchestration, no
network calls, no business logic beyond "read fields the pipeline stages
already computed and turn them into aggregate numbers" -- exactly the
same discipline `discovery_eval/metrics.py` already follows for the
Discovery stage (see that module's own docstring). Nothing in this file
recomputes a signal a pipeline stage already owns; it only reads what
`run_full_pipeline_benchmark.py` already collected into a `SiteResult`.

Discovery-stage metrics are NOT recomputed here -- `aggregate_metrics()`
in `discovery_eval/metrics.py` already computes every Discovery metric
this framework needs (resolution rate, validation success, duplicate
rate, provider agreement, parse success, latency percentiles, ...). This
module is only responsible for the stages downstream of Discovery:
Scraper, Normalizer, Enricher, Company Intelligence, Lead Scoring, and
the end-to-end Pipeline rollup. `build_combined_metrics()` at the bottom
merges the (untouched) Discovery aggregate with this module's own
aggregates into the single dict `generate_report.py` writes out as
`pipeline_metrics.json`.

Every threshold below is a fixed, documented constant (never learned,
never tuned on this run's own data) -- the same discipline
`discovery_eval/metrics.py` uses for its own audit thresholds.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# Reuse the exact percentile implementation discovery_eval.metrics already
# uses for its own latency percentiles, rather than re-deriving it -- a
# generic, dependency-free statistics helper with no Discovery-specific
# logic in it. (Leading underscore is a module-private naming convention,
# not an enforced boundary; this is the same kind of cross-module reuse
# discovery_eval/metrics.py itself does for `website_validator.domain_of`.)
from discovery_eval.metrics import _percentile as _percentile_of

# The exact stage names used throughout this framework, matching the
# order `run_full_pipeline_benchmark.py` executes them in and the stage
# names `application.workflows.graph_nodes.py` uses for the equivalent
# production stages (scrape / enrich / company_intelligence / qualification).
# "scoring" here == production's "qualification" stage; kept as "scoring"
# to match core.domain.services.scoring.LeadScoringService's own name and
# scripts/test_pipeline_full.py's existing precedent.
PIPELINE_STAGES = ("scrape", "normalize", "enrich", "company_intelligence", "scoring")

# Fixed, documented audit thresholds (never learned, never tuned on this
# run's own data) -- mirrors discovery_eval/metrics.py's own convention.
WEAK_CONFIDENCE_THRESHOLD = 0.5
LOW_ENRICHMENT_CONFIDENCE_THRESHOLD = 0.5
HIGH_VALIDATION_FAILURE_RATE = 0.25


# ---------------------------------------------------------------------------
# Record shape: one row per (query, selected website) pair pushed through
# the full pipeline.
# ---------------------------------------------------------------------------


@dataclass
class SiteResult:
    """One business, selected by Discovery for one benchmark query, pushed
    through Scraper -> Normalizer -> Enricher -> Company Intelligence ->
    Lead Scoring. Field shapes for `scrape`/`enrichment`/`company_intelligence`/
    `score` mirror the serialization already used by
    `scripts/test_pipeline_full.py` (`_serialize_scrape_result`,
    `_serialize_enrichment_result`) and `application/workflows/graph_nodes.py`
    (`_serialize_scraping_result`, `_serialize_enrichment_result`,
    `_serialize_score_result`) so this framework's JSON output reads the
    same way production's own serialized stage output does.
    """

    # -- identity / provenance (traces back to the exact Discovery query
    # and BusinessRecord this site came from; see discovery_eval.metrics) --
    query_id: str
    query: str
    domain_tag: str
    difficulty_tag: str
    business_name: str
    website: str
    rank_score: float
    discovery_confidence: Optional[float]  # BusinessRecord.confidence (Discovery's own selection_confidence)

    # -- per-stage raw output (None if the stage never ran, e.g. an earlier
    # stage produced nothing to hand it) --
    scrape: Optional[Dict[str, Any]] = None
    normalized: Optional[Dict[str, Any]] = None
    enrichment: Optional[Dict[str, Any]] = None
    company_intelligence: Optional[Dict[str, Any]] = None
    score: Optional[Dict[str, Any]] = None

    # -- audit: recomputed by re-reading CompanyIntelligenceAgent's own
    # evidence-gathering output (see run_full_pipeline_benchmark.py's use
    # of CompanyIntelligenceAgent._gather_evidence), never re-derived from
    # raw text. None when company_intelligence never ran. --
    grounded_technology_precision: Optional[float] = None

    stage_timings_ms: Dict[str, int] = field(default_factory=dict)
    errors: List[Dict[str, str]] = field(default_factory=list)

    # Mirrors application.dto.models.PipelineStatus / lead_pipeline.py's
    # own final_status rule exactly: SUCCESS if no stage raised, FAILED if
    # the very first stage (scrape) never produced usable data at all,
    # PARTIAL_SUCCESS otherwise.
    final_status: str = "FAILED"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _avg(values: List[float]) -> Optional[float]:
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 4) if values else None


def _latency_stats(values: List[float]) -> Dict[str, float]:
    values = [v for v in values if v is not None]
    if not values:
        return {"avg_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "n": 0}
    return {
        "avg_ms": round(statistics.fmean(values), 1),
        "median_ms": round(statistics.median(values), 1),
        "p95_ms": round(_percentile_of(values, 0.95), 1),
        "n": len(values),
    }


def _confidence_distribution(values: List[float]) -> Dict[str, Any]:
    """Same five-number summary shape used for every confidence-bearing
    stage below, so the report can show them side by side."""
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0, "avg": None, "median": None, "min": None, "max": None}
    return {
        "n": len(values),
        "avg": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


# ---------------------------------------------------------------------------
# Scraper metrics
# ---------------------------------------------------------------------------


def compute_scraper_metrics(results: List[SiteResult]) -> Dict[str, Any]:
    scraped = [r for r in results if r.scrape is not None]
    success = [r for r in scraped if r.scrape.get("success")]
    blocked = [r for r in scraped if r.scrape.get("blocked_detected")]
    multi_page = [r for r in success if (r.scrape.get("pages_scraped") or 1) > 1]

    tier_usage: Dict[str, int] = {}
    for r in scraped:
        for tier in r.scrape.get("tiers_attempted") or []:
            tier_usage[tier] = tier_usage.get(tier, 0) + 1

    method_usage: Dict[str, int] = {}
    for r in success:
        method_usage[r.scrape.get("method", "unknown")] = method_usage.get(r.scrape.get("method", "unknown"), 0) + 1

    # "Structured extraction coverage": fraction of successful scrapes that
    # produced at least one structured-data signal -- either a non-empty
    # JSON-LD block (`data["jsonld_raw"]`, always sourced from
    # `_parse_page`'s canonical JSON-LD extraction per
    # core/infrastructure/scraping/scraper.py's own comment) or a
    # company name resolved from JSON-LD rather than a weaker fallback
    # (`company_name_source` in `_COMPANY_NAME_SOURCE_CONFIDENCE`'s
    # "jsonld_organization"/"jsonld_generic_name" keys) -- read from the
    # scraper's own raw `data` dict, never re-parsed from HTML here.
    structured_hits = sum(
        1 for r in success
        if r.scrape.get("data") and (
            r.scrape["data"].get("jsonld_raw")
            or str(r.scrape["data"].get("company_name_source", "")).startswith("jsonld")
        )
    )

    return {
        "sites_attempted": len(scraped),
        "scrape_success_rate": _rate(len(success), len(scraped)),
        "blocked_rate": _rate(len(blocked), len(scraped)),
        "fallback_tier_usage_counts": tier_usage,
        "final_method_counts": method_usage,
        "avg_pages_scraped": _avg([r.scrape.get("pages_scraped") for r in success]),
        "multi_page_enrichment_rate": _rate(len(multi_page), len(success)),
        "structured_extraction_coverage": _rate(structured_hits, len(success)),
        "confidence_distribution": _confidence_distribution([r.scrape.get("confidence") for r in scraped]),
        "latency_ms": _latency_stats([r.stage_timings_ms.get("scrape") for r in scraped]),
    }


# ---------------------------------------------------------------------------
# Normalizer metrics
# ---------------------------------------------------------------------------

# Same field list scripts/test_pipeline_full.py already used for its own
# field-completeness metric -- the canonical output keys of
# core.infrastructure.normalization.normalizer.normalize_scraped_fields.
_NORMALIZER_FIELDS = (
    "organization_type", "brand_name", "description", "founded_year",
    "employee_count", "emails", "phones", "address", "operating_regions",
    "products", "services", "social_profiles", "technologies",
)


def compute_normalizer_metrics(results: List[SiteResult]) -> Dict[str, Any]:
    normalized = [r for r in results if r.normalized is not None]
    n = len(normalized)

    field_completeness = {
        f: _pct(sum(1 for r in normalized if r.normalized.get(f)), n) for f in _NORMALIZER_FIELDS
    }

    all_field_confidences: List[float] = []
    for r in normalized:
        all_field_confidences.extend((r.normalized.get("field_confidence") or {}).values())

    return {
        "sites_normalized": n,
        "field_completeness_pct": field_completeness,
        "organization_type_coverage": _pct(sum(1 for r in normalized if r.normalized.get("organization_type")), n),
        "address_coverage": _pct(sum(1 for r in normalized if r.normalized.get("address")), n),
        "social_coverage": _pct(sum(1 for r in normalized if r.normalized.get("social_profiles")), n),
        "technology_coverage": _pct(sum(1 for r in normalized if r.normalized.get("technologies")), n),
        "confidence_distribution": _confidence_distribution(all_field_confidences),
        "latency_ms": _latency_stats([r.stage_timings_ms.get("normalize") for r in normalized]),
    }


# ---------------------------------------------------------------------------
# Enricher metrics
# ---------------------------------------------------------------------------


def compute_enricher_metrics(results: List[SiteResult]) -> Dict[str, Any]:
    attempted = [r for r in results if r.normalized is not None]  # enrichment only ever runs if normalize ran
    enriched = [r for r in results if r.enrichment is not None]

    method_counts: Dict[str, int] = {}
    for r in enriched:
        method_counts[r.enrichment.get("method", "unknown")] = method_counts.get(r.enrichment.get("method", "unknown"), 0) + 1
    n = len(enriched)

    # EnrichmentResult only ever exposes the WINNING tier's method label
    # (core.infrastructure.enrichment.enricher.WaterfallEnricher._finalize),
    # never the full `contributing_methods` list it computed internally --
    # so "LLM fallback rate" here is deliberately defined as, and reported
    # as, an upper-bound proxy ("method is llm or merged"), not an exact
    # count of runs where the LLM tier specifically fired. Documented here
    # rather than silently approximated, matching this codebase's own
    # convention for a known observability gap (see
    # scripts/test_pipeline_full.py's `explainability_coverage` comment on
    # lead_scoring for the same style of caveat).
    llm_or_merged = sum(1 for r in enriched if r.enrichment.get("method") in ("llm", "merged"))

    evidence_fields = ("business_type", "technologies", "operating_regions", "offerings", "primary_contact")
    evidence_completeness = {
        f: _pct(sum(1 for r in enriched if (r.enrichment.get("data") or {}).get(f)), n) for f in evidence_fields
    }

    return {
        "sites_eligible": len(attempted),
        "enrichment_coverage": _rate(n, len(attempted)),
        "method_distribution_pct": {k: _pct(v, n) for k, v in method_counts.items()},
        "deterministic_contribution_rate": _rate(method_counts.get("heuristic", 0), n),
        "external_api_contribution_rate": _rate(method_counts.get("external_api", 0), n),
        "llm_contribution_rate": _rate(method_counts.get("llm", 0), n),
        "merged_contribution_rate": _rate(method_counts.get("merged", 0), n),
        "llm_fallback_rate_upper_bound": _rate(llm_or_merged, n),
        "evidence_field_completeness_pct": evidence_completeness,
        "confidence_distribution": _confidence_distribution([r.enrichment.get("confidence") for r in enriched]),
        "latency_ms": _latency_stats([r.stage_timings_ms.get("enrich") for r in attempted]),
    }


# ---------------------------------------------------------------------------
# Company Intelligence metrics
# ---------------------------------------------------------------------------


def compute_company_intelligence_metrics(results: List[SiteResult]) -> Dict[str, Any]:
    ci_results = [r for r in results if r.company_intelligence is not None]
    n = len(ci_results)

    source_counts: Dict[str, int] = {}
    for r in ci_results:
        source_counts[r.company_intelligence.get("source", "unknown")] = (
            source_counts.get(r.company_intelligence.get("source", "unknown"), 0) + 1
        )

    explainable = sum(
        1 for r in ci_results if (r.company_intelligence.get("explanation") or {}).get("evidence")
    )

    # Hallucination invariant, same pattern as scripts/test_pipeline_full.py's
    # own `hallucination_check`: the deterministic/heuristic path
    # (company_intelligence_agent.py::_analyze_heuristically) always
    # returns [] for pain_points/growth_indicators by construction -- any
    # non-empty value here means that invariant broke.
    heuristic = [r for r in ci_results if r.company_intelligence.get("source") == "heuristic"]
    heuristic_violations = [
        f"{r.query_id}:{r.website}" for r in heuristic
        if r.company_intelligence.get("pain_points") or r.company_intelligence.get("growth_indicators")
    ]

    grounding_scores = [r.grounded_technology_precision for r in ci_results if r.grounded_technology_precision is not None]

    return {
        "sites_analyzed": n,
        "source_distribution_pct": {k: _pct(v, n) for k, v in source_counts.items()},
        "heuristic_usage_rate": _rate(source_counts.get("heuristic", 0), n),
        "llm_usage_rate": _rate(source_counts.get("llm", 0), n),
        "explainability_coverage": _rate(explainable, n),
        "grounded_technology_precision": _avg(grounding_scores),
        "hallucination_check": {
            "heuristic_results_checked": len(heuristic),
            "invariant_violations": heuristic_violations,
            "status": "PASS" if not heuristic_violations else "FAIL",
        },
        "icp_alignment_distribution": _confidence_distribution([r.company_intelligence.get("icp_alignment_score") for r in ci_results]),
        "latency_ms": _latency_stats([r.stage_timings_ms.get("company_intelligence") for r in ci_results]),
    }


# ---------------------------------------------------------------------------
# Lead Scoring metrics
# ---------------------------------------------------------------------------


def compute_lead_scoring_metrics(results: List[SiteResult]) -> Dict[str, Any]:
    scored = [r for r in results if r.score is not None]
    n = len(scored)
    scores = [r.score.get("total_score") for r in scored]
    labels = [r.score.get("qualification_label") for r in scored]

    criterion_contribution: Dict[str, List[float]] = {}
    for r in scored:
        for name, val in (r.score.get("criteria_scores") or {}).items():
            criterion_contribution.setdefault(name, []).append(val)

    return {
        "leads_scored": n,
        "score_distribution": _confidence_distribution(scores),
        "qualification_distribution_pct": {
            label: _pct(labels.count(label), n) for label in sorted(set(labels))
        },
        "criterion_avg_contribution": {k: _avg(v) for k, v in criterion_contribution.items()},
        # ScoreResult (core.domain.services.scoring.ScoreResult) carries no
        # per-criterion reason/evidence field -- a known, pre-existing gap
        # already flagged in scripts/test_pipeline_full.py, not something
        # this framework can measure without changing production code
        # (out of scope -- see "no redesign" instruction).
        "explainability_coverage": 0.0,
        "latency_ms": _latency_stats([r.stage_timings_ms.get("scoring") for r in scored]),
    }


# ---------------------------------------------------------------------------
# Pipeline (end-to-end) metrics
# ---------------------------------------------------------------------------


def compute_pipeline_metrics(results: List[SiteResult]) -> Dict[str, Any]:
    n = len(results)
    success = [r for r in results if r.final_status == "SUCCESS"]
    partial = [r for r in results if r.final_status == "PARTIAL_SUCCESS"]
    failed = [r for r in results if r.final_status == "FAILED"]

    stage_completion = {}
    stage_reached = {
        "scrape": [r for r in results if r.scrape is not None and r.scrape.get("success")],
        "normalize": [r for r in results if r.normalized is not None],
        "enrich": [r for r in results if r.enrichment is not None],
        "company_intelligence": [r for r in results if r.company_intelligence is not None],
        "scoring": [r for r in results if r.score is not None],
    }
    for stage, reached in stage_reached.items():
        stage_completion[stage] = _rate(len(reached), n)

    per_stage_latency = {
        stage: _latency_stats([r.stage_timings_ms.get(stage) for r in results if r.stage_timings_ms.get(stage) is not None])
        for stage in PIPELINE_STAGES
    }

    failures_by_stage = {
        stage: [f"{r.query_id}:{r.website}" for r in results if any(e.get("stage") == stage for e in r.errors)]
        for stage in PIPELINE_STAGES
    }

    end_to_end_latency = [sum(r.stage_timings_ms.values()) for r in results if r.stage_timings_ms]

    # Confidence propagation: each stage's own already-computed confidence
    # value, averaged, laid out side by side -- lets a reviewer see
    # whether confidence is preserved or degrades moving downstream.
    # Nothing here is a new signal; every value is read straight off the
    # per-stage output already captured on SiteResult.
    confidence_propagation = {
        "scrape": _avg([r.scrape.get("confidence") for r in results if r.scrape]),
        "normalize": _avg([
            statistics.fmean(v) if (v := list((r.normalized.get("field_confidence") or {}).values())) else None
            for r in results if r.normalized
        ]),
        "enrich": _avg([r.enrichment.get("confidence") for r in results if r.enrichment]),
        "company_intelligence": _avg([r.company_intelligence.get("icp_alignment_score") for r in results if r.company_intelligence]),
    }

    # Evidence preservation: of the sites where the Normalizer found at
    # least one structured field, what fraction still carry non-empty
    # explanation evidence out of Company Intelligence -- i.e. did the
    # evidence actually survive to the final analysis stage, rather than
    # being silently dropped somewhere in enrich/CI. Both sides are fields
    # already captured on SiteResult; nothing is re-derived from raw text.
    had_normalizer_evidence = [
        r for r in results
        if r.normalized and any(r.normalized.get(f) for f in _NORMALIZER_FIELDS)
    ]
    preserved_to_ci = [
        r for r in had_normalizer_evidence
        if r.company_intelligence and (r.company_intelligence.get("explanation") or {}).get("evidence")
    ]

    return {
        "sites_processed": n,
        "end_to_end_success_rate": _rate(len(success), n),
        "partial_success_rate": _rate(len(partial), n),
        "failure_rate": _rate(len(failed), n),
        "stage_completion_rate": stage_completion,
        "per_stage_latency_ms": per_stage_latency,
        "end_to_end_latency_ms": _latency_stats(end_to_end_latency),
        "failures_by_stage": failures_by_stage,
        "confidence_propagation": confidence_propagation,
        "evidence_preservation_rate": _rate(len(preserved_to_ci), len(had_normalizer_evidence)),
    }


# ---------------------------------------------------------------------------
# Combined report-level metrics (Discovery + downstream, merged)
# ---------------------------------------------------------------------------


def build_combined_metrics(
    discovery_aggregate: Dict[str, Any],
    results: List[SiteResult],
) -> Dict[str, Any]:
    """Merges the untouched `discovery_eval.metrics.aggregate_metrics()`
    output with this module's own per-stage aggregates into the single
    dict `generate_report.py` serializes as `pipeline_metrics.json`.
    Adds exactly one derived Discovery metric that discovery_eval itself
    doesn't compute (`official_domain_precision`), built purely from
    fields discovery_aggregate already contains -- no new Discovery
    computation, just a ratio of two existing counts."""
    total_validated = discovery_aggregate.get("total_validated", 0)
    non_precise = (
        discovery_aggregate.get("directory_marketplace_selected_count", 0)
        + discovery_aggregate.get("social_profile_selected_count", 0)
    )
    official_domain_precision = (
        round(1 - (non_precise / total_validated), 4) if total_validated else 0.0
    )

    return {
        "discovery": {
            **discovery_aggregate,
            "official_domain_precision": official_domain_precision,
        },
        "scraper": compute_scraper_metrics(results),
        "normalizer": compute_normalizer_metrics(results),
        "enricher": compute_enricher_metrics(results),
        "company_intelligence": compute_company_intelligence_metrics(results),
        "lead_scoring": compute_lead_scoring_metrics(results),
        "pipeline": compute_pipeline_metrics(results),
    }
