"""
Unit tests for application.discovery.evidence.

Everything in evidence.py is pure (no I/O), so these run against plain
constructed inputs -- no aiohttp session, no DB, no event loop required
beyond what a couple of async provider stubs need in
test_website_resolver_evidence.py alongside this file.
"""

from application.discovery.evidence import (
    CandidatePool,
    Evidence,
    EvidenceBundle,
    EvidenceCategory,
    EvidenceType,
    LightweightVerificationEngine,
    brand_identity_evidence,
    location_corroboration_evidence,
    provider_agreement_evidence,
    verification_evidence,
)
from application.discovery.website_validator import ValidationOutcome


# -- EvidenceBundle --------------------------------------------------------


def test_bundle_confidence_sums_deltas_and_clamps_to_zero_one():
    bundle = EvidenceBundle()
    bundle.add(Evidence(EvidenceType.PROVIDER_FOUND, "overpass", 0.30, "found"))
    bundle.add(Evidence(EvidenceType.BRAND_MATCH, "overpass", 0.25, "matched"))
    assert bundle.confidence == 0.55


def test_bundle_confidence_never_exceeds_one():
    bundle = EvidenceBundle()
    for _ in range(10):
        bundle.add(Evidence(EvidenceType.PROVIDER_AGREEMENT, "x", 0.25, "agree"))
    assert bundle.confidence == 1.0


def test_bundle_confidence_never_drops_below_zero():
    bundle = EvidenceBundle()
    bundle.add(Evidence(EvidenceType.PROVIDER_FOUND, "x", 0.10, "found"))
    bundle.add(Evidence(EvidenceType.PROVIDER_DISAGREEMENT, "x", -0.50, "disagree"))
    assert bundle.confidence == 0.0


def test_bundle_add_ignores_none_without_fabricating_an_entry():
    bundle = EvidenceBundle()
    bundle.add(None)
    assert bundle.items == []
    assert bundle.confidence == 0.0


def test_bundle_explain_is_human_readable_and_ordered():
    bundle = EvidenceBundle()
    bundle.add(Evidence(EvidenceType.PROVIDER_FOUND, "serper", 0.30, "found it"))
    bundle.add(Evidence(EvidenceType.HTTPS, "serper", 0.03, "uses https"))
    lines = bundle.explain()
    assert len(lines) == 2
    assert "found it" in lines[0]
    assert "uses https" in lines[1]


def test_bundle_by_category_filters_correctly():
    bundle = EvidenceBundle()
    bundle.add(Evidence(EvidenceType.PROVIDER_FOUND, "x", 0.30, "found"))
    bundle.add(Evidence(EvidenceType.BRAND_MATCH, "x", 0.20, "brand"))
    assert len(bundle.by_category(EvidenceCategory.PROVIDER)) == 1
    assert len(bundle.by_category(EvidenceCategory.IDENTITY)) == 1
    assert bundle.by_category(EvidenceCategory.LOCATION) == []


# -- brand_identity_evidence ------------------------------------------------


def test_brand_identity_evidence_present_for_exact_match():
    ev = brand_identity_evidence("Nike", "nike.com", "overpass")
    assert ev is not None
    assert ev.type is EvidenceType.BRAND_MATCH
    assert ev.confidence_delta > 0


def test_brand_identity_evidence_absent_when_no_match():
    ev = brand_identity_evidence("Regal Shoes", "regmovies.com", "serper")
    assert ev is None


# -- location_corroboration_evidence ----------------------------------------


def test_location_evidence_full_credit_for_matching_address():
    ev = location_corroboration_evidence(
        "Mumbai", "metroshoes.com", "123 MG Road, Mumbai", "overpass"
    )
    assert ev is not None
    assert ev.confidence_delta == 0.15


def test_location_evidence_half_credit_for_domain_only_match():
    ev = location_corroboration_evidence("Mumbai", "metroshoesmumbai.com", None, "serper")
    assert ev is not None
    assert ev.confidence_delta == 0.075


def test_location_evidence_absent_when_nothing_corroborates():
    ev = location_corroboration_evidence("Mumbai", "metroshoes.com", None, "serper")
    assert ev is None


# -- verification_evidence --------------------------------------------------


def test_verification_evidence_empty_for_failed_outcome():
    outcome = ValidationOutcome(ok=False, reason="unreachable_http_500")
    assert verification_evidence(outcome, "overpass") == []


