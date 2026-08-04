"""
Unit tests for application.discovery.verification (Sprint 2B).

Verifiers operate only on FeatureSet, so tests build FeatureSet/
ExtractedFeature values directly -- no CandidatePool, no Evidence, no
BusinessCandidate needed anywhere in this file. That absence is itself
part of what's being tested: this module never imports
application.discovery.evidence at all (see
test_module_never_imports_evidence_or_identity below), so a Verifier is
structurally unable to inspect raw provider output or evidence, not just
asked nicely not to.
"""

import application.discovery.verification as verification

from application.discovery.features import ExtractedFeature, FeatureId, FeatureSet
from application.discovery.verification import (
    BusinessVerifier,
    DomainVerifier,
    IdentityVerifier,
    LocationVerifier,
    Verifier,
    VerificationPipeline,
    VerificationResult,
    VerificationStatus,
    WebsiteVerifier,
)


def _feature(feature_id, normalized_value, confidence_impact=0.0):
    return ExtractedFeature(
        feature_id=feature_id,
        normalized_value=normalized_value,
        raw_value=None,
        confidence_impact=confidence_impact,
        supporting_evidence=(),
        source_providers=(),
        explanation="test feature",
    )


def _feature_set(**overrides):
    """A fully-populated, fully-verified-looking FeatureSet, with any
    FeatureId overridden by `overrides` (feature name -> normalized_value)."""
    defaults = {fid: 0.0 for fid in FeatureId}
    defaults[FeatureId.REACHABILITY] = 1.0
    defaults[FeatureId.HTTPS] = 1.0
    defaults[FeatureId.CANONICAL_DOMAIN] = 1.0
    defaults[FeatureId.DOMAIN_QUALITY] = 1.0
    defaults[FeatureId.BRAND_SIMILARITY] = 1.0
    defaults[FeatureId.LOCATION_CONSISTENCY] = 1.0
    defaults[FeatureId.PROVIDER_AGREEMENT] = 0.5
    defaults[FeatureId.BUSINESS_COMPLETENESS] = 1.0
    defaults[FeatureId.CONTACT_COMPLETENESS] = 0.667
    defaults[FeatureId.VERIFICATION_READINESS] = 1.0
    defaults[FeatureId.EVIDENCE_DENSITY] = 1.0
    defaults[FeatureId.WEBSITE_STRUCTURE] = None
    by_name = {fid.name: fid for fid in FeatureId}
    for key, value in overrides.items():
        defaults[by_name[key]] = value
    features = tuple(_feature(fid, val) for fid, val in defaults.items())
    return FeatureSet(domain="example.com", features=features)


# -- Module never imports evidence.py or identity.py (structural check) ----


def test_module_never_imports_evidence_or_identity():
    assert "evidence" not in verification.__dict__
    assert "identity" not in verification.__dict__


# -- DomainVerifier ------------------------------------------------------------


def test_domain_verifier_passes_when_reachable():
    result = DomainVerifier().verify(_feature_set(REACHABILITY=1.0))
    assert result.status is VerificationStatus.PASSED
    assert result.critical is True


def test_domain_verifier_fails_when_not_reachable():
    result = DomainVerifier().verify(_feature_set(REACHABILITY=0.0))
    assert result.status is VerificationStatus.FAILED


# -- BusinessVerifier (advisory, never critical) ------------------------------


def test_business_verifier_is_not_critical():
    assert BusinessVerifier.critical is False


def test_business_verifier_passes_when_complete():
    result = BusinessVerifier().verify(_feature_set())
    assert result.status is VerificationStatus.PASSED


def test_business_verifier_inconclusive_not_failed_when_incomplete():
    result = BusinessVerifier().verify(_feature_set(BUSINESS_COMPLETENESS=0.0))
    # Missing data degrades to INCONCLUSIVE, never FAILED -- a thin
    # business record must not read as a rejection.
    assert result.status is VerificationStatus.INCONCLUSIVE


# -- LocationVerifier (advisory) ----------------------------------------------


def test_location_verifier_inconclusive_when_absent():
    result = LocationVerifier().verify(_feature_set(LOCATION_CONSISTENCY=0.0))
    assert result.status is VerificationStatus.INCONCLUSIVE
    assert result.critical is False


# -- WebsiteVerifier (always honest INCONCLUSIVE today) -----------------------


def test_website_verifier_is_always_inconclusive_today():
    result = WebsiteVerifier().verify(_feature_set())
    assert result.status is VerificationStatus.INCONCLUSIVE
    assert result.confidence_contribution == 0.0


# -- IdentityVerifier ----------------------------------------------------------


