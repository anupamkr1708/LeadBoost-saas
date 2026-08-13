"""
Full-Pipeline Evaluation — Report Generation.

Pure I/O / formatting: takes the records + metrics already computed by
`run_full_pipeline_benchmark.py` / `evaluation.metrics` and writes them to
disk in the five output artifacts the evaluation brief asks for:

    pipeline_metrics.json   -- the combined_metrics dict (Discovery +
                                every downstream stage + the pipeline
                                rollup), exactly as returned by
                                `evaluation.metrics.build_combined_metrics`
    pipeline_summary.md     -- human-readable report (Executive Summary,
                                per-stage sections, failure reasons,
                                missing fields, latencies, confidence
                                distributions, recommendations)
    pipeline_results.json   -- full per-site raw output, one entry per
                                (query, selected website) pair
    stage_metrics.csv       -- one row per pipeline stage: how many sites
                                reached it, its success/coverage rate, and
                                its latency stats
    benchmark_summary.csv   -- one row per benchmark query: Discovery
                                outcome plus how many/which selected sites
                                were pushed through the full pipeline and
                                how they ended up

No metric is computed in this file -- every number written here already
exists on the objects passed in. Recommendations are gated on fixed,
already-measured thresholds only (see `_recommendations`), same
discipline as `discovery_eval/report.py::_recommendations` -- never an
invented suggestion, never an architectural change.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from discovery_eval.metrics import QueryRecord

from scripts.evaluation.metrics import PIPELINE_STAGES, SiteResult

STAGE_METRICS_CSV_FIELDS = ["stage", "n", "rate_pct", "avg_ms", "median_ms", "p95_ms"]

BENCHMARK_SUMMARY_CSV_FIELDS = [
    "query_id", "query", "domain_tag", "difficulty_tag", "discovery_error",
    "businesses_found", "validated_businesses", "sites_pushed_to_pipeline",
    "sites_success", "sites_partial_success", "sites_failed",
    "avg_score", "qualification_labels",
]


# ---------------------------------------------------------------------------
# pipeline_results.json
# ---------------------------------------------------------------------------


def write_pipeline_results_json(results: List[SiteResult], path: Path) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sites_tested": len(results),
        "results": [r.to_dict() for r in results],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# pipeline_metrics.json
# ---------------------------------------------------------------------------


def write_pipeline_metrics_json(combined_metrics: Dict[str, Any], path: Path) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **combined_metrics,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# stage_metrics.csv
# ---------------------------------------------------------------------------

# What "rate" means per stage -- each is already computed by
# evaluation.metrics under a stage-appropriate name; this just picks the
# single headline number for the CSV's `rate_pct` column so every stage
# has one comparable row.
_STAGE_RATE_PATH = {
    "discovery": ("discovery", "validation_success_rate"),
    "scrape": ("scraper", "scrape_success_rate"),
    "normalize": ("normalizer", None),  # normalizer has no single success/failure signal; always runs if scrape did
    "enrich": ("enricher", "enrichment_coverage"),
    "company_intelligence": ("company_intelligence", None),  # always runs if enrich stage was reached
    "scoring": ("lead_scoring", None),  # always runs if company_intelligence produced output
}

_STAGE_LATENCY_PATH = {
    "discovery": ("discovery", None),  # discovery latency is per-query, not per-site; reported separately in the md summary
    "scrape": ("scraper", "latency_ms"),
    "normalize": ("normalizer", "latency_ms"),
    "enrich": ("enricher", "latency_ms"),
    "company_intelligence": ("company_intelligence", "latency_ms"),
    "scoring": ("lead_scoring", "latency_ms"),
}


def write_stage_metrics_csv(combined_metrics: Dict[str, Any], results: List[SiteResult], path: Path) -> None:
    pipeline = combined_metrics.get("pipeline", {})
    stage_completion = pipeline.get("stage_completion_rate", {})
    per_stage_latency = pipeline.get("per_stage_latency_ms", {})

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=STAGE_METRICS_CSV_FIELDS)
        writer.writeheader()
        for stage in PIPELINE_STAGES:
            latency = per_stage_latency.get(stage, {"n": 0, "avg_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0})
            writer.writerow({
                "stage": stage,
                "n": latency.get("n", 0),
                "rate_pct": round(stage_completion.get(stage, 0.0) * 100, 1),
                "avg_ms": latency.get("avg_ms", 0.0),
                "median_ms": latency.get("median_ms", 0.0),
                "p95_ms": latency.get("p95_ms", 0.0),
            })


# ---------------------------------------------------------------------------
# benchmark_summary.csv
# ---------------------------------------------------------------------------


def write_benchmark_summary_csv(
    query_records: List[QueryRecord], results: List[SiteResult], path: Path
) -> None:
    by_query: Dict[str, List[SiteResult]] = {}
    for r in results:
        by_query.setdefault(r.query_id, []).append(r)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BENCHMARK_SUMMARY_CSV_FIELDS)
        writer.writeheader()
        for q in query_records:
            sites = by_query.get(q.id, [])
            scores = [s.score.get("total_score") for s in sites if s.score]
            labels = [s.score.get("qualification_label") for s in sites if s.score]
            writer.writerow({
                "query_id": q.id,
                "query": q.query,
                "domain_tag": q.domain_tag,
                "difficulty_tag": q.difficulty_tag,
                "discovery_error": q.error or "",
                "businesses_found": q.businesses_found,
                "validated_businesses": q.validated_businesses,
                "sites_pushed_to_pipeline": len(sites),
                "sites_success": sum(1 for s in sites if s.final_status == "SUCCESS"),
                "sites_partial_success": sum(1 for s in sites if s.final_status == "PARTIAL_SUCCESS"),
                "sites_failed": sum(1 for s in sites if s.final_status == "FAILED"),
                "avg_score": round(sum(scores) / len(scores), 1) if scores else "",
                "qualification_labels": "; ".join(labels),
            })


# ---------------------------------------------------------------------------
# pipeline_summary.md
# ---------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _top_missing_fields(combined_metrics: Dict[str, Any], n: int = 10) -> List[tuple]:
    """Ranks Normalizer field-completeness entries ascending (most-missing
    first) -- every number here is `100 - field_completeness_pct[field]`,
    read straight from the already-computed metric, nothing recomputed."""
    completeness = combined_metrics.get("normalizer", {}).get("field_completeness_pct", {})
    missing = [(field, round(100 - pct, 1)) for field, pct in completeness.items()]
    return sorted(missing, key=lambda kv: kv[1], reverse=True)[:n]


def _recommendations(combined_metrics: Dict[str, Any]) -> List[str]:
    """Every line is gated on a specific metric in this run crossing a
    fixed, documented threshold -- no line is added speculatively, and no
    architectural change is ever suggested (same discipline as
    discovery_eval/report.py::_recommendations)."""
    lines: List[str] = []
    disco = combined_metrics.get("discovery", {})
    scraper = combined_metrics.get("scraper", {})
    enricher = combined_metrics.get("enricher", {})
    ci = combined_metrics.get("company_intelligence", {})
    pipeline = combined_metrics.get("pipeline", {})

    # Every rate-based check below is also gated on a minimum sample size
    # for the stage it reads from -- otherwise "0 sites attempted" reads
    # as a 0.0% success rate and would fire every threshold below
    # spuriously. `sites_processed`/`sites_attempted`/etc. are exactly
    # the same denominators the rate itself was computed from.
    if disco.get("total_businesses_found", 0) > 0 and disco.get("validation_failure_rate", 0) > 0.25:
        lines.append(
            f"Discovery validation failure rate is {_fmt_pct(disco['validation_failure_rate'])} "
            "(>25%) -- see the Discovery evaluation's own `evaluation_report.md` "
            "(`discovery_eval/outputs/`) for the most common validation_reason values."
        )
    if scraper.get("sites_attempted", 0) > 0 and scraper.get("blocked_rate", 0) > 0.15:
        lines.append(
            f"Scraper blocked rate is {_fmt_pct(scraper['blocked_rate'])} (>15%) across sites "
            "attempted this run -- check `pipeline_results.json` for which sites report "
            "`blocked_detected: true` and whether they share a common anti-bot vendor."
        )
    if scraper.get("sites_attempted", 0) > 0 and scraper.get("scrape_success_rate", 1.0) < 0.7:
        lines.append(
            f"Scraper success rate is {_fmt_pct(scraper['scrape_success_rate'])} (<70%) -- "
            "review `stage_metrics.csv`'s `scrape` row and cross-check against the "
            "`tiers_attempted` field in `pipeline_results.json` for sites that never escalate "
            "past the first tier."
        )
    if enricher.get("sites_eligible", 0) > 0 and enricher.get("llm_fallback_rate_upper_bound", 0) > 0.5:
        lines.append(
            f"Enrichment's LLM-or-merged rate is {_fmt_pct(enricher['llm_fallback_rate_upper_bound'])} "
            "(>50%) -- the deterministic tier alone is insufficient for most sites this run; "
            "see `evidence_field_completeness_pct` for which fields the deterministic tier is "
            "missing most often."
        )
    if ci.get("hallucination_check", {}).get("status") == "FAIL":
        lines.append(
            "Company Intelligence hallucination check FAILED: one or more heuristic-sourced "
            "results carried non-empty pain_points/growth_indicators, which the deterministic "
            "path should never produce by construction. See `hallucination_check.invariant_violations` "
            "in `pipeline_metrics.json`."
        )
    if pipeline.get("sites_processed", 0) > 0 and pipeline.get("evidence_preservation_rate", 1.0) < 0.7:
        lines.append(
            f"Evidence preservation rate is {_fmt_pct(pipeline['evidence_preservation_rate'])} "
            "(<70%) -- of sites where the Normalizer found structured evidence, fewer than 70% "
            "still carried non-empty explanation evidence out of Company Intelligence. Check "
            "whether `enriched_data`/`scraped_data` are both reaching "
            "`CompanyIntelligenceAgent._gather_evidence` as expected."
        )
    if pipeline.get("sites_processed", 0) > 0 and pipeline.get("failure_rate", 0) > 0.2:
        lines.append(
            f"End-to-end pipeline failure rate is {_fmt_pct(pipeline['failure_rate'])} (>20%) -- "
            "see `pipeline.failures_by_stage` in `pipeline_metrics.json` for which stage most "
            "sites fail at."
        )
    if pipeline.get("sites_processed", 0) == 0:
        lines.append(
            "No sites were pushed through the full pipeline this run (Discovery selected zero "
            "validated businesses across every benchmark query) -- every downstream metric above "
            "is vacuous (n=0), not a signal about Scraper/Normalizer/Enricher/Company "
            "Intelligence/Lead Scoring. Check the Discovery Metrics section and "
            "`discovery_eval/outputs/evaluation_report.md` first."
        )
    return lines


def write_pipeline_summary_md(
    combined_metrics: Dict[str, Any],
    query_records: List[QueryRecord],
    results: List[SiteResult],
    output_dir: Path,
) -> None:
    disco = combined_metrics.get("discovery", {})
    scraper = combined_metrics.get("scraper", {})
    normalizer = combined_metrics.get("normalizer", {})
    enricher = combined_metrics.get("enricher", {})
    ci = combined_metrics.get("company_intelligence", {})
    scoring = combined_metrics.get("lead_scoring", {})
    pipeline = combined_metrics.get("pipeline", {})

    lines: List[str] = []
    lines.append("# Full-Pipeline Benchmark Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append(
        "This report evaluates the production pipeline end to end: "
        "Discovery -> Scraper -> Normalizer -> Enricher -> Company Intelligence -> "
        "Lead Scoring, executed through each stage's own existing, unmodified "
        "production code (no shortcuts, no mocking, no re-implemented logic). "
        "The benchmark queries are the exact ~100-query Discovery benchmark "
        "dataset (`discovery_eval/queries.py`) -- reused as-is, not regenerated."
    )
    lines.append("")

    # -- Executive Summary --
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- Benchmark queries: {len(query_records)}")
    lines.append(f"- Sites pushed through the full pipeline: {pipeline.get('sites_processed', 0)}")
    lines.append(f"- End-to-end success rate: {_fmt_pct(pipeline.get('end_to_end_success_rate', 0.0))}")
    lines.append(f"- Partial success rate: {_fmt_pct(pipeline.get('partial_success_rate', 0.0))}")
    lines.append(f"- Failure rate: {_fmt_pct(pipeline.get('failure_rate', 0.0))}")
    lines.append(f"- Discovery validation success rate: {_fmt_pct(disco.get('validation_success_rate', 0.0))}")
    lines.append(f"- Discovery Health Score: {disco.get('discovery_health_score', 'n/a')} / 100")
    lines.append("")

    # -- Discovery --
    lines.append("## Discovery Metrics")
    lines.append("")
    lines.append(f"- Website resolution success rate: {_fmt_pct(disco.get('website_resolution_success_rate', 0.0))}")
    lines.append(f"- Validation success rate: {_fmt_pct(disco.get('validation_success_rate', 0.0))}")
    lines.append(f"- Duplicate rate: {_fmt_pct(disco.get('duplicate_rate', 0.0))}")
    lines.append(f"- Official domain precision: {_fmt_pct(disco.get('official_domain_precision', 0.0))}")
    lines.append(f"- Provider agreement rate: {_fmt_pct(disco.get('provider_agreement_rate', 0.0))}")
    lines.append(f"- Query parse success rate: {_fmt_pct(disco.get('parse_success_rate', 0.0))}")
    lines.append(f"- Average query latency: {disco.get('avg_latency_ms', 0.0)} ms "
                  f"(p95 {disco.get('p95_latency_ms', 0.0)} ms)")
    lines.append("")
    lines.append("Full Discovery-stage breakdown (per-query CSV/JSON/charts) is in "
                  "`discovery_eval/outputs/` -- this section is a read-only summary of that "
                  "same, unmodified `aggregate_metrics()` output.")
    lines.append("")

    # -- Scraper --
    lines.append("## Scraper Metrics")
    lines.append("")
    lines.append(f"- Sites attempted: {scraper.get('sites_attempted', 0)}")
    lines.append(f"- Scrape success rate: {_fmt_pct(scraper.get('scrape_success_rate', 0.0))}")
    lines.append(f"- Blocked rate: {_fmt_pct(scraper.get('blocked_rate', 0.0))}")
    lines.append(f"- Multi-page enrichment rate: {_fmt_pct(scraper.get('multi_page_enrichment_rate', 0.0))}")
    lines.append(f"- Average pages scraped: {scraper.get('avg_pages_scraped')}")
    lines.append(f"- Structured extraction coverage: {_fmt_pct(scraper.get('structured_extraction_coverage', 0.0))}")
    lines.append(f"- Fallback tier usage: {json.dumps(scraper.get('fallback_tier_usage_counts', {}))}")
    lines.append("")

    # -- Normalizer --
    lines.append("## Normalizer Metrics")
    lines.append("")
    lines.append(f"- Sites normalized: {normalizer.get('sites_normalized', 0)}")
    lines.append(f"- Organization type coverage: {_fmt_pct(normalizer.get('organization_type_coverage', 0.0))}")
    lines.append(f"- Address coverage: {_fmt_pct(normalizer.get('address_coverage', 0.0))}")
    lines.append(f"- Social coverage: {_fmt_pct(normalizer.get('social_coverage', 0.0))}")
    lines.append(f"- Technology coverage: {_fmt_pct(normalizer.get('technology_coverage', 0.0))}")
    lines.append("")
    lines.append("Field completeness: " + json.dumps(normalizer.get("field_completeness_pct", {})))
    lines.append("")

    # -- Enricher --
    lines.append("## Enricher Metrics")
    lines.append("")
    lines.append(f"- Enrichment coverage: {_fmt_pct(enricher.get('enrichment_coverage', 0.0))}")
    lines.append(f"- Deterministic (heuristic) contribution: {_fmt_pct(enricher.get('deterministic_contribution_rate', 0.0))}")
    lines.append(f"- External API contribution: {_fmt_pct(enricher.get('external_api_contribution_rate', 0.0))}")
    lines.append(f"- LLM contribution: {_fmt_pct(enricher.get('llm_contribution_rate', 0.0))}")
    lines.append(f"- Merged contribution: {_fmt_pct(enricher.get('merged_contribution_rate', 0.0))}")
    lines.append(f"- LLM-or-merged rate (upper bound, see caveat in `evaluation/metrics.py`): "
                  f"{_fmt_pct(enricher.get('llm_fallback_rate_upper_bound', 0.0))}")
    lines.append("")

    # -- Company Intelligence --
    lines.append("## Company Intelligence Metrics")
    lines.append("")
    lines.append(f"- Heuristic usage rate: {_fmt_pct(ci.get('heuristic_usage_rate', 0.0))}")
    lines.append(f"- LLM usage rate: {_fmt_pct(ci.get('llm_usage_rate', 0.0))}")
    lines.append(f"- Explainability coverage: {_fmt_pct(ci.get('explainability_coverage', 0.0))}")
    gtp = ci.get("grounded_technology_precision")
    lines.append(f"- Grounded technology precision: {gtp if gtp is not None else 'n/a'}")
    hc = ci.get("hallucination_check", {})
    lines.append(f"- Hallucination check: **{hc.get('status', 'n/a')}** "
                  f"({hc.get('heuristic_results_checked', 0)} heuristic results checked)")
    if hc.get("invariant_violations"):
        lines.append(f"  - Violations: {hc['invariant_violations']}")
    lines.append("")

    # -- Lead Scoring --
    lines.append("## Lead Scoring Metrics")
    lines.append("")
    dist = scoring.get("score_distribution", {})
    lines.append(f"- Leads scored: {scoring.get('leads_scored', 0)}")
    lines.append(f"- Score distribution: avg={dist.get('avg')} median={dist.get('median')} "
                  f"min={dist.get('min')} max={dist.get('max')}")
    lines.append(f"- Qualification distribution: {json.dumps(scoring.get('qualification_distribution_pct', {}))}")
    lines.append(f"- Criterion average contribution: {json.dumps(scoring.get('criterion_avg_contribution', {}))}")
    lines.append("")

    # -- Pipeline --
    lines.append("## Pipeline Metrics")
    lines.append("")
    lines.append(f"- Stage completion rate: {json.dumps(pipeline.get('stage_completion_rate', {}))}")
    lines.append(f"- Evidence preservation rate: {_fmt_pct(pipeline.get('evidence_preservation_rate', 0.0))}")
    lines.append(f"- Confidence propagation (avg per stage): {json.dumps(pipeline.get('confidence_propagation', {}))}")
    lines.append("")

    # -- Top Failure Reasons --
    lines.append("## Top Failure Reasons")
    lines.append("")
    failures_by_stage = pipeline.get("failures_by_stage", {})
    any_failures = any(failures_by_stage.values())
    if any_failures:
        lines.append("| Stage | Failure count |")
        lines.append("|---|---|")
        for stage, ids in failures_by_stage.items():
            if ids:
                lines.append(f"| {stage} | {len(ids)} |")
    else:
        lines.append("No stage failures recorded in this run.")
    lines.append("")

    # -- Most Common Missing Fields --
    lines.append("## Most Common Missing Fields")
    lines.append("")
    missing = _top_missing_fields(combined_metrics)
    if missing and any(pct > 0 for _, pct in missing):
        lines.append("| Field | Missing (%) |")
        lines.append("|---|---|")
        for field_name, pct in missing:
            if pct > 0:
                lines.append(f"| {field_name} | {pct} |")
    else:
        lines.append("No normalized sites recorded in this run.")
    lines.append("")

    # -- Average Latencies --
    lines.append("## Average Latencies")
    lines.append("")
    lines.append("| Stage | Avg (ms) | Median (ms) | P95 (ms) | n |")
    lines.append("|---|---|---|---|---|")
    for stage, stats in pipeline.get("per_stage_latency_ms", {}).items():
        lines.append(f"| {stage} | {stats.get('avg_ms', 0.0)} | {stats.get('median_ms', 0.0)} | "
                      f"{stats.get('p95_ms', 0.0)} | {stats.get('n', 0)} |")
    e2e = pipeline.get("end_to_end_latency_ms", {})
    lines.append(f"| end_to_end | {e2e.get('avg_ms', 0.0)} | {e2e.get('median_ms', 0.0)} | "
                  f"{e2e.get('p95_ms', 0.0)} | {e2e.get('n', 0)} |")
    lines.append("")

    # -- Confidence Distributions --
    lines.append("## Confidence Distributions")
    lines.append("")
    lines.append("| Stage | n | avg | median | min | max |")
    lines.append("|---|---|---|---|---|---|")
    for stage_name, dist in (
        ("scrape", scraper.get("confidence_distribution", {})),
        ("normalize (field_confidence)", normalizer.get("confidence_distribution", {})),
        ("enrich", enricher.get("confidence_distribution", {})),
        ("company_intelligence (icp_alignment)", ci.get("icp_alignment_distribution", {})),
        ("lead_scoring (total_score)", scoring.get("score_distribution", {})),
    ):
        lines.append(f"| {stage_name} | {dist.get('n', 0)} | {dist.get('avg')} | {dist.get('median')} | "
                      f"{dist.get('min')} | {dist.get('max')} |")
    lines.append("")

    # -- Recommendations --
    lines.append("## Recommendations")
    lines.append("")
    lines.append(
        "Every recommendation below is triggered by a specific metric in this run crossing a "
        "fixed threshold (see `scripts/evaluation/generate_report.py::_recommendations`). None "
        "of these propose an architectural change -- this framework measures the pipeline, it "
        "does not redesign it."
    )
    lines.append("")
    recs = _recommendations(combined_metrics)
    if recs:
        for rec in recs:
            lines.append(f"- {rec}")
    else:
        lines.append("No thresholds were crossed in this run.")
    lines.append("")

    (output_dir / "pipeline_summary.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Convenience: write everything in one call
# ---------------------------------------------------------------------------


def write_all(
    combined_metrics: Dict[str, Any],
    query_records: List[QueryRecord],
    results: List[SiteResult],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_pipeline_metrics_json(combined_metrics, output_dir / "pipeline_metrics.json")
    write_pipeline_results_json(results, output_dir / "pipeline_results.json")
    write_stage_metrics_csv(combined_metrics, results, output_dir / "stage_metrics.csv")
    write_benchmark_summary_csv(query_records, results, output_dir / "benchmark_summary.csv")
    write_pipeline_summary_md(combined_metrics, query_records, results, output_dir)
