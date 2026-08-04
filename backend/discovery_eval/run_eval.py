#!/usr/bin/env python3
"""
Discovery Evaluation Framework — Runner.

    python -m discovery_eval.run_eval
    python -m discovery_eval.run_eval --limit 10       # quick smoke run
    python -m discovery_eval.run_eval --concurrency 5  # override query concurrency

Executes the production Discovery pipeline as a black box, exactly up
through ranking, using ONLY DiscoveryService's own existing internal
stage methods (`_search`, `_resolve_and_validate`, `_detect_duplicates`)
plus the public `rank_businesses()` -- the identical sequence
`discover_and_create_leads()` itself runs before it ever creates a Lead.

This script NEVER calls `discover_and_create_leads()` and NEVER touches
`DiscoveryService._create_leads_and_run_pipelines` /
`DiscoveryService._record_metrics`, so it never:
  - creates a Lead or writes to the database (DiscoveryService is
    constructed with db=None; nothing this script calls ever reads
    self.db -- verified against discovery_service.py directly)
  - spends subscription/lead quota
  - triggers the AI enrichment pipeline, the scraper, or LeadPipeline

Must be run from the repository root (the directory containing
`application/`), so that `application.discovery.*` imports resolve.
Requires the same environment/API keys (e.g. SERPER_API_KEY) that
already power the production Discovery pipeline -- see the README for
what happens when a key is missing (graceful degradation, not a crash;
this script surfaces it as a low validation/resolution rate, not an
error).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path
from typing import Any, List, Tuple

# Allow `python discovery_eval/run_eval.py` from the repo root as well
# as `python -m discovery_eval.run_eval`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from discovery_eval.metrics import (  # noqa: E402
    BusinessRecord,
    QueryRecord,
    aggregate_metrics,
    build_query_record,
    extract_business_record,
)
from discovery_eval.queries import BENCHMARK_QUERIES, QueryCase  # noqa: E402
from discovery_eval.report import (  # noqa: E402
    write_business_csv,
    write_json,
    write_manual_review_csv,
    write_markdown_report,
    write_query_csv,
)
from discovery_eval.charts import generate_charts  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


def _import_discovery():
    """Deferred import so `--help` and unit tests (metrics/report/charts,
    which never need the real pipeline) work even before `application`
    is on the path / its dependencies are installed."""
    from application.discovery.discovery_service import DiscoveryService
    from application.discovery.exceptions import ProviderError, QueryParseError
    from application.discovery.ranking import rank_businesses

    return DiscoveryService, ProviderError, QueryParseError, rank_businesses


async def run_single_query(
    service, rank_businesses, QueryParseError, ProviderError, case: QueryCase
) -> Tuple[QueryRecord, List[BusinessRecord]]:
    t0 = time.perf_counter()
    try:
        parsed = service.parser.parse(case.query)
    except QueryParseError as e:
        record = build_query_record(case, error=str(e))
        return record, []

    try:
        ts0 = time.perf_counter()
        candidates = await service._search(parsed)
        ts1 = time.perf_counter()

        discovered, resolved_via_fallback_count = await service._resolve_and_validate(
            candidates, parsed.location
        )
        tr1 = time.perf_counter()

        duplicates_removed = service._detect_duplicates(discovered)
        td1 = time.perf_counter()

        eligible = [b for b in discovered if b.resolution.validated and not b.is_duplicate]
        rejected = [b for b in discovered if not b.resolution.validated or b.is_duplicate]

        ranked = rank_businesses(eligible, parsed.category, parsed.location)
        tk1 = time.perf_counter()

        selected = ranked[: parsed.limit]
        not_selected = ranked[parsed.limit :]
    except (ProviderError, Exception) as e:  # noqa: BLE001 -- one query must never abort the batch
        record = build_query_record(
            case,
            parsed=parsed,
            error=f"{type(e).__name__}: {e}",
            stage_times_ms={"total_ms": int((time.perf_counter() - t0) * 1000)},
        )
        return record, []

    stage_times_ms = {
        "search_ms": int((ts1 - ts0) * 1000),
        "resolve_ms": int((tr1 - ts1) * 1000),
        "dedup_ms": int((td1 - tr1) * 1000),
        "rank_ms": int((tk1 - td1) * 1000),
        "pipeline_ms": int((tk1 - ts0) * 1000),
        "total_ms": int((tk1 - t0) * 1000),
    }

    query_record = build_query_record(
        case,
        parsed=parsed,
        candidates=candidates,
        discovered=discovered,
        eligible=eligible,
        rejected=rejected,
        duplicates_removed=duplicates_removed,
        resolved_via_fallback_count=resolved_via_fallback_count,
        selected=selected,
        stage_times_ms=stage_times_ms,
    )

    business_records: List[BusinessRecord] = []
    selected_ids = {id(b) for b in selected}
    not_selected_ids = {id(b) for b in not_selected}
    for business in discovered:
        if business.is_duplicate:
            outcome = "duplicate"
        elif not business.resolution.validated:
            outcome = "no_website" if not business.resolution.website else "validation_failed"
        elif id(business) in selected_ids:
            outcome = "selected"
        elif id(business) in not_selected_ids:
            outcome = "not_selected"
        else:
            outcome = "not_selected"  # defensive fallback, should not occur
        business_records.append(extract_business_record(case, business, outcome))

    return query_record, business_records


async def run_benchmark(cases: List[QueryCase], concurrency: int) -> Tuple[List[QueryRecord], List[BusinessRecord]]:
    DiscoveryService, ProviderError, QueryParseError, rank_businesses = _import_discovery()
    service = DiscoveryService(db=None)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def bound_run(case: QueryCase):
        async with semaphore:
            return await run_single_query(service, rank_businesses, QueryParseError, ProviderError, case)

    query_records: List[QueryRecord] = []
    business_records: List[BusinessRecord] = []
    try:
        results = await asyncio.gather(*[bound_run(c) for c in cases], return_exceptions=True)
        for case, result in zip(cases, results):
            if isinstance(result, BaseException):
                traceback.print_exception(type(result), result, result.__traceback__)
                query_records.append(build_query_record(case, error=f"{type(result).__name__}: {result}"))
                continue
            qrec, brecs = result
            query_records.append(qrec)
            business_records.extend(brecs)
    finally:
        # DiscoveryService now owns a shared aiohttp session across this
        # whole run (H1 hardening: connection reuse instead of a fresh
        # TCP+TLS handshake per HTTP call). It's a no-op if the service
        # never made an HTTP call, or if it was given an externally-owned
        # session -- see DiscoveryService.aclose()'s own docstring. Always
        # runs, even if a query raised, so we never leak the connection.
        if hasattr(service, "aclose"):
            await service.aclose()
    return query_records, business_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Discovery evaluation benchmark.")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Only run the first N benchmark queries (quick smoke run).",
    )
    parser.add_argument(
        "--concurrency", type=int, default=3,
        help="Max concurrent queries in flight (default 3, matches "
             "DiscoveryService's own conservative default concurrency philosophy).",
    )
    args = parser.parse_args()

    cases = BENCHMARK_QUERIES[: args.limit] if args.limit else BENCHMARK_QUERIES
    print(f"Running Discovery evaluation on {len(cases)} queries (concurrency={args.concurrency})...")

    started = time.perf_counter()
    query_records, business_records = asyncio.run(run_benchmark(cases, args.concurrency))
    elapsed = time.perf_counter() - started

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    aggregate = aggregate_metrics(query_records, business_records)

    write_query_csv(query_records, OUTPUT_DIR / "query_results.csv")
    write_business_csv(business_records, OUTPUT_DIR / "business_results.csv")
    write_manual_review_csv(query_records, business_records, OUTPUT_DIR / "manual_review.csv")
    write_json(query_records, business_records, aggregate, OUTPUT_DIR / "evaluation.json")
    generate_charts(query_records, business_records, aggregate, OUTPUT_DIR)
    write_markdown_report(aggregate, query_records, business_records, OUTPUT_DIR)

    print(f"Done in {elapsed:.1f}s.")
    print(f"Discovery Health Score: {aggregate['discovery_health_score']} / 100")
    print(f"Validated: {aggregate['total_validated']} / {aggregate['total_businesses_found']} "
          f"({aggregate['validation_success_rate'] * 100:.1f}%)")
    print(f"Failed queries: {aggregate['failed_queries']} / {aggregate['total_queries']}")
    print(f"Outputs written to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()