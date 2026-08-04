"""
Unit tests for application.discovery.digital_identity (Sprint 2C).

Mirrors the approach of test_features.py/test_verification.py/
test_confidence.py before it: everything in digital_identity.py is pure
(no I/O), so these run against a real, small, end-to-end GenericIdentityResolver
result (built from plain constructed CandidatePool/BusinessCandidate
inputs, exactly like test_identity.py already does) rather than mocks --
the whole point of this module is that it reads *real* already-computed
BusinessIdentity/FeatureStore/VerificationBundle/ConfidenceTrace data, so
tests should exercise that real data, not a stand-in for it.

Covers, per the Sprint 2C brief: VerifiedDigitalIdentity construction,
Identity Completeness, Verification Quality (separate from Verification
Passed), the Risk Model (never blocking), the Consensus Engine (broader
than providers alone), Digital Identity Quality, Digital Identity Status,
canonical-object JSON-serializability, and backward compatibility (no
existing Sprint 1/2A/2B behavior changes -- covered by the additional
tests appended to test_identity.py and test_website_resolver_evidence.py).
"""

import json

import pytest

from application.discovery.digital_identity import (
    ConsensusEngine,
    DigitalIdentityBuilder,
    DigitalIdentityStatus,
    RiskAssessment,
    VerificationTrace,
    VerifiedDigitalIdentity,
    assess_digital_identity_quality,
    assess_identity_completeness,
    assess_risk,
    assess_verification_quality,
    empty_consensus_record,
)
from application.discovery.dto import BusinessCandidate
from application.discovery.evidence import (
    CandidatePool,
    brand_identity_evidence,
    location_corroboration_evidence,
    verification_evidence,
)
from application.discovery.identity import GenericIdentityResolver
from application.discovery.verification import VerificationStatus
from application.discovery.website_validator import ValidationOutcome


# -- Shared fixtures ----------------------------------------------------------


def _fully_verified_business_identity(
    business_name="Nike", location="Mumbai", address=None, phone=None, latitude=None, longitude=None
):
    """Builds a real, end-to-end BusinessIdentity through
    GenericIdentityResolver -- exactly the object DigitalIdentityBuilder
    is designed to consume -- rather than a hand-built stand-in."""
    pool = CandidatePool(business_name, location)
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    candidate.evidence.add(brand_identity_evidence(business_name, candidate.domain, candidate.provider))
    if address:
        candidate.evidence.add(location_corroboration_evidence(location, candidate.domain, address, candidate.provider))
    outcome = ValidationOutcome(ok=True, normalized_url="https://nike.com/")
    candidate.evidence.extend(verification_evidence(outcome, candidate.provider))

    business = BusinessCandidate(
        name=business_name, website="https://nike.com", address=address, phone=phone, latitude=latitude, longitude=longitude
    )
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, location, chosen=candidate, chosen_url="https://nike.com/")
    return business_identity, business


def _no_candidate_business_identity(business_name="Ghost Biz", location="Nowhere"):
    pool = CandidatePool(business_name, location)
    business = BusinessCandidate(name=business_name)
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, location, chosen=None, chosen_url=None)
    return business_identity, business


def _unverified_business_identity(business_name="Nike", location="Mumbai"):
    """A candidate exists but never passed reachability -- i.e. a real
    "unverified" BusinessIdentity, not merely "no candidates"."""
    pool = CandidatePool(business_name, location)
    candidate = pool.add_candidate(
        url="https://nike.com", provider="overpass", validated=False, rejection_reason="request_timed_out"
    )
    candidate.evidence.add(brand_identity_evidence(business_name, candidate.domain, candidate.provider))
    business = BusinessCandidate(name=business_name, website="https://nike.com")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, location, chosen=None, chosen_url=None)
    return business_identity, business


def _disagreement_business_identity():
    """Two providers disagree on the domain, and the winning candidate has
    no brand/location match at all -- a genuinely weak, risky identity."""
    pool = CandidatePool("XYZ Corp", "Delhi")
    winner = pool.add_candidate(url="https://random-unrelated.com", provider="overpass", validated=True)
    pool.add_candidate(url="https://another-site.com", provider="serper", validated=True)
    business = BusinessCandidate(name="XYZ Corp")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(
        business, pool, "Delhi", chosen=winner, chosen_url="https://random-unrelated.com/"
    )
    return business_identity, business


