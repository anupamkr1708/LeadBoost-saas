#!/usr/bin/env python3
"""
Full-Pipeline Evaluation Framework — Sample Smoke Test.

    python -m scripts.test_pipeline_sample
    python -m scripts.test_pipeline_sample --queries 5
    python -m scripts.test_pipeline_sample --no-llm

A small, fast run for debugging the Discovery -> Scraper -> Normalizer ->
Enricher -> Company Intelligence -> Lead Scoring wiring before committing
to a full ~100-query benchmark run. This is a thin wrapper around
`scripts.run_full_pipeline_benchmark.run_full_pipeline_benchmark()` --
it does NOT define its own query list or re-implement any pipeline
logic (per "reuse over duplication"); it just calls the same runner with
a small slice of the same reused benchmark dataset
(`discovery_eval.queries.BENCHMARK_QUERIES`) and a tight per-query cap so
it finishes in well under a minute even with the LLM tier enabled.

Writes to `scripts/evaluation/outputs/sample/` (NOT the full-run output
directory) so running this never overwrites a full benchmark's results.

WHERE TO PUT THIS
-------------------
backend/scripts/test_pipeline_sample.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from discovery_eval.metrics import aggregate_metrics  # noqa: E402
from discovery_eval.queries import BENCHMARK_QUERIES  # noqa: E402

from scripts.run_full_pipeline_benchmark import run_full_pipeline_benchmark  # noqa: E402
from scripts.evaluation.metrics import build_combined_metrics  # noqa: E402
from scripts.evaluation import generate_report  # noqa: E402

SAMPLE_OUTPUT_DIR = Path(__file__).resolve().parent / "evaluation" / "outputs" / "sample"
DEFAULT_SAMPLE_SIZE = 5


def main() -> None:
    parser = argparse.ArgumentParser(description="Fast smoke test for the full-pipeline benchmark wiring.")
    parser.add_argument("--queries", type=int, default=DEFAULT_SAMPLE_SIZE,
                         help=f"How many benchmark queries to sample (default {DEFAULT_SAMPLE_SIZE}).")
    parser.add_argument("--no-llm", action="store_true",
                         help="Force every stage down its heuristic/deterministic path only.")
    args = parser.parse_args()

    cases = BENCHMARK_QUERIES[: args.queries]
    print(f"[sample] Running full-pipeline smoke test on {len(cases)} queries "
          f"(top 1 selected website each, allow_llm={not args.no_llm})...")

    started = time.perf_counter()
    run = asyncio.run(run_full_pipeline_benchmark(
        cases,
        discovery_concurrency=min(3, len(cases)) or 1,
        pipeline_concurrency=min(3, len(cases)) or 1,
        top_n_per_query=1,
        allow_llm=not args.no_llm,
    ))
    elapsed = time.perf_counter() - started

    discovery_aggregate = aggregate_metrics(run.query_records, run.business_records)
    combined_metrics = build_combined_metrics(discovery_aggregate, run.site_results)

    SAMPLE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    generate_report.write_all(combined_metrics, run.query_records, run.site_results, SAMPLE_OUTPUT_DIR)

    pipeline_metrics = combined_metrics["pipeline"]
    print(f"[sample] Done in {elapsed:.1f}s.")
    print(f"[sample] Queries: {len(cases)}  Sites pushed through pipeline: {pipeline_metrics['sites_processed']}")
    print(f"[sample] End-to-end success rate: {pipeline_metrics['end_to_end_success_rate'] * 100:.1f}%")
    for site in run.site_results:
        status = site.final_status
        score = (site.score or {}).get("total_score") if site.score else None
        print(f"[sample]   {site.query_id:12s} {site.website:40s} status={status:16s} score={score}")
    print(f"[sample] Outputs written to: {SAMPLE_OUTPUT_DIR}")


if __name__ == "__main__":
    main()
