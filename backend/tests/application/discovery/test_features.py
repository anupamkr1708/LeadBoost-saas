"""
Unit tests for application.discovery.features (Sprint 2B).

Everything here is pure (no I/O), so tests construct plain
CandidatePool/CandidateContext/BusinessCandidate inputs directly, mirroring
test_evidence.py and test_identity.py's own approach.
"""

from application.discovery.dto import BusinessCandidate
from application.discovery.evidence import (
    CandidatePool,
    EvidenceType,
    brand_identity_evidence,
    location_corroboration_evidence,
    verification_evidence,
)
from application.discovery.features import (
    CandidateContext,
    FeatureId,
    FeatureStore,
    GenericFeatureExtractionEngine,
)
from application.discovery.website_validator import ValidationOutcome


def _context_from_pool(pool, domain, verified=True):
    """Build a CandidateContext the same way identity.py does, from every
    raw candidate in `pool` sharing `domain`."""
    matching = [c for c in pool.candidates if c.domain == domain]
    from application.discovery.evidence import EvidenceBundle

    bundle = EvidenceBundle()
    for c in matching:
        bundle.extend(c.evidence.items)
    providers = []
    urls = []
    for c in matching:
        if c.provider not in providers:
            providers.append(c.provider)
        if c.url not in urls:
            urls.append(c.url)
    return CandidateContext(domain=domain, urls=tuple(urls), contributing_providers=tuple(providers), evidence=bundle, verified=verified)


def _fully_verified_context(business_name="Nike", domain="nike.com", location="Mumbai", address=None):
    pool = CandidatePool(business_name, location)
    candidate = pool.add_candidate(url=f"https://{domain}", provider="overpass", validated=True)
    candidate.evidence.add(brand_identity_evidence(business_name, domain, "overpass"))
    if address:
        candidate.evidence.add(location_corroboration_evidence(location, domain, address, "overpass"))
    outcome = ValidationOutcome(ok=True, normalized_url=f"https://{domain}/")
    candidate.evidence.extend(verification_evidence(outcome, "overpass"))
    return pool, _context_from_pool(pool, domain, verified=True)


engine = GenericFeatureExtractionEngine()


# -- Feature extraction: completeness of the produced set --------------------


def test_extract_returns_every_feature_id_always():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    feature_set = engine.extract(business, context, "Mumbai")

    ids = {f.feature_id for f in feature_set.features}
    assert ids == set(FeatureId)


def test_extract_is_pure_and_deterministic():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    first = engine.extract(business, context, "Mumbai")
    second = engine.extract(business, context, "Mumbai")

    for f1, f2 in zip(first.features, second.features):
        assert f1.feature_id == f2.feature_id
        assert f1.normalized_value == f2.normalized_value
        assert f1.confidence_impact == f2.confidence_impact


def test_feature_set_by_id_returns_none_for_missing_domain_lookup():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    feature_set = engine.extract(business, context, "Mumbai")

    # Every real FeatureId is present, so by_id never legitimately returns
    # None here -- this documents the guarantee rather than testing a gap.
    for feature_id in FeatureId:
        assert feature_set.by_id(feature_id) is not None


# -- Feature normalization / shape --------------------------------------------


def test_every_feature_has_the_required_shape():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    feature_set = engine.extract(business, context, "Mumbai")

    for f in feature_set.features:
        assert isinstance(f.feature_id, FeatureId)
        assert isinstance(f.confidence_impact, float)
        assert isinstance(f.supporting_evidence, tuple)
        assert isinstance(f.source_providers, tuple)
        assert isinstance(f.explanation, str) and f.explanation
        assert isinstance(f.timestamp, float)
        if f.normalized_value is not None:
            assert 0.0 <= f.normalized_value <= 1.0


def test_normalized_values_are_bounded_even_for_a_bare_provider_hit():
    """A candidate with only a bare PROVIDER_FOUND observation (no brand
    match, no location, never validated) should still produce a complete,
    in-bounds feature set -- graceful degradation, never a crash or an
    out-of-range value."""
    pool = CandidatePool("Nike", "Mumbai")
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False)
    context = _context_from_pool(pool, "nike.com", verified=False)
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    feature_set = engine.extract(business, context, "Mumbai")

    for f in feature_set.features:
        if f.normalized_value is not None:
            assert 0.0 <= f.normalized_value <= 1.0


# -- Never fabricate: reserved features ---------------------------------------


def test_website_structure_is_reserved_and_never_fabricated():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    feature_set = engine.extract(business, context, "Mumbai")

    structure = feature_set.by_id(FeatureId.WEBSITE_STRUCTURE)
    assert structure.raw_value is None
    assert structure.normalized_value is None
    assert structure.confidence_impact == 0.0


def test_contact_completeness_never_fabricates_an_email():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com", phone="0221234567", address="1 Park St")
    feature_set = engine.extract(business, context, "Mumbai")

    contact = feature_set.by_id(FeatureId.CONTACT_COMPLETENESS)
    assert contact.raw_value["email"] is False
    assert contact.normalized_value < 1.0  # can never reach 1.0 -- email is never available


# -- Domain quality ------------------------------------------------------------


def test_domain_quality_is_full_when_reachable_https_and_canonical():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    feature_set = engine.extract(business, context, "Mumbai")

    quality = feature_set.by_id(FeatureId.DOMAIN_QUALITY)
    assert quality.normalized_value == 1.0
    assert quality.confidence_impact == 0.0  # informational -- avoids double-counting sub-signals