# ---------------------------------------------------------------------------
# VerifiedDigitalIdentity / DigitalIdentityBuilder end-to-end
# ---------------------------------------------------------------------------


def test_builder_produces_verified_digital_identity_for_a_strong_match():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai", phone="555-1234")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert isinstance(vdi, VerifiedDigitalIdentity)
    assert vdi.selected_website == business_identity.selected_website == "https://nike.com/"
    assert vdi.business_identity is business_identity  # reference, never a copy
    assert vdi.status is DigitalIdentityStatus.VERIFIED_STRONG
    assert vdi.risk_assessment.risk_level == "none"
    assert vdi.quality.overall_score > 0.7


def test_builder_never_crashes_and_reports_no_identity_when_pool_is_empty():
    business_identity, business = _no_candidate_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.status is DigitalIdentityStatus.NO_IDENTITY
    assert vdi.selected_website is None
    assert vdi.quality.overall_score == 0.0
    # Risk must still explain, never crash or silently omit.
    assert any(i.code == "no_website" for i in vdi.risk_assessment.indicators)
    assert vdi.risk_assessment.risk_level == "high"


def test_builder_reports_unverified_status_when_candidate_never_validated():
    business_identity, business = _unverified_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert business_identity.verification_state == "unverified"
    assert vdi.status is DigitalIdentityStatus.UNVERIFIED


def test_verified_digital_identity_to_dict_is_fully_json_serializable():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai", phone="555-1234")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    payload = vdi.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["status"] == "verified_strong"
    assert payload["selected_website"] == "https://nike.com/"
    assert isinstance(payload["business_identity"], dict)
    assert isinstance(payload["consensus"], dict)
    assert isinstance(payload["identity_completeness"], dict)
    assert isinstance(payload["verification_quality"], dict)
    assert isinstance(payload["risk_assessment"], dict)
    assert isinstance(payload["quality"], dict)


def test_verified_digital_identity_explain_mentions_status_and_selected_website():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    lines = " ".join(vdi.explain())
    assert "verified_strong" in lines
    assert "nike.com" in lines


def test_feature_store_is_reused_not_recomputed():
    """DigitalIdentityBuilder must reuse BusinessIdentity's own
    FeatureStore object -- never a fresh, independently-extracted copy."""
    business_identity, business = _fully_verified_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.feature_store is business_identity.feature_store


def test_evidence_bundle_and_confidence_trace_are_reused_not_recomputed():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.evidence_bundle is business_identity.evidence_bundle
    assert vdi.confidence_trace is business_identity.decision_trace.confidence_trace
    assert vdi.verification_bundle is business_identity.decision_trace.verification_bundle


# ---------------------------------------------------------------------------
# Verification Trace (distinct from the selected-only Verification Bundle)
# ---------------------------------------------------------------------------


def test_verification_trace_covers_every_candidate_group_not_just_selected():
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert isinstance(vdi.verification_trace, VerificationTrace)
    domains = {domain for domain, _ in vdi.verification_trace.bundles}
    assert domains == {"random-unrelated.com", "another-site.com"}
    assert vdi.verification_trace.selected_domain == "random-unrelated.com"
    # The rejected group still has a real, non-None verification bundle.
    rejected_bundle = vdi.verification_trace.by_domain("another-site.com")
    assert rejected_bundle is not None


def test_verification_trace_selected_entry_matches_the_reused_bundle_exactly():
    """The selected domain's entry in VerificationTrace must be the exact
    same object as the top-level `verification_bundle` -- never a
    second, independently-recomputed bundle that could drift."""
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    selected_domain = vdi.verification_trace.selected_domain
    assert vdi.verification_trace.by_domain(selected_domain) is vdi.verification_bundle


