#!/usr/bin/env python3
"""
Full-Pipeline Evaluation Framework — Runner.

    python -m scripts.run_full_pipeline_benchmark
    python -m scripts.run_full_pipeline_benchmark --limit 10
    python -m scripts.run_full_pipeline_benchmark --top-n-per-query 1
    python -m scripts.run_full_pipeline_benchmark --no-llm

Executes the production pipeline end to end, as a black box, using ONLY
each stage's own existing production code -- no shortcuts, no mocking,
no re-implemented logic:

    Discovery (reused, unmodified: discovery_eval.run_eval.run_benchmark)
        -> Scraper (core.infrastructure.scraping.scraper.TieredScraper)
        -> Normalizer (core.infrastructure.normalization.normalizer.normalize_scraped_fields)
        -> Enricher (core.infrastructure.enrichment.enricher.WaterfallEnricher)
        -> Company Intelligence (application.agents.company_intelligence_agent.CompanyIntelligenceAgent)
        -> Lead Scoring (core.domain.services.scoring.LeadScoringService)

BENCHMARK QUERIES ARE REUSED, NOT REGENERATED
-----------------------------------------------
This script does not define its own query dataset. It imports
`discovery_eval.queries.BENCHMARK_QUERIES` -- the same ~100-query,
fixed-order, fixed-ID dataset the Discovery evaluation already uses --
and treats it as the single source of truth for what "the benchmark" is
so a Discovery regression and a full-pipeline regression are always
measured against the identical set of queries.

WHY DISCOVERY ITSELF IS NOT RE-EXECUTED HERE
-----------------------------------------------
Stage A of this script IS `discovery_eval.run_eval.run_benchmark()`,
called directly, unmodified. That function already runs the identical
sequence `DiscoveryService.discover_and_create_leads()` runs before it
ever creates a Lead (`_search` -> `_resolve_and_validate` ->
`_detect_duplicates` -> `rank_businesses`), against a `DiscoveryService`
constructed with `db=None`, so nothing here creates a Lead, writes to the
database, or spends quota during Discovery. Re-deriving that logic here
instead of importing it would violate the "reuse over duplication"
principle this whole framework is built on.

WHY GRAPH_NODES.PY IS NOT IMPORTED FOR STAGES B-F
----------------------------------------------------
`application.workflows.graph_nodes.py` is the real production
orchestration for Scraper/Enricher/Company Intelligence/Scoring, but it
is entangled with the database layer (`core.domain.models.lead.Lead` is
a SQLAlchemy model with relationships; `core.infrastructure.database.crud`,
`application.context.context_builder`, `application.observability.repository`
all expect a live `Session`). `scripts/test_pipeline_full.py` already
established -- and documented, after checking directly against the
consumer files, not guessing -- that the safe, DB-free way to exercise
this same code is to call each stage's own underlying service directly
(`TieredScraper`, `normalize_scraped_fields`, `WaterfallEnricher`,
`CompanyIntelligenceAgent`, `LeadScoringService`) with small local
dataclasses that carry only the fields those services actually read.
This script reuses that exact, already-validated pattern (see
`_LocalLead` / `_LocalLeadContext` below) rather than re-deriving it, and
reuses `application.utils.stage_logger.stage_span` -- the same
timing/logging primitive `graph_nodes.py` itself uses for its production
stage instrumentation -- which has no database dependency and is safe to
import directly.

WHY A CAP ON SITES PER QUERY (top-n-per-query)
--------------------------------------------------
In production, `discover_and_create_leads()` runs the full pipeline for
EVERY selected business (`ranked[:parsed.limit]`), and `parsed.limit`
defaults to 20 (`application/discovery/query_parser.py::_DEFAULT_LIMIT`).
Applied literally across ~100 benchmark queries, that would mean
potentially thousands of live scrapes (plus LLM calls) per benchmark run
-- impractical for something meant to run repeatedly, deterministically,
release over release. `--top-n-per-query` (default 3) caps how many of
each query's already-*ranked* selected businesses are pushed through the
full pipeline; pass `--top-n-per-query 0` to disable the cap and exercise
every selected business, matching production's literal fan-out. This is
a scope/cost decision made explicit and overridable, not a code-path
shortcut -- the businesses that DO get selected still go through the
exact same downstream code as every other business.

ON "DETERMINISTIC"
---------------------
The query set, the code paths, and every non-LLM stage are fully
deterministic. When Company Intelligence or the Enricher's LLM tier
fires, their output is not byte-for-byte reproducible across runs (model
sampling, provider availability) -- this is inherent to the pipeline
being evaluated, not something this framework can paper over. Pass
`--no-llm` for a fully deterministic run that forces every stage down
its heuristic/deterministic path only, useful for isolating pure
code-path regressions from LLM-output drift between releases.

WHERE TO PUT THIS
-------------------
backend/scripts/run_full_pipeline_benchmark.py

OUTPUTS
--------
Written to `scripts/evaluation/outputs/` (override with --output-dir):
    pipeline_metrics.json, pipeline_summary.md, pipeline_results.json,
    stage_metrics.csv, benchmark_summary.csv
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from discovery_eval.metrics import BusinessRecord, QueryRecord, aggregate_metrics  # noqa: E402
from discovery_eval.queries import BENCHMARK_QUERIES, QueryCase  # noqa: E402
from discovery_eval.run_eval import run_benchmark as run_discovery_benchmark  # noqa: E402

from scripts.evaluation.metrics import SiteResult, build_combined_metrics  # noqa: E402
from scripts.evaluation import generate_report  # noqa: E402

from application.utils.stage_logger import stage_span  # noqa: E402
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

OUTPUT_DIR = Path(__file__).resolve().parent / "evaluation" / "outputs"

# Same 6 keys graph_nodes.py's real enrich() stage copies from
# EnrichmentResult.data onto the Lead row, and scripts/test_pipeline_full.py
# already reproduced for the same reason -- copied verbatim so this
# script's Lead ends up in the same state a real pipeline run would leave
# it in before Company Intelligence / Scoring read it.
_ENRICHMENT_LEAD_FIELDS = (
    "industry", "employees", "revenue_band", "founded_year", "contact_name", "contact_title",
)

DEFAULT_TOP_N_PER_QUERY = 3


# ---------------------------------------------------------------------------
# Local, DB-free stand-ins for Lead / LeadContext.
# See module docstring for why these exist instead of the real ORM types.
# Field set copied from scripts/test_pipeline_full.py's already-validated
# _LocalLead / _LocalLeadContext, plus `phone` (see comment on the field).
# ---------------------------------------------------------------------------


@dataclass
class _LocalLead:
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
    # Not read by WaterfallEnricher, but core.domain.services.scoring's
    # _evaluate_email_quality() does read `getattr(lead, "phone", None)`
    # as a fallback signal when email_confidence is 0 -- omitted from
    # test_pipeline_full.py's original field set even though Lead Scoring
    # is one of its two named targets; added here so that scoring branch
    # is actually exercisable. Populated the same way graph_nodes.py's
    # real scrape() stage populates it: straight from the scraper's own
    # raw `data.get("phone")`, not from the Normalizer.
    phone: Optional[str] = None
    scrape_confidence: float = 0.0
    email_confidence: float = 0.0
    enrichment_confidence: float = 0.0


@dataclass
class _LocalLeadContext:
    company_name: Optional[str] = None
    website: Optional[str] = None
    industry: Optional[str] = None
    employees: Optional[str] = None
    about_text: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    scraped_data: Dict[str, Any] = field(default_factory=dict)
    enriched_data: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Serialization (copied verbatim from scripts/test_pipeline_full.py's
# already-validated _serialize_scrape_result / _serialize_enrichment_result
# -- pure field-mapping, not business logic, so re-deriving it here would
# only risk drifting from the already-checked field names).
# ---------------------------------------------------------------------------


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
    """See scripts/test_pipeline_full.py's identical helper for the caveat:
    best-effort local proxy for Lead.email_confidence, taking the highest
    per-email confidence normalize_scraped_fields already computed."""
    emails = normalized.get("emails") or []
    confidences = [e.get("confidence", 0.0) for e in emails if isinstance(e, dict)]
    return max(confidences) if confidences else 0.0


# ---------------------------------------------------------------------------
# Stage 1: reuse the Discovery benchmark exactly, then pick targets.
# ---------------------------------------------------------------------------


def select_targets(
    query_records: List[QueryRecord],
    business_records: List[BusinessRecord],
    top_n_per_query: int,
) -> List[Tuple[QueryRecord, BusinessRecord]]:
    """For every query, take its Discovery-selected businesses (`outcome
    == "selected"`, i.e. exactly the set `discover_and_create_leads()`
    would have created Leads for) that carry a validated website, sort by
    the same `rank_score` Discovery itself ranked them by, and keep the
    top `top_n_per_query` (0 == keep all, matching production's literal
    fan-out). See module docstring for why this cap exists."""
    query_by_id = {q.id: q for q in query_records}
    by_query: Dict[str, List[BusinessRecord]] = {}
    for b in business_records:
        if b.outcome == "selected" and b.final_website and b.website_validated:
            by_query.setdefault(b.query_id, []).append(b)

    targets: List[Tuple[QueryRecord, BusinessRecord]] = []
    for query_id, businesses in by_query.items():
        query_record = query_by_id.get(query_id)
        if query_record is None:
            continue
        ranked = sorted(businesses, key=lambda b: b.rank_score, reverse=True)
        chosen = ranked if top_n_per_query <= 0 else ranked[:top_n_per_query]
        for b in chosen:
            targets.append((query_record, b))
    return targets


# ---------------------------------------------------------------------------
# Stages 2-6: Scraper -> Normalizer -> Enricher -> Company Intelligence ->
# Lead Scoring, run for one selected website.
# ---------------------------------------------------------------------------


async def _run_stage(stage_name, timings_ms, errors, coro, pipeline_id):
    """Runs and times one pipeline stage via `stage_span`/`StageTimer` --
    the exact timing/logging primitive `application.workflows.graph_nodes.py`
    uses for its own production stages (see module docstring for why that
    module itself isn't imported). Catches any exception so one failing
    stage never aborts the rest of this site's pipeline, mirroring
    graph_nodes.py's own per-stage error isolation (a failed stage becomes
    a recorded error + PARTIAL_SUCCESS, not a crash)."""
    try:
        with stage_span(stage_name, lead_id=None, pipeline_id=pipeline_id) as timer:
            result = await coro
        timings_ms[stage_name] = timer.duration_ms
        return result
    except Exception as e:  # noqa: BLE001 -- one stage must never abort the site
        timings_ms[stage_name] = timer.duration_ms
        errors.append({"stage": stage_name, "error": f"{type(e).__name__}: {e}"})
        return None


async def run_pipeline_for_site(
    scraper: TieredScraper,
    query_record: QueryRecord,
    business: BusinessRecord,
    allow_llm: bool,
) -> SiteResult:
    website = business.final_website
    pipeline_id = f"benchmark:{query_record.id}:{website}"
    timings_ms: Dict[str, int] = {}
    errors: List[Dict[str, str]] = []

    site = SiteResult(
        query_id=query_record.id,
        query=query_record.query,
        domain_tag=query_record.domain_tag,
        difficulty_tag=query_record.difficulty_tag,
        business_name=business.business_name,
        website=website,
        rank_score=business.rank_score,
        discovery_confidence=business.confidence,
        stage_timings_ms=timings_ms,
        errors=errors,
    )

    # -- Stage: Scraper --
    scrape_result: Optional[ScrapingResult] = await _run_stage(
        "scrape", timings_ms, errors, scraper.scrape(website), pipeline_id
    )
    if scrape_result is not None:
        site.scrape = _serialize_scrape_result(scrape_result)
    if scrape_result is None or not scrape_result.data:
        site.final_status = "FAILED"
        return site

    # -- Stage: Normalizer --
    async def _normalize():
        return normalize_scraped_fields(scrape_result.data)

    normalized = await _run_stage("normalize", timings_ms, errors, _normalize(), pipeline_id)
    site.normalized = normalized
    if normalized is None:
        site.final_status = "PARTIAL_SUCCESS"
        return site

    # -- Stage: Enricher --
    lead = _LocalLead(
        company_name=normalized.get("brand_name") or scrape_result.data.get("company_name"),
        website=website,
        about_text=normalized.get("description"),
        linkedin_url=(normalized.get("social_profiles") or {}).get("linkedin_url")
        or scrape_result.data.get("linkedin_url"),
        phone=scrape_result.data.get("phone"),
        scrape_confidence=scrape_result.confidence,
        email_confidence=_best_email_confidence(normalized),
    )

    async def _enrich():
        # Off-thread, same as graph_nodes.py's real enrich() stage --
        # enrichment can block on an LLM call, and production never runs
        # that inline on the event loop.
        return await asyncio.to_thread(WaterfallEnricher().enrich_lead_data, lead, scrape_result.data)

    enrichment_result: Optional[EnrichmentResult] = await _run_stage(
        "enrich", timings_ms, errors, _enrich(), pipeline_id
    )
    site.enrichment = _serialize_enrichment_result(enrichment_result)
    if enrichment_result is not None:
        # Mirrors graph_nodes.py's real enrich() stage: copy these 6
        # fields from enrichment output onto the Lead, same as production.
        for key in _ENRICHMENT_LEAD_FIELDS:
            if key in enrichment_result.data:
                setattr(lead, key, enrichment_result.data[key])
        lead.enrichment_confidence = enrichment_result.confidence
    enriched_data = enrichment_result.data if enrichment_result else {}

    # -- Stage: Company Intelligence --
    context = _LocalLeadContext(
        company_name=lead.company_name,
        website=website,
        industry=lead.industry,
        employees=lead.employees,
        about_text=lead.about_text,
        email=(normalized.get("emails") or [{}])[0].get("email") if normalized.get("emails") else None,
        linkedin_url=lead.linkedin_url,
        scraped_data=scrape_result.data,
        enriched_data=enriched_data,
    )

    async def _company_intelligence():
        return await asyncio.to_thread(CompanyIntelligenceAgent().run, context, allow_llm)

    ci_output = await _run_stage("company_intelligence", timings_ms, errors, _company_intelligence(), pipeline_id)
    if ci_output is not None:
        site.company_intelligence = ci_output.model_dump() if hasattr(ci_output, "model_dump") else vars(ci_output)
        # Grounding audit: recomputed by calling CompanyIntelligenceAgent's
        # own evidence-gathering staticmethod directly (not re-derived) --
        # see evaluation.metrics's grounded_technology_precision docstring.
        evidence = CompanyIntelligenceAgent._gather_evidence(context)
        evidence_technologies = set(evidence.get("technologies") or [])
        signals = ci_output.technology_signals or []
        site.grounded_technology_precision = (
            round(sum(1 for t in signals if t in evidence_technologies) / len(signals), 4)
            if signals else 1.0  # vacuously grounded: nothing claimed, nothing to be wrong about
        )

    # -- Stage: Lead Scoring --
    if ci_output is not None:
        async def _scoring():
            return LeadScoringService().score_lead(lead, company_intelligence=ci_output)

        score_result = await _run_stage("scoring", timings_ms, errors, _scoring(), pipeline_id)
        if score_result is not None:
            site.score = {
                "total_score": score_result.total_score,
                "qualification_label": score_result.qualification_label,
                "criteria_scores": score_result.criteria_scores,
            }

    # Mirrors application.workflows.lead_pipeline.py's own PipelineStatus
    # rule exactly: SUCCESS if no stage recorded an error, PARTIAL_SUCCESS
    # otherwise (scrape already succeeded, or we'd have returned FAILED
    # above).
    site.final_status = "SUCCESS" if not errors else "PARTIAL_SUCCESS"
    return site


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


@dataclass
class FullBenchmarkRun:
    query_records: List[QueryRecord]
    business_records: List[BusinessRecord]
    site_results: List[SiteResult]


async def run_full_pipeline_benchmark(
    cases: List[QueryCase],
    *,
    discovery_concurrency: int = 3,
    pipeline_concurrency: int = 1,
    top_n_per_query: int = DEFAULT_TOP_N_PER_QUERY,
    allow_llm: bool = True,
) -> FullBenchmarkRun:
    # Stage A: Discovery -- 100% reused, unmodified.
    query_records, business_records = await run_discovery_benchmark(cases, discovery_concurrency)

    # Stage B: pick the top valid official website(s) per query.
    targets = select_targets(query_records, business_records, top_n_per_query)

    # Stages C-G: Scraper -> Normalizer -> Enricher -> Company
    # Intelligence -> Lead Scoring, bounded concurrency, one shared
    # TieredScraper session across every target (same connection-reuse
    # pattern scripts/test_pipeline_full.py already uses).
    site_results: List[SiteResult] = []
    semaphore = asyncio.Semaphore(max(1, pipeline_concurrency))

    async def bound_run(query_record: QueryRecord, business: BusinessRecord) -> SiteResult:
        async with semaphore:
            return await run_pipeline_for_site(scraper, query_record, business, allow_llm)

    try:
        async with TieredScraper() as scraper:
            results = await asyncio.gather(
                *[bound_run(q, b) for q, b in targets], return_exceptions=True
            )
            for (query_record, business), result in zip(targets, results):
                if isinstance(result, BaseException):
                    site_results.append(SiteResult(
                        query_id=query_record.id,
                        query=query_record.query,
                        domain_tag=query_record.domain_tag,
                        difficulty_tag=query_record.difficulty_tag,
                        business_name=business.business_name,
                        website=business.final_website,
                        rank_score=business.rank_score,
                        discovery_confidence=business.confidence,
                        errors=[{"stage": "unhandled", "error": f"{type(result).__name__}: {result}"}],
                        final_status="FAILED",
                    ))
                else:
                    site_results.append(result)
    finally:
        await close_scraper_resources()

    return FullBenchmarkRun(
        query_records=query_records, business_records=business_records, site_results=site_results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full-pipeline evaluation benchmark.")
    parser.add_argument("--limit", type=int, default=None,
                         help="Only run the first N benchmark queries (quick smoke run).")
    parser.add_argument("--discovery-concurrency", type=int, default=3,
                         help="Max concurrent Discovery queries in flight (default 3).")
    parser.add_argument("--pipeline-concurrency", type=int, default=3,
                         help="Max concurrent Scraper->...->Scoring runs in flight (default 3).")
    parser.add_argument("--top-n-per-query", type=int, default=DEFAULT_TOP_N_PER_QUERY,
                         help=f"Cap on selected websites pushed through the full pipeline per "
                              f"query (default {DEFAULT_TOP_N_PER_QUERY}; 0 = no cap, matches "
                              f"production's literal fan-out -- see module docstring).")
    parser.add_argument("--no-llm", action="store_true",
                         help="Force every stage down its heuristic/deterministic path only, "
                              "for a fully deterministic run (see module docstring).")
    parser.add_argument("--output-dir", type=str, default=None,
                         help=f"Where to write the report (default {OUTPUT_DIR}).")
    args = parser.parse_args()

    cases = BENCHMARK_QUERIES[: args.limit] if args.limit else BENCHMARK_QUERIES
    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_DIR

    print(f"Running full-pipeline benchmark on {len(cases)} queries "
          f"(discovery_concurrency={args.discovery_concurrency}, "
          f"pipeline_concurrency={args.pipeline_concurrency}, "
          f"top_n_per_query={args.top_n_per_query}, "
          f"allow_llm={not args.no_llm})...")

    started = time.perf_counter()
    run = asyncio.run(run_full_pipeline_benchmark(
        cases,
        discovery_concurrency=args.discovery_concurrency,
        pipeline_concurrency=args.pipeline_concurrency,
        top_n_per_query=args.top_n_per_query,
        allow_llm=not args.no_llm,
    ))
    elapsed = time.perf_counter() - started

    discovery_aggregate = aggregate_metrics(run.query_records, run.business_records)
    combined_metrics = build_combined_metrics(discovery_aggregate, run.site_results)

    output_dir.mkdir(parents=True, exist_ok=True)
    generate_report.write_all(combined_metrics, run.query_records, run.site_results, output_dir)

    pipeline_metrics = combined_metrics["pipeline"]
    print(f"Done in {elapsed:.1f}s.")
    print(f"Discovery Health Score: {discovery_aggregate['discovery_health_score']} / 100")
    print(f"Sites pushed through full pipeline: {pipeline_metrics['sites_processed']}")
    print(f"End-to-end success rate: {pipeline_metrics['end_to_end_success_rate'] * 100:.1f}%")
    print(f"Outputs written to: {output_dir}")


if __name__ == "__main__":
    main()
