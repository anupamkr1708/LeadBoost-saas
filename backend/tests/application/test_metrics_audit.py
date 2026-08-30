"""
Regression tests for the metrics/analytics audit.

Each test proves one CONFIRMED bug: it is written and run against the
UNMODIFIED code first (see the audit report for the exact before/after
pytest output), fails there for the documented reason, and passes once
the minimal fix lands. Every test traces raw upstream data -> the exact
metric/instrumentation code path -> the final value, per the audit's own
"evidence over assumption" rule -- no formula is changed, only what data
feeds it.
"""

from datetime import date, timedelta

import pytest

from application.discovery.dto import BusinessCandidate, WebsiteResolution
from application.discovery.discovery_service import DiscoveryService
from application.workflows.graph_nodes import LeadPipelineNodes


# ---------------------------------------------------------------------------
# TASK 1 -- average_grounding = 0.02
# ---------------------------------------------------------------------------


class TestGroundingSourceTextCompleteness:
    """CONFIRMED BUG. Root cause: `confidence_evaluation`'s `source_text`
    (application/workflows/graph_nodes.py) was built only from
    about_text + scraped_data.text_content + industry_analysis. But the
    Decision Agent's own prompt (application/prompts/templates/
    decision_v1.yaml) explicitly instructs it to draw "evidence" from
    pain_points, growth_indicators, score, qualification_label, and
    icp_alignment_score too (see the `variables` list and the human
    message template) -- and its deterministic fallback
    (DecisionAgent._decide_deterministically) produces evidence strings
    made *entirely* of the score/confidence values, never of scraped
    prose at all. None of those fields were in `source_text`, so
    genuinely-grounded evidence citing them was scored as ungrounded
    purely because the reference text didn't contain what was being
    checked against -- not because the AI hallucinated."""

    async def test_evidence_citing_pain_points_and_growth_indicators_is_grounded(
        self, db_session, sample_lead
    ):
        """The LLM path: evidence that genuinely, correctly restates a
        real pain_point/growth_indicator the Company Intelligence stage
        produced must be recognized as grounded."""
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "grounding-test-llm-path",
            "context": {
                "lead_id": sample_lead.id,
                "organization_id": sample_lead.organization_id,
                "company_name": "Acme Robotics",
                "website": sample_lead.website,
                "about_text": "Acme Robotics builds industrial automation hardware.",
                "scraped_data": {"text_content": ""},
                "enriched_data": {},
                "sender_org": "Our Company",
            },
            "company_intelligence": {
                "industry_analysis": "Industrial automation and robotics.",
                "pain_points": ["High customer support ticket volume reported in reviews"],
                "growth_indicators": ["Recently opened a second manufacturing facility"],
                "icp_alignment_score": 0.72,
            },
            "score_result": {"total_score": 81.4, "qualification_label": "Hot Lead"},
            "scraping_result": {"confidence": 0.83},
            "enrichment_result": {"confidence": 0.6},
            "decision": {
                "qualification": "Hot Lead",
                "explanation": {
                    "confidence": 0.9,
                    # Genuinely, correctly drawn from the pain_points/
                    # growth_indicators above -- exactly what
                    # decision_v1.yaml instructs the LLM to produce.
                    "evidence": [
                        "High customer support ticket volume reported in reviews",
                        "Recently opened a second manufacturing facility",
                    ],
                },
            },
            "stage_timings_ms": {},
            "errors": [],
        }

        result = await nodes.confidence_evaluation(state)

        assert result["errors"] == []
        report = result["evaluation"]
        assert report is not None
        assert report["grounding"] >= 0.9, (
            "genuinely-grounded evidence quoting real pain_points/growth_indicators "
            f"was scored as ungrounded: grounding={report['grounding']}"
        )

    async def test_deterministic_fallback_evidence_is_grounded(self, db_session, sample_lead):
        """The no-LLM path: DecisionAgent._decide_deterministically's own
        evidence strings ("Deterministic score: X/100", "ICP alignment
        score: Y", ...) are not hallucination risk at all -- they are a
        computed audit trail -- but the grounding check was still run
        against them, and previously scored 0.0 every single time because
        none of those values were in source_text."""
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "grounding-test-deterministic-path",
            "context": {
                "lead_id": sample_lead.id,
                "organization_id": sample_lead.organization_id,
                "company_name": "Acme Robotics",
                "website": sample_lead.website,
                "about_text": "",
                "scraped_data": {},
                "enriched_data": {},
                "sender_org": "Our Company",
            },
            "company_intelligence": {"icp_alignment_score": 0.65},
            "score_result": {"total_score": 72.3, "qualification_label": "Warm Lead"},
            "scraping_result": {"confidence": 0.8},
            "enrichment_result": {"confidence": 0.75},
            "decision": {
                "qualification": "Warm Lead",
                "explanation": {
                    "confidence": 0.85,
                    # Exactly DecisionAgent._decide_deterministically's
                    # own format strings.
                    "evidence": [
                        "Deterministic score: 72.3/100",
                        "ICP alignment score: 0.65",
                        "Scrape confidence: 0.8",
                        "Enrichment confidence: 0.75",
                    ],
                },
            },
            "stage_timings_ms": {},
            "errors": [],
        }

        result = await nodes.confidence_evaluation(state)

        assert result["errors"] == []
        report = result["evaluation"]
        assert report["grounding"] >= 0.75, (
            "deterministic-fallback evidence (a computed audit trail, not an "
            f"LLM claim) was scored as ungrounded: grounding={report['grounding']}"
        )

    async def test_evidence_with_no_support_anywhere_is_still_scored_ungrounded(
        self, db_session, sample_lead
    ):
        """Guardrail: the fix must not make grounding trivially always
        pass. A claim with no relationship to any upstream field must
        still score low -- this is what proves the check still does
        something, rather than being fixed by disabling it."""
        nodes = LeadPipelineNodes(db_session)
        state = {
            "lead_id": sample_lead.id,
            "organization_id": sample_lead.organization_id,
            "pipeline_id": "grounding-test-negative-control",
            "context": {
                "lead_id": sample_lead.id,
                "organization_id": sample_lead.organization_id,
                "company_name": "Acme Robotics",
                "website": sample_lead.website,
                "about_text": "Acme Robotics builds industrial automation hardware.",
                "scraped_data": {"text_content": ""},
                "enriched_data": {},
                "sender_org": "Our Company",
            },
            "company_intelligence": {
                "industry_analysis": "Industrial automation and robotics.",
                "pain_points": [],
                "growth_indicators": [],
            },
            "score_result": {"total_score": 40.0, "qualification_label": "Cold Lead"},
            "scraping_result": {"confidence": 0.5},
            "enrichment_result": {"confidence": 0.5},
            "decision": {
                "qualification": "Cold Lead",
                "explanation": {
                    "confidence": 0.6,
                    "evidence": [
                        "Recently secured a nine figure acquisition from a private equity firm"
                    ],
                },
            },
            "stage_timings_ms": {},
            "errors": [],
        }

        result = await nodes.confidence_evaluation(state)

        assert result["errors"] == []
        report = result["evaluation"]
        assert report["grounding"] < 0.5, (
            "an unsupported claim with no relationship to any real upstream field "
            f"was incorrectly scored as grounded: grounding={report['grounding']}"
        )