def test_verification_trace_explain_marks_the_selected_domain():
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    lines = vdi.verification_trace.explain()
    assert any("(selected)" in line for line in lines)


def test_verification_trace_to_dict_is_json_serializable():
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)
    json.dumps(vdi.verification_trace.to_dict())


# ---------------------------------------------------------------------------
# Identity Completeness
# ---------------------------------------------------------------------------


def test_completeness_full_marks_when_everything_is_present():
    business_identity, business = _fully_verified_business_identity(
        address="1 Park St, Mumbai", phone="555-1234", latitude=19.07, longitude=72.87
    )
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    completeness = vdi.identity_completeness
    assert completeness.has_website is True
    assert completeness.has_phone is True
    assert completeness.has_address is True
    assert completeness.has_coordinates is True
    assert completeness.overall_score > 0.8


def test_completeness_reports_missing_fields_without_fabricating():
    business_identity, business = _fully_verified_business_identity()  # no address/phone/coordinates
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    completeness = vdi.identity_completeness
    assert completeness.has_phone is False
    assert completeness.has_address is False
    assert completeness.has_coordinates is False
    assert any("phone" in m for m in completeness.missing_information)
    assert any("address" in m for m in completeness.missing_information)
    assert any("coordinates" in m for m in completeness.missing_information)


def test_completeness_no_candidates_is_zero_and_explicit_not_crashed():
    business_identity, business = _no_candidate_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    completeness = vdi.identity_completeness
    assert completeness.has_website is False
    assert completeness.provider_support_ratio == 0.0
    assert completeness.verification_coverage == 0.0
    assert completeness.overall_score < 0.3


def test_completeness_provider_support_ratio_reflects_real_pool_data():
    """Two providers converge on the same domain -- provider_support_ratio
    should be 1.0 (both distinct providers seen support the winner), not
    an invented fixed denominator."""
    pool = CandidatePool("Nike", "Mumbai")
    c1 = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    c2 = pool.add_candidate(url="https://nike.com/about", provider="serper", validated=True)
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, "Mumbai", chosen=c1, chosen_url="https://nike.com/")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.identity_completeness.provider_support_ratio == 1.0


def test_assess_identity_completeness_is_a_pure_function_with_no_io():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai")
    selected_domain = business_identity.decision_trace.selected_domain
    selected_group = next(g for g in business_identity.website_candidates if g.domain == selected_domain)
    feature_set = business_identity.feature_store.get(selected_domain)

    completeness = assess_identity_completeness(
        business=business,
        selected_group=selected_group,
        groups=business_identity.website_candidates,
        feature_set=feature_set,
        verification_bundle=business_identity.decision_trace.verification_bundle,
        confidence_trace=business_identity.decision_trace.confidence_trace,
    )
    assert completeness.has_website is True
    assert 0.0 <= completeness.overall_score <= 1.0


# ---------------------------------------------------------------------------
# Verification Quality (distinct from Verification Passed)
# ---------------------------------------------------------------------------


def test_verification_quality_none_bundle_reports_zero_strength_not_a_crash():
    quality = assess_verification_quality(None)
    assert quality.passed is False
    assert quality.strength == 0.0


def test_verification_quality_can_pass_while_reporting_weak_strength():
    """The brief's central example made concrete: a business can PASS
    verification (every critical verifier passed) while VerificationQuality
    still reports meaningfully-less-than-1.0 strength, because advisory
    verifiers (business/location/website) are thin or inconclusive."""
    business_identity, business = _fully_verified_business_identity()  # no address -- LocationVerifier INCONCLUSIVE
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.verification_quality.passed is True
    assert vdi.verification_quality.strength < 1.0
    assert vdi.verification_quality.inconclusive_ratio > 0.0


def test_verification_quality_strength_is_higher_with_more_corroboration():
    weak_identity, weak_business = _fully_verified_business_identity()
    strong_identity, strong_business = _fully_verified_business_identity(address="1 Park St, Mumbai", phone="555-1234")

    weak_quality = assess_verification_quality(weak_identity.decision_trace.verification_bundle)
    strong_quality = assess_verification_quality(strong_identity.decision_trace.verification_bundle)

    assert strong_quality.strength >= weak_quality.strength


