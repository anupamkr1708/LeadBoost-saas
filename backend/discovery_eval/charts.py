"""
Discovery Evaluation — Charts.

Four charts, exactly as scoped: latency.png, confidence.png,
validation_success.png, provider_performance.png. Every input is a
value already present on QueryRecord/BusinessRecord or already computed
by metrics.aggregate_metrics -- this module only plots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from discovery_eval.metrics import BusinessRecord, QueryRecord


def _ok(query_records: List[QueryRecord]) -> List[QueryRecord]:
    return [q for q in query_records if q.error is None]


def generate_latency_chart(query_records: List[QueryRecord], path: Path) -> None:
    ok = _ok(query_records)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))

    latencies = [q.execution_time_ms for q in ok]
    axes[0].hist(latencies, bins=min(20, max(5, len(latencies) // 3)) or 5, color="#4C72B0", edgecolor="white")
    axes[0].set_title("Query Execution Time Distribution")
    axes[0].set_xlabel("Execution time (ms)")
    axes[0].set_ylabel("Query count")

    stage_labels = ["search", "resolve", "dedup", "rank"]
    stage_values = [
        sum(q.search_time_ms for q in ok) / len(ok) if ok else 0,
        sum(q.resolve_time_ms for q in ok) / len(ok) if ok else 0,
        sum(q.dedup_time_ms for q in ok) / len(ok) if ok else 0,
        sum(q.ranking_time_ms for q in ok) / len(ok) if ok else 0,
    ]
    axes[1].bar(stage_labels, stage_values, color="#55A868")
    axes[1].set_title("Average Time per Pipeline Stage")
    axes[1].set_ylabel("Average time (ms)")

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_confidence_chart(query_records: List[QueryRecord], path: Path) -> None:
    ok = _ok(query_records)
    confidences = [q.winner_confidence for q in ok if q.winner_confidence is not None]

    fig, ax = plt.subplots(figsize=(7, 4.5))
    if confidences:
        ax.hist(confidences, bins=20, range=(0, 1), color="#C44E52", edgecolor="white")
        mean_conf = sum(confidences) / len(confidences)
        ax.axvline(mean_conf, color="black", linestyle="--", linewidth=1, label=f"mean = {mean_conf:.2f}")
        ax.legend()
    else:
        ax.text(0.5, 0.5, "No winner-confidence data available", ha="center", va="center")
    ax.set_title("Winner Confidence Distribution")
    ax.set_xlabel("Selection confidence (0-1)")
    ax.set_ylabel("Query count")
    ax.set_xlim(0, 1)

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_validation_success_chart(business_records: List[BusinessRecord], path: Path) -> None:
    outcome_counts: Dict[str, int] = {}
    for b in business_records:
        outcome_counts[b.outcome] = outcome_counts.get(b.outcome, 0) + 1

    order = ["selected", "not_selected", "validation_failed", "no_website", "duplicate"]
    labels = [o for o in order if o in outcome_counts] + [
        o for o in outcome_counts if o not in order
    ]
    values = [outcome_counts[label] for label in labels]
    colors = {
        "selected": "#55A868", "not_selected": "#4C72B0", "validation_failed": "#C44E52",
        "no_website": "#8172B2", "duplicate": "#CCB974",
    }
    bar_colors = [colors.get(label, "#777777") for label in labels]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    ax.bar(labels, values, color=bar_colors)
    ax.set_title("Business Outcomes Across All Queries")
    ax.set_ylabel("Business count")
    for i, v in enumerate(values):
        ax.text(i, v, str(v), ha="center", va="bottom")
    plt.setp(ax.get_xticklabels(), rotation=20, ha="right")

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_provider_performance_chart(
    aggregate: Dict[str, Any], path: Path
) -> None:
    counts = aggregate.get("provider_candidate_counts", {})
    providers = sorted(counts.keys(), key=lambda p: counts[p], reverse=True)
    values = [counts[p] for p in providers]

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    if providers:
        ax.bar(providers, values, color="#4C72B0")
        for i, v in enumerate(values):
            ax.text(i, v, str(v), ha="center", va="bottom")
    else:
        ax.text(0.5, 0.5, "No provider data available", ha="center", va="center")
    ax.set_title("Candidates Contributed per Provider")
    ax.set_ylabel("Candidate count")
    ax.set_xlabel("Provider (BusinessCandidate.source)")

    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def generate_charts(
    query_records: List[QueryRecord],
    business_records: List[BusinessRecord],
    aggregate: Dict[str, Any],
    output_dir: Path,
) -> None:
    generate_latency_chart(query_records, output_dir / "latency.png")
    generate_confidence_chart(query_records, output_dir / "confidence.png")
    generate_validation_success_chart(business_records, output_dir / "validation_success.png")
    generate_provider_performance_chart(aggregate, output_dir / "provider_performance.png")