# ---------------------------------------------------------------------------
# TASK 2 -- website_resolution_rate_pct = 0.0
# ---------------------------------------------------------------------------


class TestWebsiteResolutionFallbackCounting:
    """CONFIRMED BUG. Root cause:
    `DiscoveryService._resolve_and_validate` (application/discovery/
    discovery_service.py) counts a website as "resolved via fallback"
    with the hardcoded check `b.resolution.resolved_via == "brave"`. But
    `DiscoveryService.__init__`'s actual default fallback provider is
    `SerperWebsiteResolver()` (application/discovery/providers/
    serper_provider.py), whose `.name == "serper"` -- and
    `WebsiteResolver` stamps every fallback-resolved candidate's
    `resolved_via` with `self.fallback_provider.name`
    (application/discovery/website_resolver.py), i.e. the literal string
    "serper" in every real deployment using the default (which
    api/endpoints/discovery.py's `DiscoveryService(db)` always does --
    no caller ever passes a custom resolver_fallback). "brave" is
    therefore dead code in every real deployment: the count is
    structurally guaranteed to be 0 regardless of how many websites are
    actually, successfully resolved via the fallback path -- exactly the
    observed website_resolution_rate_pct = 0.0 despite runtime evidence
    of real fallback resolutions.

    This also explains why the EXISTING test suite never caught it: its
    own stub fallback provider in
    tests/application/discovery/test_discovery_service.py::_StubFallback
    happens to be named "brave" too, silently matching the same wrong
    assumption instead of the real production default."""

    @staticmethod
    def _fake_resolve_with_digital_identity(resolved_via: str):
        async def _fake(candidate, location):
            return (
                WebsiteResolution(
                    website=f"https://{candidate.name.lower().replace(' ', '')}.com",
                    resolved_via=resolved_via,
                    validated=True,
                ),
                None,
            )

        return _fake

    async def test_fallback_count_reflects_the_actually_configured_provider_name(
        self, db_session, monkeypatch
    ):
        service = DiscoveryService(db=db_session)
        # The real default -- confirmed above -- is SerperWebsiteResolver,
        # whose .name is "serper", not "brave".
        assert service.resolver_fallback.name == "serper"

        monkeypatch.setattr(
            service.resolver,
            "resolve_with_digital_identity",
            self._fake_resolve_with_digital_identity("serper"),
        )

        candidates = [BusinessCandidate(name="Acme Shoes", category="shoe stores")]
        discovered, resolved_via_fallback_count = await service._resolve_and_validate(
            candidates, location="Bengaluru"
        )

        assert discovered[0].resolution.resolved_via == "serper"
        assert resolved_via_fallback_count == 1, (
            "a website genuinely resolved via the actually-configured fallback "
            "provider (serper) was not counted as a fallback resolution"
        )

    async def test_overpass_resolved_and_unresolved_are_never_counted_as_fallback(
        self, db_session, monkeypatch
    ):
        """Guardrail: the fix must not start counting *every* resolution
        as a fallback hit -- only genuine fallback-provider resolutions,
        never the primary (overpass) path or an outright failure."""
        service = DiscoveryService(db=db_session)

        async def _fake(candidate, location):
            if candidate.name == "Primary Path Co":
                return WebsiteResolution(
                    website="https://primary.com", resolved_via="overpass", validated=True
                ), None
            return WebsiteResolution(website=None, resolved_via="none", validated=False), None

        monkeypatch.setattr(service.resolver, "resolve_with_digital_identity", _fake)

        candidates = [
            BusinessCandidate(name="Primary Path Co", category="shoe stores"),
            BusinessCandidate(name="Unresolved Co", category="shoe stores"),
        ]
        _, resolved_via_fallback_count = await service._resolve_and_validate(
            candidates, location="Bengaluru"
        )

        assert resolved_via_fallback_count == 0


