"""
Unit tests for application.discovery.confidence (Sprint 2B).

Like test_verification.py, this builds FeatureSet/VerificationBundle
values directly -- confidence propagation reads only those two types (see
module docstring), so no CandidatePool/Evidence/BusinessCandidate is
needed anywhere here.
"""

import application.discovery.confidence as confidence

from application.discovery.confidence import ConfidencePropagationEngine, empty_confidence_trace
from application.discovery.features import ExtractedFeature, FeatureId, FeatureSet
from application.discovery.verification import VerificationPipeline


def _feature(feature_id, normalized_value, confidence_impact=0.0):
    return ExtractedFeature(
        feature_id=feature_id,
        normalized_value=normalized_value,
        raw_value=None,
        confidence_impact=confidence_impact,
        supporting_evidence=(),
        source_providers=(),
        explanation=f"{feature_id.value} test explanation",
    )


def _feature_set(**impacts):
    """A FeatureSet where every FeatureId defaults to
    normalized_value=confidence_impact=0.0, overridable by feature name ->
    confidence_impact (normalized_value is set to 1.0 whenever impact > 0,
    matching how features.py's real features always pair the two)."""
    features = []
    for fid in FeatureId:
        impact = impacts.get(fid.name, 0.0)
        normalized = None if fid is FeatureId.WEBSITE_STRUCTURE and fid.name not in impacts else (1.0 if impact > 0 else 0.0)
        features.append(_feature(fid, normalized, impact))
    return FeatureSet(domain="example.com", features=tuple(features))


engine = ConfidencePropagationEngine()
pipeline = VerificationPipeline()


# -- Stage presence / completeness --------------------------------------------


def test_trace_always_has_all_five_stages_in_order():
    fset = _feature_set()
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    assert [s.stage for s in trace.stages] == ["provider", "website", "business", "identity", "final"]


def test_every_stage_reports_contributed_reduced_and_missing_as_tuples():
    fset = _feature_set(BRAND_SIMILARITY=0.25)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    for stage in trace.stages:
        assert isinstance(stage.contributed, tuple)
        assert isinstance(stage.reduced, tuple)
        assert isinstance(stage.missing, tuple)


# -- Provider stage --------------------------------------------------------------


def test_provider_stage_reflects_agreement_evidence():
    fset = _feature_set(PROVIDER_AGREEMENT=0.4)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    provider_stage = trace.by_stage("provider")
    assert provider_stage.value == 0.4
    assert provider_stage.contributed


def test_provider_stage_reports_missing_when_single_source():
    fset = _feature_set()
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    provider_stage = trace.by_stage("provider")
    assert provider_stage.value == 0.0
    assert provider_stage.missing


# -- Website stage builds on provider ------------------------------------------


def test_website_stage_builds_on_provider_stage():
    fset = _feature_set(PROVIDER_AGREEMENT=0.2, REACHABILITY=0.15, HTTPS=0.03, CANONICAL_DOMAIN=0.02)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    provider_value = trace.by_stage("provider").value
    website_value = trace.by_stage("website").value
    assert website_value > provider_value
    assert website_value == round(provider_value + 0.15 + 0.03 + 0.02, 3)


def test_website_stage_records_reduction_when_domain_verifier_fails():
    fset = _feature_set()  # REACHABILITY impact 0.0 -> normalized 0.0 -> domain verifier fails
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    website_stage = trace.by_stage("website")
    assert website_stage.reduced


def test_website_stage_notes_website_structure_as_missing():
    fset = _feature_set(REACHABILITY=0.15)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    website_stage = trace.by_stage("website")
    structure_explanation = fset.by_id(FeatureId.WEBSITE_STRUCTURE).explanation
    assert structure_explanation in website_stage.missing


# -- Business stage: small, capped, never dominant ----------------------------


def test_business_completeness_bonus_is_small_and_capped():
    fset = _feature_set()
    fset = FeatureSet(
        domain=fset.domain,
        features=tuple(
            _feature(f.feature_id, 1.0 if f.feature_id is FeatureId.BUSINESS_COMPLETENESS else f.normalized_value, f.confidence_impact)
            for f in fset.features
        ),
    )
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    business_stage = trace.by_stage("business")
    website_stage = trace.by_stage("website")
    # The bonus must be small (<=0.05) -- completeness alone can never
    # substitute for real identity/verification evidence.
    assert business_stage.value - website_stage.value <= 0.05


def test_business_stage_notes_email_is_always_missing():
    fset = _feature_set()
    fset = FeatureSet(
        domain=fset.domain,
        features=tuple(
            _feature(f.feature_id, 0.667 if f.feature_id is FeatureId.CONTACT_COMPLETENESS else f.normalized_value, f.confidence_impact)
            for f in fset.features
        ),
    )
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    business_stage = trace.by_stage("business")
    assert any("email" in m for m in business_stage.missing)