def test_verification_quality_reports_failed_reason_when_not_passed():
    """`chosen=None` (WebsiteResolver would never select an unvalidated
    candidate) means `decision_trace.verification_bundle` is None -- but
    the rejected candidate's own FeatureSet is still in feature_store
    (nothing here is ever thrown away), so its real VerificationBundle
    can still be inspected directly."""
    from application.discovery.verification import VerificationPipeline

    business_identity, business = _unverified_business_identity()
    assert business_identity.decision_trace.verification_bundle is None

    feature_set = business_identity.feature_store.get("nike.com")
    bundle = VerificationPipeline().run(feature_set)

    quality = assess_verification_quality(bundle)
    assert quality.passed is False
    assert "did not pass" in quality.explanation


# ---------------------------------------------------------------------------
# Risk Model (never blocks)
# ---------------------------------------------------------------------------


def test_risk_assessment_clean_bill_has_no_indicators():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai", phone="555-1234")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.risk_assessment.risk_level == "none"
    assert vdi.risk_assessment.indicators == ()


def test_risk_flags_conflicting_providers_explicitly():
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    codes = [i.code for i in vdi.risk_assessment.indicators]
    assert "conflicting_providers" in codes


def test_risk_flags_no_website_at_high_severity():
    business_identity, business = _no_candidate_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    no_website = next(i for i in vdi.risk_assessment.indicators if i.code == "no_website")
    assert no_website.severity == "high"


def test_risk_never_changes_the_selected_website_or_confidence():
    """The core "risk never blocks" guarantee: a business identity with
    multiple high-severity risk indicators still reports the exact same
    selected_website/confidence the rest of the pipeline already decided
    on -- risk is purely additive annotation."""
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.risk_assessment.risk_level == "high"
    assert vdi.selected_website == business_identity.selected_website
    assert vdi.confidence_trace.final == business_identity.confidence


def test_risk_indicators_are_json_serializable_and_never_fabricate_evidence():
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    payload = vdi.risk_assessment.to_dict()
    json.dumps(payload)
    for indicator in payload["indicators"]:
        assert isinstance(indicator["evidence"], list)


def test_assess_risk_is_a_pure_function():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai")
    selected_domain = business_identity.decision_trace.selected_domain
    selected_group = next(g for g in business_identity.website_candidates if g.domain == selected_domain)
    feature_set = business_identity.feature_store.get(selected_domain)
    verification_bundle = business_identity.decision_trace.verification_bundle
    completeness = assess_identity_completeness(
        business=business,
        selected_group=selected_group,
        groups=business_identity.website_candidates,
        feature_set=feature_set,
        verification_bundle=verification_bundle,
        confidence_trace=business_identity.decision_trace.confidence_trace,
    )
    quality = assess_verification_quality(verification_bundle)

    risk = assess_risk(
        provider_agreement=business_identity.decision_trace.provider_agreement,
        feature_set=feature_set,
        verification_bundle=verification_bundle,
        completeness=completeness,
        verification_quality=quality,
        selected_group=selected_group,
    )
    assert isinstance(risk, RiskAssessment)
    assert risk.risk_level == "none"


# ---------------------------------------------------------------------------
# Consensus Engine (broader than providers alone)
# ---------------------------------------------------------------------------


def test_consensus_is_strong_for_a_well_corroborated_identity():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai", phone="555-1234")
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.consensus.overall_consensus > 0.5
    assert vdi.consensus.identity_consensus > 0.0
    assert vdi.consensus.verification_consensus > 0.0


def test_consensus_is_weak_for_a_disagreeing_unrelated_candidate():
    business_identity, business = _disagreement_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.consensus.provider_consensus == 0.0
    assert vdi.consensus.level in ("none", "weak")


def test_consensus_single_source_gets_half_credit_not_full_or_zero():
    pool = CandidatePool("Nike", "Mumbai")
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    candidate.evidence.add(brand_identity_evidence("Nike", candidate.domain, candidate.provider))
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    vdi = DigitalIdentityBuilder().build(business_identity, business)
    assert vdi.consensus.provider_consensus == 0.5


