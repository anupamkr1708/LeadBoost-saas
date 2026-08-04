"""
Discovery Evaluation — Output writers.

CSV / JSON export plus the Markdown report. Every number in the report
is read from `aggregate_metrics()`'s output (metrics.py) -- nothing here
computes a new statistic; this module only formats. Recommendations are
built exclusively from measured thresholds already crossed, never
invented (see `_recommendations`).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from discovery_eval.metrics import BusinessRecord, QueryRecord

QUERY_CSV_FIELDS = [
    "id", "query", "domain_tag", "difficulty_tag", "error",
    "parsed_category", "parsed_location", "requested_limit",
    "execution_time_ms", "total_discovery_time_ms", "search_time_ms",
    "resolve_time_ms", "dedup_time_ms", "ranking_time_ms",
    "provider_count", "providers_used", "businesses_found",
    "validated_businesses", "rejected_businesses", "duplicate_businesses",
    "no_website_businesses", "validation_failures", "resolved_via_fallback_count",
    "winner_confidence", "identity_stability", "competition_margin",
    "competition_rival_count", "provider_agreement", "verification_strength",
    "final_selected_website", "audit_flags",
]

BUSINESS_CSV_FIELDS = [
    "query_id", "query", "business_name", "provider", "address", "category",
    "website_candidate", "final_website", "website_validated", "validation_reason",
    "evidence_count", "feature_count", "verification_count", "confidence",
    "identity_stability", "provider_agreement", "competition_score", "rank_score",
    "selection_reason", "rejected_reason", "false_positive_flags", "outcome",
    "audit_flags",
]

MANUAL_REVIEW_CSV_FIELDS = [
    "query", "business", "selected_website", "expected_website", "correct",
    "comments", "confidence", "identity_stability",
]


def write_query_csv(query_records: List[QueryRecord], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=QUERY_CSV_FIELDS)
        writer.writeheader()
        for record in query_records:
            row = record.to_row()
            writer.writerow({k: row.get(k, "") for k in QUERY_CSV_FIELDS})


def write_business_csv(business_records: List[BusinessRecord], path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=BUSINESS_CSV_FIELDS)
        writer.writeheader()
        for record in business_records:
            row = record.to_row()
            writer.writerow({k: row.get(k, "") for k in BUSINESS_CSV_FIELDS})


def write_manual_review_csv(
    query_records: List[QueryRecord], business_records: List[BusinessRecord], path: Path
) -> None:
    """One row per SELECTED business (the ones a human actually needs to
    verify), plus every query that found zero validated results (nothing
    to select, but still needs a human to confirm that's correct). All
    review columns (`expected_website`, `correct`, `comments`) are left
    blank -- per the brief, this file is for human evaluation only."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=MANUAL_REVIEW_CSV_FIELDS)
        writer.writeheader()
        for b in business_records:
            if b.outcome != "selected":
                continue
            writer.writerow({
                "query": b.query,
                "business": b.business_name,
                "selected_website": b.final_website or "",
                "expected_website": "",
                "correct": "",
                "comments": "",
                "confidence": b.confidence if b.confidence is not None else "",
                "identity_stability": b.identity_stability if b.identity_stability is not None else "",
            })
        for q in query_records:
            if q.error is None and q.businesses_found > 0 and q.validated_businesses == 0:
                writer.writerow({
                    "query": q.query,
                    "business": "",
                    "selected_website": "",
                    "expected_website": "",
                    "correct": "",
                    "comments": "no validated business found for this query",
                    "confidence": "",
                    "identity_stability": "",
                })


def write_json(
    query_records: List[QueryRecord],
    business_records: List[BusinessRecord],
    aggregate: Dict[str, Any],
    path: Path,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aggregate_metrics": aggregate,
        "queries": [r.to_row() for r in query_records],
        "businesses": [r.to_row() for r in business_records],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _top_failure_reasons(business_records: List[BusinessRecord], n: int = 10) -> List[tuple]:
    counts: Dict[str, int] = {}
    for b in business_records:
        if b.rejected_reason:
            counts[b.rejected_reason] = counts.get(b.rejected_reason, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _top_validation_failures(business_records: List[BusinessRecord], n: int = 10) -> List[tuple]:
    counts: Dict[str, int] = {}
    for b in business_records:
        if not b.website_validated and b.website_candidate:
            reason = b.validation_reason or "unknown"
            counts[reason] = counts.get(reason, 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]


def _recommendations(agg: Dict[str, Any]) -> List[str]:
    """Every line here is gated on a measured metric already crossing a
    fixed, documented threshold -- no line is added speculatively, and
    no architectural change is ever suggested (per the brief: 'Do not
    recommend architectural changes without evidence')."""
    lines: List[str] = []
    if agg["validation_failure_rate"] > 0.25:
        lines.append(
            f"Validation failure rate is {_fmt_pct(agg['validation_failure_rate'])} "
            "(>25%) -- a meaningful share of found businesses have a website candidate "
            "that never passes validation. Review `business_results.csv` filtered to "
            "`website_validated=False` for the most common `validation_reason` values."
        )
    if agg["website_resolution_success_rate"] < 0.5:
        lines.append(
            f"Website resolution success rate is {_fmt_pct(agg['website_resolution_success_rate'])} "
            "(<50%) -- more than half of found businesses never got a website at all. "
            "Check `queries_no_validated_results` in evaluation.json for which "
            "categories/locations are affected."
        )
    if agg["provider_agreement_rate"] < 0.5:
        lines.append(
            f"Provider agreement rate is {_fmt_pct(agg['provider_agreement_rate'])} "
            "(<50%) among queries where a provider-agreement signal was available -- "
            "independent providers frequently proposed different websites. See the "
            "`provider_disagreement` audit flag in `query_results.csv`."
        )
    if agg["directory_marketplace_selected_count"] > 0:
        lines.append(
            f"{agg['directory_marketplace_selected_count']} selected business(es) resolved to a "
            "domain matching this framework's independent directory/marketplace list, despite "
            "passing Discovery's own validation. Cross-check these against "
            "`application.discovery.website_validator.REJECTED_DOMAINS` for a possible gap "
            "(see `business_results.csv`, `audit_flags` contains `directory_or_marketplace_selected`)."
        )
    if agg["social_profile_selected_count"] > 0:
        lines.append(
            f"{agg['social_profile_selected_count']} selected business(es) resolved to a known "
            "social-platform domain. Same review as above, filtered to `social_profile_selected`."
        )
    if agg["structural_false_positive_count"] > 0:
        lines.append(
            f"{agg['structural_false_positive_count']} selected business(es) carry a structural "
            "false-positive risk code from `false_positive.py` (visible in `False Positive Flags`) "
            "despite being the final selection -- these passed competition/ranking but were still "
            "flagged upstream; worth a manual look."
        )
    if agg["ok_queries"] and len(agg["queries_no_competing_candidate"]) / agg["ok_queries"] > 0.5:
        lines.append(
            f"{len(agg['queries_no_competing_candidate'])} of {agg['ok_queries']} queries "
            f"({_fmt_pct(len(agg['queries_no_competing_candidate']) / agg['ok_queries'])}) had only one "
            "website candidate to resolve identity against, so `winning_margin` is 0.0 by definition "
            "(see `competition.py::compete`'s own single-candidate branch), not a genuinely thin race. "
            "This usually means only one search provider contributed candidates this run -- check "
            "`provider_candidate_counts` above; if it's just one provider, treat this run's "
            "confidence/identity/false-positive numbers as a single-provider baseline, not a "
            "representative measurement of Discovery with all providers active."
        )
    if agg["p95_latency_ms"] > 15000:
        lines.append(
            f"P95 latency is {agg['p95_latency_ms']:.0f}ms. If this benchmark is run "
            "again after a Discovery change and this number moves up materially, treat "
            "it as a regression signal before merging."
        )
    if len(agg["queries_manual_review"]) > 0:
        lines.append(
            f"{len(agg['queries_manual_review'])} of {agg['ok_queries']} queries "
            f"({_fmt_pct(len(agg['queries_manual_review']) / agg['ok_queries'])} if agg['ok_queries'] else 0) "
            "are flagged for manual review (weak confidence, thin competition margin, "
            "provider disagreement, low identity stability, or no validated result). "
            "See `manual_review.csv`."
        )
    if not lines:
        lines.append(
            "No metric crossed this report's fixed audit thresholds this run. That is "
            "itself a measurement, not a guarantee -- re-run after any Discovery change "
            "and compare against this run's `evaluation.json`."
        )
    return lines


def write_markdown_report(
    aggregate: Dict[str, Any],
    query_records: List[QueryRecord],
    business_records: List[BusinessRecord],
    output_dir: Path,
) -> None:
    agg = aggregate
    lines: List[str] = []
    lines.append("# Discovery Evaluation Report")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append(
        "This report evaluates the production Discovery pipeline "
        "(`application.discovery.discovery_service.DiscoveryService`) as a black box, "
        "executed exactly up through ranking (the pipeline's own \"Discovery Result\" "
        "boundary), via its existing internal stage methods. No Lead was created, no "
        "database was written, and no downstream AI/scraper/enrichment pipeline ran "
        "for any query in this benchmark."
    )
    lines.append("")

    # -- Overall Summary --
    lines.append("## Overall Summary")
    lines.append("")
    lines.append(f"- **Discovery Health Score: {agg['discovery_health_score']} / 100**")
    lines.append(f"- Queries run: {agg['total_queries']} ({agg['ok_queries']} parsed and executed, {agg['failed_queries']} failed to parse)")
    lines.append(f"- Businesses found: {agg['total_businesses_found']}")
    lines.append(f"- Validated: {agg['total_validated']} ({_fmt_pct(agg['validation_success_rate'])})")
    lines.append(f"- Rejected (validation failed): {agg['total_rejected']}")
    lines.append(f"- Duplicates removed: {agg['total_duplicates']}")
    lines.append(f"- No website found: {agg['total_no_website']}")
    lines.append("")

    # -- Latency --
    lines.append("## Latency Analysis")
    lines.append("")
    lines.append("| Metric | Value (ms) |")
    lines.append("|---|---|")
    lines.append(f"| Average | {agg['avg_latency_ms']} |")
    lines.append(f"| Median | {agg['median_latency_ms']} |")
    lines.append(f"| P95 | {agg['p95_latency_ms']} |")
    lines.append(f"| P99 | {agg['p99_latency_ms']} |")
    lines.append("")
    lines.append("See `latency.png` for the full distribution and per-stage breakdown.")
    lines.append("")

    # -- Validation --
    lines.append("## Validation Analysis")
    lines.append("")
    lines.append(f"- Validation success rate: {_fmt_pct(agg['validation_success_rate'])}")
    lines.append(f"- Website resolution success rate: {_fmt_pct(agg['website_resolution_success_rate'])}")
    lines.append(f"- Validation failure rate: {_fmt_pct(agg['validation_failure_rate'])}")
    lines.append(f"- Duplicate rate: {_fmt_pct(agg['duplicate_rate'])}")
    lines.append("")
    failures = _top_validation_failures(business_records)
    if failures:
        lines.append("**Most common validation failures:**")
        lines.append("")
        lines.append("| Reason | Count |")
        lines.append("|---|---|")
        for reason, count in failures:
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    # -- Provider Analysis --
    lines.append("## Provider Analysis")
    lines.append("")
    lines.append(f"- Provider agreement rate: {_fmt_pct(agg['provider_agreement_rate'])}")
    lines.append("")
    lines.append("**Candidates contributed per provider (`BusinessCandidate.source`):**")
    lines.append("")
    lines.append("| Provider | Candidates |")
    lines.append("|---|---|")
    for provider, count in sorted(agg["provider_candidate_counts"].items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"| {provider} | {count} |")
    lines.append("")
    lines.append("See `provider_performance.png` for a visual breakdown.")
    lines.append("")

    # -- Ranking / Identity --
    lines.append("## Ranking Analysis")
    lines.append("")
    lines.append(f"- Average winner confidence: {agg['avg_confidence']:.3f}")
    lines.append(f"- Average competition margin: {agg['avg_competition_margin']:.3f}")
    lines.append(f"- Queries with multiple strong candidates (top-2 rank scores within "
                  f"{5.0} points): {len(agg['queries_multiple_strong_candidates'])}")
    lines.append(f"- Queries where the winner had no rival candidate at all "
                  f"(`compete()` had exactly one domain to evaluate -- margin is 0.0 by "
                  f"definition, not a close race): {len(agg['queries_no_competing_candidate'])}")
    lines.append("")

    lines.append("## Identity Analysis")
    lines.append("")
    lines.append(f"- Average identity stability: {agg['avg_identity_stability']:.3f}")
    lines.append(f"- Selected businesses resolving to a directory/marketplace domain: "
                  f"{agg['directory_marketplace_selected_count']}")
    lines.append(f"- Selected businesses resolving to a social-profile domain: "
                  f"{agg['social_profile_selected_count']}")
    lines.append(f"- Selected businesses carrying a structural false-positive signal: "
                  f"{agg['structural_false_positive_count']}")
    lines.append("")

    # -- Failure reasons --
    lines.append("## Most Common Failure Reasons")
    lines.append("")
    reasons = _top_failure_reasons(business_records)
    if reasons:
        lines.append("| Reason | Count |")
        lines.append("|---|---|")
        for reason, count in reasons:
            lines.append(f"| {reason} | {count} |")
    else:
        lines.append("No rejected businesses recorded in this run.")
    lines.append("")

    # -- Queries needing attention --
    def _query_list_section(title: str, ids: List[str]) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not ids:
            lines.append("None.")
        else:
            by_id = {q.id: q for q in query_records}
            for qid in ids:
                q = by_id.get(qid)
                if q:
                    lines.append(f"- `{qid}` — \"{q.query}\"")
        lines.append("")

    _query_list_section("Queries That Need Manual Review", agg["queries_manual_review"])
    _query_list_section(
        "Queries With Weak Competition (margin < %.2f)" % 0.05,
        [q.id for q in query_records if "thin_competition_margin" in q.audit_flags],
    )
    _query_list_section(
        "Queries With Low Confidence (winner confidence < %.2f)" % 0.5,
        [q.id for q in query_records if "weak_confidence_winner" in q.audit_flags],
    )
    _query_list_section("Queries With No Website", agg["queries_no_validated_results"])
    _query_list_section(
        "Queries Where The Winner Had No Rival Candidate (margin 0.0 by definition)",
        agg["queries_no_competing_candidate"],
    )

    # -- Discovery Health Score breakdown --
    lines.append("## Discovery Health Score")
    lines.append("")
    lines.append(
        f"**{agg['discovery_health_score']} / 100** — unweighted mean of five already-measured "
        "rates from this run: validation success rate, website resolution success rate, "
        "average winner confidence, average identity stability, and query parse success rate. "
        "Not a Discovery-computed number; this is this evaluation framework's own rollup, for "
        "regression comparison across runs, not a judgment Discovery itself makes."
    )
    lines.append("")

    # -- Recommendations --
    lines.append("## Recommendations")
    lines.append("")
    lines.append(
        "Every recommendation below is triggered by a specific metric in this run "
        "crossing a fixed threshold (see `report.py::_recommendations`). None of these "
        "propose an architectural change -- per the evaluation brief, this framework "
        "only measures Discovery, it does not redesign it."
    )
    lines.append("")
    for rec in _recommendations(agg):
        lines.append(f"- {rec}")
    lines.append("")

    if agg["failed_query_ids"]:
        lines.append("## Queries That Failed To Execute")
        lines.append("")
        lines.append(
            "These raised `QueryParseError` or an unhandled exception before reaching "
            "Discovery's search stage; see `query_results.csv`'s `error` column."
        )
        lines.append("")
        by_id = {q.id: q for q in query_records}
        for qid in agg["failed_query_ids"]:
            q = by_id[qid]
            lines.append(f"- `{qid}` — \"{q.query}\": {q.error}")
        lines.append("")

    (output_dir / "evaluation_report.md").write_text("\n".join(lines), encoding="utf-8")