# ---------------------------------------------------------------------------
# TASK 4 -- social_coverage / technology_coverage reported as 10000%
# ---------------------------------------------------------------------------


class TestBenchmarkCoveragePercentDoubleConversion:
    """CONFIRMED BUG. Root cause: `scripts/evaluation/metrics.py::_pct()`
    already returns a 0-100 scaled percentage (`round(100.0 * numerator /
    denominator, 1)`), but `scripts/evaluation/generate_report.py::_fmt_pct()`
    assumes its input is a 0-1 fraction and multiplies by 100 again
    (`f"{x * 100:.1f}%"`). Every metric computed via `_rate()` (a genuine
    0-1 fraction) is displayed correctly by `_fmt_pct`; every metric
    computed via `_pct()` and then also passed through `_fmt_pct` is
    double-converted. This affects exactly four report lines --
    organization_type_coverage, address_coverage, social_coverage,
    technology_coverage -- all four already-0-100 values from
    `_normalizer_metrics`. A site with 100% real coverage (every one of
    N sites has the field) reports as 10000.0%, exactly matching the
    audit's evidence."""

    def test_full_coverage_reports_as_100_not_10000_percent(self, tmp_path):
        from discovery_eval.metrics import QueryRecord
        from scripts.evaluation.generate_report import write_all
        from scripts.evaluation.metrics import SiteResult, build_combined_metrics

        query_records = [
            QueryRecord(
                id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
                businesses_found=1, validated_businesses=1,
            )
        ]
        # Every site has social_profiles and technologies populated --
        # genuine 100% coverage on both fields.
        site = SiteResult(
            query_id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
            business_name="x", website="https://x.com", rank_score=1.0,
            discovery_confidence=0.5,
            normalized={
                "organization_type": "LLC",
                "address": "1 Main St",
                "social_profiles": ["https://linkedin.com/company/x"],
                "technologies": ["react"],
            },
            score={"total_score": 50.0, "qualification_label": "Qualified", "criteria_scores": {}},
            final_status="SUCCESS",
        )
        combined = build_combined_metrics(
            {"total_businesses_found": 1, "total_validated": 1,
             "directory_marketplace_selected_count": 0, "social_profile_selected_count": 0},
            [site],
        )

        # Sanity-check the underlying metric itself is correct (0-100
        # scale, genuinely 100.0) before touching the report formatting
        # at all -- isolates the bug to display, not calculation.
        assert combined["normalizer"]["social_coverage"] == 100.0
        assert combined["normalizer"]["technology_coverage"] == 100.0

        write_all(combined, query_records, [site], tmp_path)
        report_text = (tmp_path / "pipeline_summary.md").read_text()

        assert "10000.0%" not in report_text, (
            "double-percentage-conversion bug reproduced: a genuinely-100% "
            "coverage value was displayed as 10000.0%"
        )
        assert "Social coverage: 100.0%" in report_text
        assert "Technology coverage: 100.0%" in report_text
        assert "Organization type coverage: 100.0%" in report_text
        assert "Address coverage: 100.0%" in report_text

    def test_partial_coverage_still_reports_correctly(self, tmp_path):
        """Guardrail: the fix must not just clamp/hardcode 100% -- a
        genuine partial coverage rate must still display as itself."""
        from discovery_eval.metrics import QueryRecord
        from scripts.evaluation.generate_report import write_all
        from scripts.evaluation.metrics import SiteResult, build_combined_metrics

        query_records = [
            QueryRecord(
                id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
                businesses_found=2, validated_businesses=2,
            )
        ]
        sites = [
            SiteResult(
                query_id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
                business_name="x", website="https://x.com", rank_score=1.0,
                discovery_confidence=0.5,
                normalized={"social_profiles": ["https://linkedin.com/company/x"]},
                final_status="SUCCESS",
            ),
            SiteResult(
                query_id="q1", query="q", domain_tag="Retail", difficulty_tag="standard",
                business_name="y", website="https://y.com", rank_score=1.0,
                discovery_confidence=0.5,
                normalized={},
                final_status="SUCCESS",
            ),
        ]
        combined = build_combined_metrics(
            {"total_businesses_found": 2, "total_validated": 2,
             "directory_marketplace_selected_count": 0, "social_profile_selected_count": 0},
            sites,
        )

        assert combined["normalizer"]["social_coverage"] == 50.0

        write_all(combined, query_records, sites, tmp_path)
        report_text = (tmp_path / "pipeline_summary.md").read_text()

        assert "Social coverage: 50.0%" in report_text
        assert "5000.0%" not in report_text


