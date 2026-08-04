"""
Lightweight tests for the Discovery Evaluation Framework itself.

These do NOT import application.discovery.* (the real production
package isn't on the path in an isolated test environment, and
shouldn't need to be for these tests -- see run_eval.py's own deferred-
import docstring). Instead they use the smallest possible no-op stubs
that duck-type DiscoveredBusiness/VerifiedDigitalIdentity's attribute
shape, per the evaluation brief's own instruction: "If imports require
stubs, create the smallest possible no-op stubs only for smoke
testing."

Covers exactly what the brief asks for: metrics calculations, CSV
generation, report generation, JSON export.
"""

import csv
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from discovery_eval.metrics import (
    BusinessRecord,
    QueryRecord,
    aggregate_metrics,
    audit_business,
    audit_query,
    build_query_record,
    extract_business_record,
)
from discovery_eval.queries import BENCHMARK_QUERIES, QueryCase
from discovery_eval.report import (
    write_business_csv,
    write_json,
    write_manual_review_csv,
    write_markdown_report,
    write_query_csv,
)
from discovery_eval.charts import generate_charts


# ---------------------------------------------------------------------------
# No-op stubs -- duck-type the real DTOs closely enough for extraction
# logic to exercise every attribute path it reads, without importing
# pydantic/sqlalchemy/the application package.
# ---------------------------------------------------------------------------


def _candidate(name="Regal Shoes", source="overpass", website="https://regalshoes.example",
               address="12 MG Road, Mumbai", category="shoe store"):
    return SimpleNamespace(name=name, category=category, address=address, phone="022-1234567",
                            website=website, latitude=19.07, longitude=72.87, rating=4.2,
                            review_count=58, source=source)


def _resolution(website="https://regalshoes.example", resolved_via="overpass",
                 validated=True, rejection_reason=None):
    return SimpleNamespace(website=website, resolved_via=resolved_via, validated=validated,
                            rejection_reason=rejection_reason)


def _fake_identity(*, confidence=0.82, stability=0.71, provider_consensus=0.66,
                    verification_strength=0.75, selected_website="https://regalshoes.example",
                    winning_margin=0.18, risk=()):
    domain = selected_website.split("//", 1)[-1]
    competition = [
        SimpleNamespace(domain=domain, relative_confidence=confidence, margin=winning_margin,
                         winning_margin=winning_margin, losing_margin=0.0),
        SimpleNamespace(domain="rival.example", relative_confidence=confidence - winning_margin,
                         margin=-winning_margin, winning_margin=0.0, losing_margin=winning_margin),
    ]
    identity_candidates = [
        SimpleNamespace(normalized_domain=domain, registrable_domain=domain, risk=risk),
    ]
    identity_resolution = SimpleNamespace(
        competition=competition,
        identity_candidates=identity_candidates,
        selection_confidence=confidence,
        selection_reason=f"{domain} selected",
    )
    return SimpleNamespace(
        selected_website=selected_website,
        selection_confidence=confidence,
        selection_reason=f"{domain} selected",
        quality=SimpleNamespace(identity_stability=stability),
        consensus=SimpleNamespace(provider_consensus=provider_consensus),
        verification_quality=SimpleNamespace(strength=verification_strength),
        evidence_bundle=SimpleNamespace(items=[object(), object(), object()]),
        feature_store=SimpleNamespace(all=lambda: [SimpleNamespace(features=[object(), object()])]),
        verification_bundle=SimpleNamespace(results=[object(), object()]),
        identity_resolution=identity_resolution,
    )


def _discovered_business(*, name="Regal Shoes", validated=True, is_duplicate=False,
                          duplicate_key=None, rank_score=88.5, identity="default",
                          website="https://regalshoes.example", rejection_reason=None):
    if identity == "default":
        identity = _fake_identity(selected_website=website) if validated else None
    return SimpleNamespace(
        candidate=_candidate(name=name, website=website),
        resolution=_resolution(website=website if validated else None, validated=validated,
                                rejection_reason=rejection_reason),
        is_duplicate=is_duplicate,
        duplicate_key=duplicate_key,
        rank_score=rank_score,
        identity=identity,
    )


_CASE = QueryCase("t-01", "shoe stores in Mumbai", "Retail", "standard")
_PARSED = SimpleNamespace(category="shoe stores", location="Mumbai", limit=20)


# ---------------------------------------------------------------------------
# Business-record extraction
# ---------------------------------------------------------------------------


def test_extract_business_record_selected_reads_identity_fields():
    business = _discovered_business()
    record = extract_business_record(_CASE, business, "selected")

    assert record.business_name == "Regal Shoes"
    assert record.website_validated is True
    assert record.evidence_count == 3
    assert record.feature_count == 2
    assert record.verification_count == 2
    assert record.confidence == pytest.approx(0.82)
    assert record.identity_stability == pytest.approx(0.71)
    assert record.provider_agreement == pytest.approx(0.66)
    assert record.competition_score == pytest.approx(0.82)
    assert record.rank_score == pytest.approx(88.5)
    assert record.outcome == "selected"
    assert record.rejected_reason is None