def test_consensus_engine_no_candidates_returns_empty_record_not_a_crash():
    business_identity, business = _no_candidate_business_identity()
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert vdi.consensus.overall_consensus == 0.0
    assert vdi.consensus.level == "none"


def test_empty_consensus_record_helper():
    record = empty_consensus_record("test reason")
    assert record.overall_consensus == 0.0
    assert record.explanation == "test reason"


def test_consensus_engine_is_stateless_and_reusable():
    engine = ConsensusEngine()
    business_identity_1, business_1 = _fully_verified_business_identity(address="1 Park St, Mumbai")
    business_identity_2, business_2 = _disagreement_business_identity()

    r1 = engine.compute(
        provider_agreement=business_identity_1.decision_trace.provider_agreement,
        verification_bundle=business_identity_1.decision_trace.verification_bundle,
        feature_set=business_identity_1.feature_store.get(business_identity_1.decision_trace.selected_domain),
        confidence_trace=business_identity_1.decision_trace.confidence_trace,
    )
    r2 = engine.compute(
        provider_agreement=business_identity_2.decision_trace.provider_agreement,
        verification_bundle=business_identity_2.decision_trace.verification_bundle,
        feature_set=business_identity_2.feature_store.get(business_identity_2.decision_trace.selected_domain),
        confidence_trace=business_identity_2.decision_trace.confidence_trace,
    )
    # Calling compute() twice on the same engine must not leak state
    # between calls -- results depend only on the arguments given.
    assert r1.overall_consensus != r2.overall_consensus


# ---------------------------------------------------------------------------
# Digital Identity Quality (reusable rollup)
# ---------------------------------------------------------------------------


def test_quality_high_risk_caps_the_overall_score():
    business_identity, business = _disagreement_business_identity()
    selected_domain = business_identity.decision_trace.selected_domain
    selected_group = next((g for g in business_identity.website_candidates if g.domain == selected_domain), None)
    feature_set = business_identity.feature_store.get(selected_domain)
    verification_bundle = business_identity.decision_trace.verification_bundle
    completeness = assess_identity_completeness(
        business=business,
        selected_group=selected_group,
        groups=business_identity.website_candidates,
        feature_set=feature_set,
        verification_bundle=verification_bundle,
        confidence_trace=business_identity.decision_trace.confidence_trace,
    )
    quality_signal = assess_verification_quality(verification_bundle)
    risk = assess_risk(
        provider_agreement=business_identity.decision_trace.provider_agreement,
        feature_set=feature_set,
        verification_bundle=verification_bundle,
        completeness=completeness,
        verification_quality=quality_signal,
        selected_group=selected_group,
    )
    consensus = ConsensusEngine().compute(
        provider_agreement=business_identity.decision_trace.provider_agreement,
        verification_bundle=verification_bundle,
        feature_set=feature_set,
        confidence_trace=business_identity.decision_trace.confidence_trace,
    )
    assert risk.risk_level == "high"

    overall_quality = assess_digital_identity_quality(completeness, quality_signal, consensus, risk, feature_set)
    assert overall_quality.overall_score <= 0.5


def test_quality_missing_signals_deduplicates_across_sources():
    business_identity, business = _fully_verified_business_identity()  # no address/phone/coordinates
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    # No duplicate strings even though completeness and verification
    # quality can both surface an overlapping "missing" explanation.
    assert len(vdi.quality.missing_signals) == len(set(vdi.quality.missing_signals))


def test_quality_to_dict_is_json_serializable():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai")
    vdi = DigitalIdentityBuilder().build(business_identity, business)
    json.dumps(vdi.quality.to_dict())


# ---------------------------------------------------------------------------
# Digital Identity Status
# ---------------------------------------------------------------------------


def test_status_is_no_identity_when_verification_state_is_no_candidates():
    business_identity, business = _no_candidate_business_identity()
    assert business_identity.verification_state == "no_candidates"
    vdi = DigitalIdentityBuilder().build(business_identity, business)
    assert vdi.status is DigitalIdentityStatus.NO_IDENTITY