def test_identity_verifier_passes_on_brand_match():
    fset = _feature_set(BRAND_SIMILARITY=0.8, LOCATION_CONSISTENCY=0.0, PROVIDER_AGREEMENT=0.0)
    result = IdentityVerifier().verify(fset)
    assert result.status is VerificationStatus.PASSED
    assert "brand" in result.reason.lower() or "name" in result.reason.lower()


def test_identity_verifier_passes_on_location_when_no_brand_match():
    fset = _feature_set(BRAND_SIMILARITY=0.0, LOCATION_CONSISTENCY=1.0, PROVIDER_AGREEMENT=0.0)
    result = IdentityVerifier().verify(fset)
    assert result.status is VerificationStatus.PASSED
    assert "location" in result.reason.lower()


def test_identity_verifier_passes_on_provider_agreement_as_last_resort():
    fset = _feature_set(BRAND_SIMILARITY=0.0, LOCATION_CONSISTENCY=0.0, PROVIDER_AGREEMENT=0.5)
    result = IdentityVerifier().verify(fset)
    assert result.status is VerificationStatus.PASSED
    assert "provider" in result.reason.lower()


def test_identity_verifier_fails_when_nothing_supports_the_match():
    fset = _feature_set(BRAND_SIMILARITY=0.0, LOCATION_CONSISTENCY=0.0, PROVIDER_AGREEMENT=0.0)
    result = IdentityVerifier().verify(fset)
    assert result.status is VerificationStatus.FAILED
    assert result.critical is True


# -- VerificationBundle / overall_passed --------------------------------------


def test_overall_passed_true_when_all_critical_verifiers_pass():
    bundle = VerificationPipeline().run(_feature_set())
    assert bundle.overall_passed is True


def test_overall_passed_false_when_domain_verifier_fails():
    bundle = VerificationPipeline().run(_feature_set(REACHABILITY=0.0))
    assert bundle.overall_passed is False


def test_overall_passed_unaffected_by_non_critical_failures():
    """WebsiteVerifier is always INCONCLUSIVE, LocationVerifier can be
    INCONCLUSIVE -- neither should ever block overall_passed on their
    own."""
    bundle = VerificationPipeline().run(_feature_set(LOCATION_CONSISTENCY=0.0))
    location_result = bundle.by_verifier("location_verification")
    assert location_result.status is not VerificationStatus.PASSED
    assert bundle.overall_passed is True  # domain + identity still pass


def test_overall_passed_false_with_no_verifiers_run():
    from application.discovery.verification import VerificationBundle

    empty_bundle = VerificationBundle(results=())
    assert empty_bundle.overall_passed is False


def test_by_verifier_returns_none_for_unknown_id():
    bundle = VerificationPipeline().run(_feature_set())
    assert bundle.by_verifier("does_not_exist") is None


def test_bundle_to_dict_is_json_serializable_shape():
    bundle = VerificationPipeline().run(_feature_set())
    payload = bundle.to_dict()
    assert isinstance(payload["overall_passed"], bool)
    assert isinstance(payload["results"], list)
    assert all(isinstance(r["critical"], bool) for r in payload["results"])


# -- Strategy pattern / extensibility (register without modifying) ----------


class _AlwaysPassCriticalVerifier(Verifier):
    id = "always_pass_critical"
    critical = True

    def verify(self, features: FeatureSet) -> VerificationResult:
        return self._result(VerificationStatus.PASSED, 1.0, "always passes", ())


class _AlwaysFailCriticalVerifier(Verifier):
    id = "always_fail_critical"
    critical = True

    def verify(self, features: FeatureSet) -> VerificationResult:
        return self._result(VerificationStatus.FAILED, 0.0, "always fails", ())


def test_register_adds_a_verifier_without_touching_existing_ones():
    pipeline = VerificationPipeline()
    original_count = len(pipeline.verifiers)

    pipeline.register(_AlwaysPassCriticalVerifier())

    assert len(pipeline.verifiers) == original_count + 1
    bundle = pipeline.run(_feature_set())
    assert bundle.by_verifier("always_pass_critical").status is VerificationStatus.PASSED
    # Every pre-existing verifier's own id/behavior is untouched.
    assert bundle.by_verifier("domain_verification").status is VerificationStatus.PASSED


def test_a_newly_registered_critical_verifier_correctly_gates_overall_passed():
    """This is the correctness property that matters for extensibility:
    overall_passed must reflect a verifier registered *after*
    construction, not just the module's original default set."""
    pipeline = VerificationPipeline()
    pipeline.register(_AlwaysFailCriticalVerifier())

    bundle = pipeline.run(_feature_set())

    assert bundle.overall_passed is False


def test_custom_verifier_list_replaces_defaults_entirely():
    pipeline = VerificationPipeline(verifiers=[_AlwaysPassCriticalVerifier()])
    bundle = pipeline.run(_feature_set())
    assert len(bundle.results) == 1
    assert bundle.overall_passed is True