# ---------------------------------------------------------------------------
# TASK 3 -- duplicate_removal_rate_pct = 0.0 despite "5 duplicate-flagged
# results" -- investigated, NOT a bug (documented per the audit's own
# "only classify as BUG if the implementation contradicts the evidence" rule)
# ---------------------------------------------------------------------------


class TestDuplicateRemovalRateIsCorrectlyWired:
    """NOT A BUG. `_detect_duplicates` -> `_record_metrics` ->
    `create_discovery_run_record` -> `DiscoveryRunRecord.duplicates_removed`
    -> `get_discovery_metrics`'s `total_duplicates / total_businesses`
    is a fully, correctly wired path end to end (proven below: a
    persisted run with real duplicates produces a correctly non-zero
    rate). This is a genuinely different code path from
    `discovery_eval/run_eval.py`, which computes its own, separate
    "Duplicates removed" figure (discovery_eval/report.py) entirely
    in-memory and -- confirmed by grep -- never calls
    `create_discovery_run_record` or touches `DiscoveryRunRecord` at all
    (matching its own module docstring: "zero database writes"). The
    task's "one duplicate test run reported 5 duplicate-flagged results"
    evidence, using that exact "duplicate-flagged" terminology, matches
    discovery_eval's own vocabulary (`outcome == "duplicate"`,
    `duplicate_businesses`), not the live analytics path's. A 0.0%
    duplicate_removal_rate_pct on the live `/analytics/discovery`
    endpoint at a given moment is therefore consistent with there
    genuinely having been zero in-batch duplicates among whichever
    `DiscoveryRunRecord`s were persisted and queried at that time -- a
    property of that specific dataset, not a metric bug. This is
    exactly the "mixing benchmark metrics with runtime metrics" trap
    Task 5 explicitly warns about, caught before being misdiagnosed as
    a bug."""

    def test_duplicate_removal_rate_is_correctly_nonzero_when_duplicates_exist(
        self, db_session, sample_org
    ):
        from application.observability.metrics_service import AnalyticsService
        from application.observability.repository import create_discovery_run_record

        create_discovery_run_record(
            db_session,
            organization_id=sample_org.id,
            query="electronics stores",
            category="electronics",
            location="Bengaluru",
            requested_limit=10,
            businesses_returned=10,
            businesses_missing_website=0,
            websites_resolved_via_fallback=0,
            duplicates_removed=5,
            validated_leads=5,
            duration_ms=1000,
        )

        metrics = AnalyticsService(db_session).get_discovery_metrics(
            organization_id=sample_org.id
        )

        assert metrics.duplicate_removal_rate_pct == pytest.approx(50.0), (
            "the live, DB-backed duplicate-removal metric did not correctly reflect "
            "5 real duplicates out of 10 businesses_returned"
        )

    def test_discovery_eval_harness_never_writes_a_discovery_run_record(self):
        """Confirms the two 'duplicate' figures are drawn from disjoint
        data sources, so a 0% live rate does not contradict a nonzero
        harness-reported rate from a separate, unpersisted run."""
        import inspect

        from discovery_eval import run_eval

        source = inspect.getsource(run_eval)
        assert "create_discovery_run_record" not in source
        assert "DiscoveryRunRecord" not in source