def test_extract_business_record_rejected_has_reason_no_identity_crash():
    business = _discovered_business(validated=False, identity=None, website=None,
                                     rejection_reason="unreachable")
    record = extract_business_record(_CASE, business, "no_website")

    assert record.website_validated is False
    assert record.rejected_reason == "unreachable"
    assert record.confidence is None
    assert record.evidence_count == 0


def test_extract_business_record_duplicate_reason_cites_duplicate_key():
    business = _discovered_business(is_duplicate=True, duplicate_key="regalshoes.example|mumbai")
    record = extract_business_record(_CASE, business, "duplicate")
    assert "regalshoes.example" in record.rejected_reason


def test_false_positive_flags_surface_from_matched_identity_candidate():
    business = _discovered_business(identity=_fake_identity(risk=("cross_tenant_domain",)))
    record = extract_business_record(_CASE, business, "selected")
    assert "cross_tenant_domain" in record.false_positive_flags


# ---------------------------------------------------------------------------
# Audit rules
# ---------------------------------------------------------------------------


def test_audit_business_flags_weak_confidence():
    record = BusinessRecord(
        query_id="t", query="q", business_name="B", provider="overpass", address=None,
        category=None, website_candidate=None, final_website="https://x.example",
        website_validated=True, validation_reason="validated", evidence_count=1,
        feature_count=1, verification_count=1, confidence=0.2, identity_stability=0.8,
        provider_agreement=0.9, competition_score=0.5, rank_score=50.0,
        selection_reason="x", rejected_reason=None, false_positive_flags="", outcome="selected",
    )
    flags = audit_business(record)
    assert "weak_confidence_winner" in flags


def test_audit_business_flags_directory_domain():
    record = BusinessRecord(
        query_id="t", query="q", business_name="B", provider="overpass", address=None,
        category=None, website_candidate=None, final_website="https://www.justdial.com/listing/1",
        website_validated=True, validation_reason="validated", evidence_count=1,
        feature_count=1, verification_count=1, confidence=0.9, identity_stability=0.9,
        provider_agreement=0.9, competition_score=0.9, rank_score=90.0,
        selection_reason="x", rejected_reason=None, false_positive_flags="", outcome="selected",
    )
    flags = audit_business(record)
    assert "directory_or_marketplace_selected" in flags


# ---------------------------------------------------------------------------
# Query-record building + aggregate metrics
# ---------------------------------------------------------------------------


def test_build_query_record_happy_path():
    winner = _discovered_business()
    loser = _discovered_business(name="Other Shoes", website="https://other.example", rank_score=40.0,
                                   identity=_fake_identity(selected_website="https://other.example"))
    discovered = [winner, loser]
    record = build_query_record(
        _CASE, parsed=_PARSED, candidates=[winner.candidate, loser.candidate],
        discovered=discovered, eligible=discovered, rejected=[], duplicates_removed=0,
        resolved_via_fallback_count=0, selected=[winner],
        stage_times_ms={"total_ms": 1200, "search_ms": 300, "resolve_ms": 700, "dedup_ms": 50, "rank_ms": 150},
    )
    assert record.error is None
    assert record.businesses_found == 2
    assert record.validated_businesses == 2
    assert record.final_selected_website == "https://regalshoes.example"
    assert record.winner_confidence == pytest.approx(0.82)
    assert record.execution_time_ms == 1200


def test_build_query_record_parse_error_short_circuits():
    record = build_query_record(_CASE, error="Could not parse query")
    assert record.error == "Could not parse query"
    assert "query_parse_or_provider_error" in record.audit_flags
    assert record.businesses_found == 0


def test_audit_query_flags_thin_margin_and_manual_review():
    record = QueryRecord(
        id="t", query="q", domain_tag="Retail", difficulty_tag="standard",
        winner_confidence=0.9, identity_stability=0.9, provider_agreement=0.9,
        competition_margin=0.01, competition_rival_count=2, validated_businesses=1, businesses_found=1,
    )
    flags = audit_query(record, eligible=[])
    assert "thin_competition_margin" in flags
    assert "manual_review_recommended" in flags


def test_audit_query_does_not_flag_thin_margin_when_no_rival_existed():
    """Regression test: compete()'s sole-candidate branch defines
    winning_margin=0.0 by convention (no rival to compete against), not
    because of a genuinely close race. A margin of exactly 0.0 with
    only one candidate must NOT trip thin_competition_margin /
    manual_review_recommended -- it should be distinguishable via
    no_competing_candidate instead."""
    record = QueryRecord(
        id="t", query="q", domain_tag="Retail", difficulty_tag="standard",
        winner_confidence=0.9, identity_stability=0.9, provider_agreement=0.9,
        competition_margin=0.0, competition_rival_count=1, validated_businesses=1, businesses_found=1,
    )
    flags = audit_query(record, eligible=[])
    assert "thin_competition_margin" not in flags
    assert "manual_review_recommended" not in flags
    assert "no_competing_candidate" in flags