def test_verification_evidence_full_set_for_passing_root_https_url():
    outcome = ValidationOutcome(ok=True, normalized_url="https://nike.com/")
    items = verification_evidence(outcome, "overpass")
    types = {e.type for e in items}
    assert EvidenceType.REACHABILITY in types
    assert EvidenceType.HTTPS in types
    assert EvidenceType.CANONICAL_URL in types


def test_verification_evidence_no_https_bonus_for_http_url():
    outcome = ValidationOutcome(ok=True, normalized_url="http://example.com/")
    items = verification_evidence(outcome, "overpass")
    types = {e.type for e in items}
    assert EvidenceType.HTTPS not in types


def test_verification_evidence_no_canonical_bonus_for_subpage():
    outcome = ValidationOutcome(ok=True, normalized_url="https://example.com/store/locations")
    items = verification_evidence(outcome, "overpass")
    types = {e.type for e in items}
    assert EvidenceType.CANONICAL_URL not in types


def test_lightweight_verification_engine_matches_bare_function():
    outcome = ValidationOutcome(ok=True, normalized_url="https://nike.com/")
    engine = LightweightVerificationEngine()
    assert len(engine.verify(outcome, "overpass")) == len(verification_evidence(outcome, "overpass"))


# -- CandidatePool / provider agreement -------------------------------------


def test_add_candidate_seeds_provider_found_evidence():
    pool = CandidatePool("Nike", "Mumbai")
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    assert candidate.domain == "nike.com"
    assert candidate.confidence == 0.30  # PROVIDER_FOUND_WEIGHT alone


def test_best_validated_returns_first_validated_in_insertion_order():
    pool = CandidatePool("Nike", "Mumbai")
    pool.add_candidate(url="https://bad.com", provider="overpass", validated=False, rejection_reason="x")
    good = pool.add_candidate(url="https://nike.com", provider="serper", validated=True)
    assert pool.best_validated() is good


def test_best_validated_returns_none_when_pool_empty_or_all_invalid():
    pool = CandidatePool("Nike", "Mumbai")
    assert pool.best_validated() is None
    pool.add_candidate(url="https://bad.com", provider="overpass", validated=False)
    assert pool.best_validated() is None


def test_provider_agreement_boosts_both_candidates_confidence():
    pool = CandidatePool("Nike", "Mumbai")
    a = pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="timeout")
    b = pool.add_candidate(url="https://nike.com", provider="serper", validated=True)

    for ev in provider_agreement_evidence(pool, b):
        b.evidence.add(ev)

    assert any(e.type is EvidenceType.PROVIDER_AGREEMENT for e in b.evidence.items)
    # provider_agreement_evidence mutates the *other* candidate directly
    # (mutual corroboration), so `a` should reflect it too.
    assert any(e.type is EvidenceType.PROVIDER_AGREEMENT for e in a.evidence.items)


def test_provider_disagreement_penalizes_only_the_new_candidate():
    pool = CandidatePool("Nike", "Mumbai")
    a = pool.add_candidate(url="https://nike-outlet.com", provider="overpass", validated=False, rejection_reason="timeout")
    b = pool.add_candidate(url="https://nike.com", provider="serper", validated=True)

    penalties = provider_agreement_evidence(pool, b)
    assert any(e.type is EvidenceType.PROVIDER_DISAGREEMENT for e in penalties)
    assert not any(e.type is EvidenceType.PROVIDER_DISAGREEMENT for e in a.evidence.items)


def test_provider_agreement_ignores_same_provider_and_missing_domains():
    pool = CandidatePool("Nike", "Mumbai")
    pool.add_candidate(url="https://nike.com", provider="serper", validated=True)
    same_provider = pool.add_candidate(url="https://nike.com", provider="serper", validated=True)
    assert provider_agreement_evidence(pool, same_provider) == []


def test_pool_summary_reports_every_candidate_with_confidence_and_evidence():
    pool = CandidatePool("Nike", "Mumbai")
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    summary = pool.summary()
    assert len(summary) == 1
    assert summary[0]["provider"] == "overpass"
    assert summary[0]["confidence"] == 0.30
    assert isinstance(summary[0]["evidence"], list) and summary[0]["evidence"]