def test_status_is_unverified_when_verification_state_is_unverified():
    business_identity, business = _unverified_business_identity()
    assert business_identity.verification_state == "unverified"
    vdi = DigitalIdentityBuilder().build(business_identity, business)
    assert vdi.status is DigitalIdentityStatus.UNVERIFIED


def test_status_is_verified_strong_for_high_quality_low_risk_identity():
    business_identity, business = _fully_verified_business_identity(address="1 Park St, Mumbai", phone="555-1234")
    vdi = DigitalIdentityBuilder().build(business_identity, business)
    assert vdi.status is DigitalIdentityStatus.VERIFIED_STRONG


def test_status_is_verified_weak_or_moderate_for_thin_but_technically_verified_identity():
    """A candidate that passes DomainVerifier/IdentityVerifier (critical)
    purely on cross-provider agreement, with no brand/location match and
    a thin business record, should verify but grade below "strong"."""
    pool = CandidatePool("Acme", "Delhi")
    c1 = pool.add_candidate(url="https://unrelated-domain-x.com", provider="overpass", validated=True)
    c2 = pool.add_candidate(url="https://unrelated-domain-x.com", provider="serper", validated=True)
    business = BusinessCandidate(name="Acme")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(
        business, pool, "Delhi", chosen=c1, chosen_url="https://unrelated-domain-x.com/"
    )
    assert business_identity.verification_state == "verified"  # cross-provider agreement passes IdentityVerifier

    vdi = DigitalIdentityBuilder().build(business_identity, business)
    assert vdi.status in (DigitalIdentityStatus.VERIFIED_WEAK, DigitalIdentityStatus.VERIFIED_MODERATE)


# ---------------------------------------------------------------------------
# Missing Evidence (union of DecisionTrace's own list + completeness gaps)
# ---------------------------------------------------------------------------


def test_missing_evidence_merges_decision_trace_and_completeness_without_duplicates():
    business_identity, business = _fully_verified_business_identity()  # thin record
    vdi = DigitalIdentityBuilder().build(business_identity, business)

    assert len(vdi.missing_evidence) == len(set(vdi.missing_evidence))
    # Every entry from DecisionTrace's own missing_evidence must survive
    # the merge (extended, never replaced).
    for item in business_identity.decision_trace.missing_evidence:
        assert item in vdi.missing_evidence


# ---------------------------------------------------------------------------
# Generic by construction -- no category-specific branching
# ---------------------------------------------------------------------------


def test_digital_identity_quality_is_identical_across_categories():
    pool_a = CandidatePool("Curry House", "Mumbai")
    c_a = pool_a.add_candidate(url="https://curryhouse.com", provider="overpass", validated=True)
    c_a.evidence.add(brand_identity_evidence("Curry House", c_a.domain, c_a.provider))
    business_a = BusinessCandidate(name="Curry House", category="restaurant", website="https://curryhouse.com")

    pool_b = CandidatePool("Curry House", "Mumbai")
    c_b = pool_b.add_candidate(url="https://curryhouse.com", provider="overpass", validated=True)
    c_b.evidence.add(brand_identity_evidence("Curry House", c_b.domain, c_b.provider))
    business_b = BusinessCandidate(name="Curry House", category="law firm", website="https://curryhouse.com")

    resolver = GenericIdentityResolver()
    identity_a = resolver.resolve(business_a, pool_a, "Mumbai", chosen=c_a, chosen_url="https://curryhouse.com/")
    identity_b = resolver.resolve(business_b, pool_b, "Mumbai", chosen=c_b, chosen_url="https://curryhouse.com/")

    vdi_a = DigitalIdentityBuilder().build(identity_a, business_a)
    vdi_b = DigitalIdentityBuilder().build(identity_b, business_b)

    assert vdi_a.quality.overall_score == vdi_b.quality.overall_score
    assert vdi_a.status == vdi_b.status
    assert vdi_a.risk_assessment.risk_level == vdi_b.risk_assessment.risk_level