def test_aggregate_metrics_handles_mixed_success_and_failure():
    ok_record = QueryRecord(
        id="q1", query="a", domain_tag="Retail", difficulty_tag="standard",
        execution_time_ms=1000, businesses_found=5, validated_businesses=4,
        rejected_businesses=1, duplicate_businesses=0, no_website_businesses=1,
        validation_failures=0, winner_confidence=0.8, identity_stability=0.7,
        competition_margin=0.2, provider_agreement=0.9,
    )
    failed_record = build_query_record(QueryCase("q2", "??", "Retail"), error="boom")
    agg = aggregate_metrics([ok_record, failed_record], [])

    assert agg["total_queries"] == 2
    assert agg["ok_queries"] == 1
    assert agg["failed_queries"] == 1
    assert agg["avg_latency_ms"] == 1000.0
    assert agg["total_businesses_found"] == 5
    assert agg["validation_success_rate"] == pytest.approx(4 / 5)
    assert 0.0 <= agg["discovery_health_score"] <= 100.0


def test_aggregate_metrics_empty_input_does_not_crash():
    agg = aggregate_metrics([], [])
    assert agg["total_queries"] == 0
    assert agg["avg_latency_ms"] == 0.0
    assert agg["discovery_health_score"] == 0.0


# ---------------------------------------------------------------------------
# CSV / JSON / Markdown / chart generation (I/O smoke tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_records():
    winner = _discovered_business()
    query_record = build_query_record(
        _CASE, parsed=_PARSED, candidates=[winner.candidate], discovered=[winner],
        eligible=[winner], rejected=[], duplicates_removed=0, resolved_via_fallback_count=0,
        selected=[winner], stage_times_ms={"total_ms": 900, "search_ms": 200, "resolve_ms": 500,
                                            "dedup_ms": 50, "rank_ms": 150},
    )
    business_record = extract_business_record(_CASE, winner, "selected")
    return [query_record], [business_record]


def test_write_query_csv_roundtrips(sample_records, tmp_path):
    query_records, _ = sample_records
    path = tmp_path / "query_results.csv"
    write_query_csv(query_records, path)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["query"] == "shoe stores in Mumbai"
    assert rows[0]["businesses_found"] == "1"


def test_write_business_csv_roundtrips(sample_records, tmp_path):
    _, business_records = sample_records
    path = tmp_path / "business_results.csv"
    write_business_csv(business_records, path)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["business_name"] == "Regal Shoes"
    assert rows[0]["outcome"] == "selected"


def test_write_manual_review_csv_has_blank_review_columns(sample_records, tmp_path):
    query_records, business_records = sample_records
    path = tmp_path / "manual_review.csv"
    write_manual_review_csv(query_records, business_records, path)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["correct"] == ""
    assert rows[0]["expected_website"] == ""
    assert rows[0]["business"] == "Regal Shoes"


def test_write_json_is_valid_and_contains_aggregate(sample_records, tmp_path):
    query_records, business_records = sample_records
    aggregate = aggregate_metrics(query_records, business_records)
    path = tmp_path / "evaluation.json"
    write_json(query_records, business_records, aggregate, path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert "aggregate_metrics" in payload
    assert payload["aggregate_metrics"]["total_queries"] == 1
    assert len(payload["queries"]) == 1
    assert len(payload["businesses"]) == 1


def test_write_markdown_report_generates_expected_sections(sample_records, tmp_path):
    query_records, business_records = sample_records
    aggregate = aggregate_metrics(query_records, business_records)
    write_markdown_report(aggregate, query_records, business_records, tmp_path)

    report_path = tmp_path / "evaluation_report.md"
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    for heading in [
        "# Discovery Evaluation Report", "## Overall Summary", "## Latency Analysis",
        "## Validation Analysis", "## Provider Analysis", "## Ranking Analysis",
        "## Identity Analysis", "## Discovery Health Score", "## Recommendations",
    ]:
        assert heading in text


def test_generate_charts_writes_all_four_png_files(sample_records, tmp_path):
    query_records, business_records = sample_records
    aggregate = aggregate_metrics(query_records, business_records)
    generate_charts(query_records, business_records, aggregate, tmp_path)

    for filename in ["latency.png", "confidence.png", "validation_success.png", "provider_performance.png"]:
        path = tmp_path / filename
        assert path.exists()
        assert path.stat().st_size > 0


def test_generate_charts_handles_empty_input(tmp_path):
    generate_charts([], [], aggregate_metrics([], []), tmp_path)
    for filename in ["latency.png", "confidence.png", "validation_success.png", "provider_performance.png"]:
        assert (tmp_path / filename).exists()


# ---------------------------------------------------------------------------
# Query dataset sanity
# ---------------------------------------------------------------------------


def test_benchmark_query_ids_are_unique():
    ids = [c.id for c in BENCHMARK_QUERIES]
    assert len(ids) == len(set(ids))


def test_benchmark_has_approximately_100_queries():
    assert 90 <= len(BENCHMARK_QUERIES) <= 130