# -- Identity stage: the stage a wrong-but-live site fails to gain from -----


def test_identity_stage_adds_brand_and_location_on_top_of_business():
    fset = _feature_set(BRAND_SIMILARITY=0.25, LOCATION_CONSISTENCY=0.15)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    business_value = trace.by_stage("business").value
    identity_value = trace.by_stage("identity").value
    assert identity_value == round(business_value + 0.25 + 0.15, 3)


def test_identity_stage_zero_gain_for_live_but_unrelated_site():
    """A reachable, HTTPS, canonical, well-agreed-upon site with zero
    brand/location relation to the business gains nothing at the identity
    stage -- it must not be able to buy its way to a high identity score
    purely on domain quality."""
    fset = _feature_set(REACHABILITY=0.15, HTTPS=0.03, CANONICAL_DOMAIN=0.02, PROVIDER_AGREEMENT=0.4)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    business_value = trace.by_stage("business").value
    identity_value = trace.by_stage("identity").value
    assert identity_value == business_value  # no brand/location contribution


# -- Final stage: verification gate --------------------------------------------


def test_final_stage_equals_identity_stage_when_verification_passes():
    fset = _feature_set(REACHABILITY=0.15, HTTPS=0.03, CANONICAL_DOMAIN=0.02, BRAND_SIMILARITY=0.25)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    assert trace.final == trace.by_stage("identity").value
    assert trace.final > 0.0


def test_final_stage_is_zeroed_when_domain_verifier_fails_even_with_strong_identity_signal():
    """The core gating property: a candidate that never passed
    reachability must never report a confident final number, no matter
    how strong its brand/location match looks."""
    fset = _feature_set(BRAND_SIMILARITY=0.25, LOCATION_CONSISTENCY=0.15)  # REACHABILITY untouched -> 0.0
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    assert trace.by_stage("identity").value > 0.0  # would have been non-zero otherwise
    assert trace.final == 0.0
    assert trace.by_stage("final").reduced


def test_final_stage_is_zeroed_when_identity_verifier_fails_even_with_perfect_domain_quality():
    fset = _feature_set(REACHABILITY=0.15, HTTPS=0.03, CANONICAL_DOMAIN=0.02)  # no brand/location/agreement at all
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    assert trace.final == 0.0


# -- Empty trace ----------------------------------------------------------------


def test_empty_confidence_trace_has_all_stages_at_zero():
    trace = empty_confidence_trace()
    assert trace.final == 0.0
    assert all(s.value == 0.0 for s in trace.stages)
    assert all(s.missing for s in trace.stages)


def test_empty_confidence_trace_carries_the_given_reason():
    trace = empty_confidence_trace("no groups at all")
    assert all("no groups at all" in s.missing for s in trace.stages)


# -- Explain / to_dict ----------------------------------------------------------


def test_explain_produces_one_line_per_stage():
    fset = _feature_set(BRAND_SIMILARITY=0.25)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    lines = trace.explain()
    assert len(lines) == 5
    assert all(isinstance(line, str) for line in lines)


def test_to_dict_is_json_serializable_shape():
    fset = _feature_set(BRAND_SIMILARITY=0.25)
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    payload = trace.to_dict()
    assert isinstance(payload["stages"], list)
    assert payload["final"] == trace.final


# -- Determinism / boundedness -------------------------------------------------


def test_propagation_is_pure_and_deterministic():
    fset = _feature_set(BRAND_SIMILARITY=0.25, PROVIDER_AGREEMENT=0.4)
    bundle = pipeline.run(fset)

    trace_a = engine.propagate(fset, bundle)
    trace_b = engine.propagate(fset, bundle)

    assert [s.value for s in trace_a.stages] == [s.value for s in trace_b.stages]


def test_every_stage_value_is_bounded_0_to_1():
    fset = _feature_set(
        PROVIDER_AGREEMENT=1.0, REACHABILITY=1.0, HTTPS=1.0, CANONICAL_DOMAIN=1.0, BRAND_SIMILARITY=1.0, LOCATION_CONSISTENCY=1.0
    )
    bundle = pipeline.run(fset)
    trace = engine.propagate(fset, bundle)

    for stage in trace.stages:
        assert 0.0 <= stage.value <= 1.0


# -- Module never imports evidence.py or identity.py (structural check) ----


def test_module_never_imports_evidence_or_identity():
    assert "evidence" not in confidence.__dict__
    assert "identity" not in confidence.__dict__