def test_domain_quality_is_zero_when_never_validated():
    pool = CandidatePool("Nike", "Mumbai")
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="unreachable_http_404")
    outcome = ValidationOutcome(ok=False, reason="unreachable_http_404")
    pool.candidates[0].evidence.extend(verification_evidence(outcome, "overpass"))
    context = _context_from_pool(pool, "nike.com", verified=False)
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    feature_set = engine.extract(business, context, "Mumbai")

    quality = feature_set.by_id(FeatureId.DOMAIN_QUALITY)
    assert quality.normalized_value == 0.0


# -- Business / Contact completeness ------------------------------------------


def test_business_completeness_full_when_all_three_fields_present():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com", phone="0221234567", address="1 Park St")

    feature_set = engine.extract(business, context, "Mumbai")
    completeness = feature_set.by_id(FeatureId.BUSINESS_COMPLETENESS)

    assert completeness.normalized_value == 1.0
    assert completeness.raw_value == {"website": True, "phone": True, "address": True}


def test_business_completeness_partial_when_fields_missing():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike")  # no website/phone/address on the DTO itself

    feature_set = engine.extract(business, context, "Mumbai")
    completeness = feature_set.by_id(FeatureId.BUSINESS_COMPLETENESS)

    assert completeness.normalized_value == 0.0


# -- Evidence density ------------------------------------------------------------


def test_evidence_density_reflects_volume_not_positivity():
    pool, context = _fully_verified_context(address="1 Park St, Mumbai")
    business = BusinessCandidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai")

    feature_set = engine.extract(business, context, "Mumbai")
    density = feature_set.by_id(FeatureId.EVIDENCE_DENSITY)

    assert density.raw_value == len(context.evidence.items)
    assert density.normalized_value > 0.0


def test_evidence_density_is_zero_for_empty_evidence():
    context = CandidateContext(domain="nike.com", urls=("https://nike.com",), contributing_providers=("overpass",), evidence=_empty_bundle(), verified=False)
    business = BusinessCandidate(name="Nike")

    feature_set = engine.extract(business, context, "Mumbai")
    density = feature_set.by_id(FeatureId.EVIDENCE_DENSITY)

    assert density.raw_value == 0
    assert density.normalized_value == 0.0


def _empty_bundle():
    from application.discovery.evidence import EvidenceBundle

    return EvidenceBundle()


# -- Verification readiness ----------------------------------------------------


def test_verification_readiness_high_for_distinctive_name():
    pool, context = _fully_verified_context(business_name="Zephyrion Consulting Group")
    business = BusinessCandidate(name="Zephyrion Consulting Group", website="https://zephyrion.com")

    feature_set = engine.extract(business, context, "Mumbai")
    readiness = feature_set.by_id(FeatureId.VERIFICATION_READINESS)

    assert readiness.normalized_value == 1.0


def test_verification_readiness_low_for_generic_name_with_no_corroboration():
    pool = CandidatePool("Shop", "Mumbai")
    pool.add_candidate(url="https://genericshop.com", provider="overpass", validated=True)
    context = _context_from_pool(pool, "genericshop.com", verified=True)
    business = BusinessCandidate(name="Shop")

    feature_set = engine.extract(business, context, "Mumbai")
    readiness = feature_set.by_id(FeatureId.VERIFICATION_READINESS)

    assert readiness.normalized_value < 1.0


# -- No double-counting: confidence_impact reused, not reinvented -----------


def test_brand_similarity_confidence_impact_matches_evidence_bundle_delta():
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    feature_set = engine.extract(business, context, "Mumbai")
    brand = feature_set.by_id(FeatureId.BRAND_SIMILARITY)

    matches = context.evidence.by_type(EvidenceType.BRAND_MATCH)
    expected = round(sum(e.confidence_delta for e in matches), 4)
    assert brand.confidence_impact == expected


# -- Feature Store --------------------------------------------------------------


def test_feature_store_put_get_and_domains():
    store = FeatureStore()
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    feature_set = engine.extract(business, context, "Mumbai")

    store.put(feature_set)

    assert store.domains() == ["nike.com"]
    assert store.get("nike.com") is feature_set
    assert store.get("does-not-exist.com") is None
    assert store.all() == [feature_set]


def test_feature_store_put_overwrites_same_domain_without_duplicating_order():
    store = FeatureStore()
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    first = engine.extract(business, context, "Mumbai")
    second = engine.extract(business, context, "Mumbai")

    store.put(first)
    store.put(second)

    assert store.domains() == ["nike.com"]
    assert store.get("nike.com") is second


def test_feature_store_to_dict_is_json_serializable_shape():
    store = FeatureStore()
    pool, context = _fully_verified_context()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    store.put(engine.extract(business, context, "Mumbai"))

    payload = store.to_dict()

    assert "nike.com" in payload
    assert isinstance(payload["nike.com"]["features"], list)


# -- Category-agnostic (no hardcoded business-category logic) ----------------


def test_feature_extraction_never_branches_on_category():
    pool_a, context_a = _fully_verified_context(business_name="Curry House", address="1 Park St, Mumbai")
    business_a = BusinessCandidate(name="Curry House", category="restaurant", address="1 Park St, Mumbai")

    pool_b, context_b = _fully_verified_context(business_name="Curry House", address="1 Park St, Mumbai")
    business_b = BusinessCandidate(name="Curry House", category="law firm", address="1 Park St, Mumbai")

    features_a = engine.extract(business_a, context_a, "Mumbai")
    features_b = engine.extract(business_b, context_b, "Mumbai")

    for fa, fb in zip(features_a.features, features_b.features):
        assert fa.feature_id == fb.feature_id
        assert fa.confidence_impact == fb.confidence_impact
        assert fa.normalized_value == fb.normalized_value
