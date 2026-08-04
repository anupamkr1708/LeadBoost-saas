"""
Unit tests for application.discovery.false_positive (Sprint 3).
"""

from application.discovery import canonicalization as canon
from application.discovery import features as features_model
from application.discovery.evidence import Evidence, EvidenceBundle, EvidenceType
from application.discovery.false_positive import (
    DomainObservationRegistry,
    evaluate_candidate,
    redirect_domain_mismatch_signal,
    unsupported_candidate_signal,
)
from application.discovery.organization import IdentityCandidate


def _feature_set(domain, *, brand=0.0, location=0.0, agreement=0.0, reachability=1.0):
    def _f(feature_id, value):
        return features_model.ExtractedFeature(
            feature_id=feature_id, normalized_value=value, raw_value=None, confidence_impact=0.0,
            supporting_evidence=(), source_providers=(), explanation=f"{feature_id.value}={value}",
        )

    return features_model.FeatureSet(
        domain=domain,
        features=(
            _f(features_model.FeatureId.BRAND_SIMILARITY, brand),
            _f(features_model.FeatureId.LOCATION_CONSISTENCY, location),
            _f(features_model.FeatureId.PROVIDER_AGREEMENT, agreement),
            _f(features_model.FeatureId.REACHABILITY, reachability),
        ),
    )


def _candidate(domain, *, feature_set=None, evidence_bundle=None):
    return IdentityCandidate(
        provider=("overpass",),
        normalized_domain=domain,
        canonical_domain=canon.canonicalize(domain),
        website=f"https://{domain}",
        supporting_evidence=evidence_bundle or EvidenceBundle(),
        provider_confidence=0.5,
        verification_results=None,
        feature_store_entry=feature_set,
        confidence=0.3,
        identity_quality=0.0,
    )


# -- unsupported_candidate_signal ------------------------------------------------


def test_unsupported_candidate_fires_when_every_axis_is_zero():
    candidate = _candidate("parked.com", feature_set=_feature_set("parked.com", brand=0.0, location=0.0, agreement=0.0, reachability=1.0))
    signal = unsupported_candidate_signal(candidate)
    assert signal is not None
    assert signal.code == "unsupported_candidate"
    assert signal.severity == "high"


def test_unsupported_candidate_does_not_fire_with_brand_support():
    candidate = _candidate("nike.com", feature_set=_feature_set("nike.com", brand=0.9, location=0.0, agreement=0.0))
    assert unsupported_candidate_signal(candidate) is None


def test_unsupported_candidate_does_not_fire_with_location_support():
    candidate = _candidate("x.com", feature_set=_feature_set("x.com", brand=0.0, location=1.0, agreement=0.0))
    assert unsupported_candidate_signal(candidate) is None


def test_unsupported_candidate_does_not_fire_with_provider_agreement_support():
    candidate = _candidate("x.com", feature_set=_feature_set("x.com", brand=0.0, location=0.0, agreement=0.5))
    assert unsupported_candidate_signal(candidate) is None


def test_unsupported_candidate_does_not_fire_when_unreachable():
    # Already rejected upstream by website_validator -- nothing new to
    # say about it here.
    candidate = _candidate("dead.com", feature_set=_feature_set("dead.com", reachability=0.0))
    assert unsupported_candidate_signal(candidate) is None


def test_unsupported_candidate_none_without_a_feature_set():
    candidate = _candidate("x.com", feature_set=None)
    assert unsupported_candidate_signal(candidate) is None


# -- redirect_domain_mismatch_signal --------------------------------------------


def test_redirect_domain_mismatch_fires_when_final_domain_is_unrelated():
    bundle = EvidenceBundle()
    bundle.add(Evidence(type=EvidenceType.REACHABILITY, provider="overpass", confidence_delta=0.15, reason="ok", source="https://totally-unrelated.net/"))
    candidate = _candidate("parked-domain.com", evidence_bundle=bundle)
    signal = redirect_domain_mismatch_signal(candidate)
    assert signal is not None
    assert signal.code == "redirect_domain_mismatch"


def test_redirect_domain_mismatch_none_when_same_registrable_domain():
    bundle = EvidenceBundle()
    bundle.add(Evidence(type=EvidenceType.REACHABILITY, provider="overpass", confidence_delta=0.15, reason="ok", source="https://www.nike.com/"))
    candidate = _candidate("nike.com", evidence_bundle=bundle)
    assert redirect_domain_mismatch_signal(candidate) is None


def test_redirect_domain_mismatch_none_without_reachability_evidence():
    candidate = _candidate("nike.com", evidence_bundle=EvidenceBundle())
    assert redirect_domain_mismatch_signal(candidate) is None


# -- DomainObservationRegistry / cross_tenant_signal ---------------------------


def test_cross_tenant_signal_none_below_minimum_business_count():
    registry = DomainObservationRegistry()
    for i in range(2):
        registry.observe("directory.com", f"business-{i}", 0.0)
    assert registry.cross_tenant_signal("directory.com") is None


def test_cross_tenant_signal_fires_with_enough_weak_match_observations():
    registry = DomainObservationRegistry()
    for i in range(6):
        registry.observe("directory.com", f"business-{i}", 0.0)
    signal = registry.cross_tenant_signal("directory.com")
    assert signal is not None
    assert signal.code == "cross_tenant_generic_domain"


def test_cross_tenant_signal_none_when_matches_are_genuinely_strong():
    registry = DomainObservationRegistry()
    for i in range(6):
        registry.observe("nike.com", f"business-{i}", 0.9)
    assert registry.cross_tenant_signal("nike.com") is None


def test_cross_tenant_signal_scoped_per_domain():
    registry = DomainObservationRegistry()
    for i in range(6):
        registry.observe("directory.com", f"business-{i}", 0.0)
    assert registry.cross_tenant_signal("unrelated.com") is None


def test_cross_tenant_signal_repeated_business_name_does_not_inflate_distinct_count():
    registry = DomainObservationRegistry()
    for _ in range(6):
        registry.observe("directory.com", "same-business", 0.0)
    # Only ONE distinct business observed -- below the minimum threshold
    # even though `observe` was called 6 times.
    assert registry.cross_tenant_signal("directory.com") is None


def test_domain_observation_registry_to_dict_shape():
    registry = DomainObservationRegistry()
    registry.observe("a.com", "biz-1", 0.5)
    result = registry.to_dict()
    assert result["a.com"]["distinct_businesses"] == 1
    assert result["a.com"]["total"] == 1


# -- evaluate_candidate (orchestration) -----------------------------------------


def test_evaluate_candidate_returns_only_fired_signals_in_fixed_order():
    registry = DomainObservationRegistry()
    candidate = _candidate("parked.com", feature_set=_feature_set("parked.com"))
    signals = evaluate_candidate(candidate, registry)
    assert [s.code for s in signals] == ["unsupported_candidate"]


def test_evaluate_candidate_returns_empty_tuple_for_clean_candidate():
    registry = DomainObservationRegistry()
    candidate = _candidate("nike.com", feature_set=_feature_set("nike.com", brand=0.9))
    assert evaluate_candidate(candidate, registry) == ()


def test_rejection_signal_to_dict_has_expected_keys():
    candidate = _candidate("parked.com", feature_set=_feature_set("parked.com"))
    signal = unsupported_candidate_signal(candidate)
    assert set(signal.to_dict().keys()) == {"code", "severity", "description", "evidence"}
