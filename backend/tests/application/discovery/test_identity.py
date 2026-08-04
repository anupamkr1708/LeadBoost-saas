"""
Unit tests for application.discovery.identity (Sprint 2A).

Mirrors test_evidence.py's approach: everything in identity.py is pure
(no I/O), so these run against plain constructed CandidatePool/
WebsiteCandidate/BusinessCandidate inputs. Covers, per the Sprint 2A
brief: identity creation, provider agreement, candidate merging, feature
extraction, and decision-trace generation. Backward compatibility (no
existing test fails, WebsiteResolver.resolve()'s external contract is
unchanged) is covered by the additional tests appended to
test_website_resolver_evidence.py, which already runs the full pipeline
through WebsiteResolver.
"""

from application.discovery.dto import BusinessCandidate
from application.discovery.evidence import CandidatePool
from application.discovery.identity import (
    FeatureName,
    GenericIdentityResolver,
    compute_provider_agreement,
    extract_features,
    group_website_candidates,
    normalize_business_name,
    verify_identity_features,
)


def _pool_with(*, business_name="Nike", location="Mumbai"):
    return CandidatePool(business_name, location)


# -- normalize_business_name -------------------------------------------------


def test_normalize_business_name_strips_generic_suffixes_and_lowercases():
    assert normalize_business_name("Metro Shoes Pvt Ltd") == "metro shoes"


def test_normalize_business_name_handles_empty_name_gracefully():
    assert normalize_business_name("") == ""


# -- group_website_candidates (candidate merging) ----------------------------


def test_merging_collapses_same_domain_from_two_providers_into_one_group():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="timeout")
    pool.add_candidate(url="https://nike.com/about", provider="serper", validated=True)

    groups = group_website_candidates(pool)

    assert len(groups) == 1
    group = groups[0]
    assert group.domain == "nike.com"
    assert set(group.contributing_providers) == {"overpass", "serper"}
    assert group.urls == ["https://nike.com", "https://nike.com/about"]
    assert group.verified is True
    # A group with at least one validated source candidate is verified,
    # full stop -- the earlier failure shouldn't leak through as a
    # rejection reason on an otherwise-verified group.
    assert group.rejection_reason is None


def test_merging_keeps_distinct_domains_as_separate_groups():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    pool.add_candidate(url="https://nike-outlet.com", provider="serper", validated=False, rejection_reason="x")

    groups = group_website_candidates(pool)

    assert len(groups) == 2
    assert {g.domain for g in groups} == {"nike.com", "nike-outlet.com"}


def test_merging_reports_rejection_reason_only_when_group_never_verified():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="dns_resolution_failed")

    groups = group_website_candidates(pool)

    assert groups[0].verified is False
    assert groups[0].rejection_reason == "dns_resolution_failed"


def test_merging_empty_pool_produces_no_groups():
    pool = _pool_with()
    assert group_website_candidates(pool) == []


def test_merged_evidence_is_the_union_not_a_deduped_single_copy():
    """Two providers independently finding the same domain is two real
    observations -- both PROVIDER_FOUND entries should survive the merge,
    not collapse into one."""
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    pool.add_candidate(url="https://nike.com", provider="serper", validated=True)

    group = group_website_candidates(pool)[0]

    from application.discovery.evidence import EvidenceType

    provider_found_entries = group.evidence.by_type(EvidenceType.PROVIDER_FOUND)
    assert len(provider_found_entries) == 2


# -- compute_provider_agreement ----------------------------------------------


def test_provider_agreement_no_candidates():
    record = compute_provider_agreement(_pool_with())
    assert record.status == "no_candidates"
    assert record.agreeing_providers == []


def test_provider_agreement_single_source():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)

    record = compute_provider_agreement(pool)

    assert record.status == "single_source"
    assert record.agreeing_providers == ["overpass"]


def test_provider_agreement_agreement_across_providers():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="timeout")
    pool.add_candidate(url="https://nike.com/about", provider="serper", validated=True)

    record = compute_provider_agreement(pool)

    assert record.status == "agreement"
    assert record.agreeing_domain == "nike.com"
    assert set(record.agreeing_providers) == {"overpass", "serper"}
    assert record.disagreements == []


def test_provider_agreement_disagreement_is_never_hidden():
    pool = _pool_with()
    pool.add_candidate(url="https://nike-outlet.com", provider="overpass", validated=False, rejection_reason="timeout")
    pool.add_candidate(url="https://nike.com", provider="serper", validated=True)

    record = compute_provider_agreement(pool)

    assert record.status == "disagreement"
    assert record.agreeing_domain is None
    domains_reported = {d["domain"] for d in record.disagreements}
    assert domains_reported == {"nike-outlet.com", "nike.com"}


def test_provider_agreement_explain_is_human_readable():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="timeout")
    pool.add_candidate(url="https://nike.com/about", provider="serper", validated=True)
    record = compute_provider_agreement(pool)
    assert "nike.com" in record.explain()


# -- extract_features (feature extraction) -----------------------------------


def test_feature_extraction_returns_every_feature_name_always():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    features = extract_features(business, group, "Mumbai")

    names = {f.name for f in features}
    assert names == set(FeatureName)


def test_feature_extraction_brand_similarity_present_for_matching_name():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    features = extract_features(business, group, "Mumbai")
    brand = next(f for f in features if f.name is FeatureName.BRAND_SIMILARITY)

    assert brand.normalized_value == 1.0
    assert brand.confidence_contribution > 0.0


def test_feature_extraction_brand_similarity_absent_for_unrelated_name():
    pool = _pool_with()
    pool.add_candidate(url="https://regmovies.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    business = BusinessCandidate(name="Regal Shoes", website="https://regmovies.com")

    features = extract_features(business, group, "Mumbai")
    brand = next(f for f in features if f.name is FeatureName.BRAND_SIMILARITY)

    assert brand.normalized_value == 0.0
    assert brand.confidence_contribution == 0.0


def test_feature_extraction_location_consistency_full_credit_for_address():
    pool = _pool_with()
    pool.add_candidate(url="https://metroshoes.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    business = BusinessCandidate(name="Metro Shoes", address="123 MG Road, Mumbai")

    features = extract_features(business, group, "Mumbai")
    location = next(f for f in features if f.name is FeatureName.LOCATION_CONSISTENCY)

    assert location.normalized_value == 1.0


def test_feature_extraction_reserved_features_never_fabricate_data():
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    features = extract_features(business, group, "Mumbai")
    reserved = [
        f
        for f in features
        if f.name
        in (
            FeatureName.PHONE_SIMILARITY,
            FeatureName.ADDRESS_SIMILARITY,
            FeatureName.COORDINATE_SIMILARITY,
            FeatureName.ORGANIZATION_METADATA,
        )
    ]
    assert len(reserved) == 4
    for feature in reserved:
        assert feature.raw_value is None
        assert feature.normalized_value is None
        assert feature.confidence_contribution == 0.0


def test_feature_extraction_is_identical_across_categories():
    """No `if restaurant` / `if lawyer` special-casing anywhere: the same
    generic computation must behave identically regardless of what
    category the business happens to be."""
    pool_a = _pool_with(business_name="Curry House")
    pool_a.add_candidate(url="https://curryhouse.com", provider="overpass", validated=True)
    group_a = group_website_candidates(pool_a)[0]
    business_a = BusinessCandidate(name="Curry House", category="restaurant", address="1 Park St, Mumbai")

    pool_b = _pool_with(business_name="Curry House")
    pool_b.add_candidate(url="https://curryhouse.com", provider="overpass", validated=True)
    group_b = group_website_candidates(pool_b)[0]
    business_b = BusinessCandidate(name="Curry House", category="law firm", address="1 Park St, Mumbai")

    features_a = extract_features(business_a, group_a, "Mumbai")
    features_b = extract_features(business_b, group_b, "Mumbai")

    for fa, fb in zip(features_a, features_b):
        assert fa.name == fb.name
        assert fa.confidence_contribution == fb.confidence_contribution
        assert fa.normalized_value == fb.normalized_value


# -- verify_identity_features (verification consumes Features only) ---------


def test_verify_identity_true_when_brand_matches():
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    features = extract_features(business, group, "Mumbai")

    result = verify_identity_features(features)

    assert result.verified is True


def test_verify_identity_false_when_nothing_supports_the_match():
    business = BusinessCandidate(name="Regal Shoes", website="https://regmovies.com")
    pool = _pool_with()
    pool.add_candidate(url="https://regmovies.com", provider="overpass", validated=True)
    group = group_website_candidates(pool)[0]
    features = extract_features(business, group, "Somewhere Else")

    result = verify_identity_features(features)

    assert result.verified is False


# -- GenericIdentityResolver (identity creation + decision trace) -----------


def test_identity_creation_builds_full_business_identity():
    business = BusinessCandidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai", phone="0221234567")
    pool = _pool_with()
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    from application.discovery.evidence import brand_identity_evidence, location_corroboration_evidence

    candidate.evidence.add(brand_identity_evidence(business.name, candidate.domain, candidate.provider))
    candidate.evidence.add(location_corroboration_evidence("Mumbai", candidate.domain, business.address, candidate.provider))

    resolver = GenericIdentityResolver()
    identity = resolver.resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    assert identity.normalized_name == "nike"
    assert identity.selected_website == "https://nike.com/"
    assert identity.phones == ["0221234567"]
    assert identity.addresses == ["1 Park St, Mumbai"]
    assert identity.emails == []  # never fabricated -- no source for it today
    assert identity.verification_state == "verified"
    assert identity.confidence > 0.0
    assert len(identity.website_candidates) == 1


def test_identity_verification_state_no_candidates_when_pool_empty():
    business = BusinessCandidate(name="Nike")
    pool = _pool_with()
    resolver = GenericIdentityResolver()

    identity = resolver.resolve(business, pool, "Mumbai", chosen=None, chosen_url=None)

    assert identity.verification_state == "no_candidates"
    assert identity.website_candidates == []
    assert identity.selected_website is None
    assert identity.confidence == 0.0


def test_identity_verification_state_unverified_when_candidate_never_validated():
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    pool = _pool_with()
    pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="request_timed_out")
    resolver = GenericIdentityResolver()

    identity = resolver.resolve(business, pool, "Mumbai", chosen=None, chosen_url=None)

    assert identity.verification_state == "unverified"
    assert len(identity.website_candidates) == 1


def test_decision_trace_explains_selection_and_rejection():
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    pool = _pool_with()
    chosen_candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    pool.add_candidate(url="https://nike-outlet.com", provider="serper", validated=False, rejection_reason="unreachable_http_404")

    resolver = GenericIdentityResolver()
    identity = resolver.resolve(business, pool, "Mumbai", chosen=chosen_candidate, chosen_url="https://nike.com/")

    trace = identity.decision_trace
    assert trace.selected_domain == "nike.com"
    assert any(r["domain"] == "nike-outlet.com" for r in trace.rejected)
    assert "nike.com" in trace.explain()
    assert trace.provider_agreement.status == "disagreement"


def test_decision_trace_no_candidate_selected_explains_why():
    business = BusinessCandidate(name="Nike")
    pool = _pool_with()
    resolver = GenericIdentityResolver()

    identity = resolver.resolve(business, pool, "Mumbai", chosen=None, chosen_url=None)

    assert identity.decision_trace.selected_domain is None
    assert "No candidate was selected" in identity.decision_trace.explain()


def test_business_identity_provider_evidence_is_a_view_of_the_bundle():
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    pool = _pool_with()
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)

    resolver = GenericIdentityResolver()
    identity = resolver.resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    from application.discovery.evidence import EvidenceCategory

    assert identity.provider_evidence == identity.evidence_bundle.by_category(EvidenceCategory.PROVIDER)
    assert len(identity.provider_evidence) >= 1


def test_business_identity_to_dict_is_json_serializable_shape():
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    pool = _pool_with()
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)

    resolver = GenericIdentityResolver()
    identity = resolver.resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    payload = identity.to_dict()
    assert payload["normalized_name"] == "nike"
    assert isinstance(payload["website_candidates"], list)
    assert isinstance(payload["decision_trace"], dict)


# ---------------------------------------------------------------------------
# Sprint 2B: Feature Store / Verification / Confidence Propagation wired in
# ---------------------------------------------------------------------------
#
# identity.py's own responsibility narrowed in Sprint 2B (see its module
# docstring) -- feature extraction/verification/confidence propagation now
# live in application.discovery.features/verification/confidence. These
# tests check the *wiring*: that GenericIdentityResolver actually builds a
# FeatureStore and runs the new pipeline, and that DecisionTrace's new
# fields are populated correctly -- not the internals of those three
# modules, which have their own dedicated test files
# (test_features.py/test_verification.py/test_confidence.py).

from application.discovery.confidence import ConfidenceTrace  # noqa: E402
from application.discovery.evidence import (  # noqa: E402
    brand_identity_evidence,
    location_corroboration_evidence,
    verification_evidence,
)
from application.discovery.features import FeatureStore  # noqa: E402
from application.discovery.verification import VerificationBundle  # noqa: E402
from application.discovery.website_validator import ValidationOutcome  # noqa: E402


def _fully_verified_pool(business_name="Nike", location="Mumbai", address=None):
    pool = _pool_with(business_name=business_name, location=location)
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    candidate.evidence.add(brand_identity_evidence(business_name, candidate.domain, candidate.provider))
    if address:
        candidate.evidence.add(location_corroboration_evidence(location, candidate.domain, address, candidate.provider))
    outcome = ValidationOutcome(ok=True, normalized_url="https://nike.com/")
    candidate.evidence.extend(verification_evidence(outcome, candidate.provider))
    return pool, candidate


def test_business_identity_carries_a_populated_feature_store():
    pool, candidate = _fully_verified_pool()
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    assert isinstance(identity.feature_store, FeatureStore)
    assert identity.feature_store.domains() == ["nike.com"]
    assert identity.feature_store.get("nike.com") is not None


def test_decision_trace_carries_verification_bundle_and_confidence_trace():
    pool, candidate = _fully_verified_pool(address="1 Park St, Mumbai")
    business = BusinessCandidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai")

    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    trace = identity.decision_trace
    assert isinstance(trace.verification_bundle, VerificationBundle)
    assert trace.verification_bundle.overall_passed is True
    assert isinstance(trace.confidence_trace, ConfidenceTrace)
    assert trace.confidence_trace.final == identity.confidence


def test_decision_trace_features_used_lists_only_features_with_impact():
    pool, candidate = _fully_verified_pool(address="1 Park St, Mumbai")
    business = BusinessCandidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai")

    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    assert "brand_similarity" in identity.decision_trace.features_used
    assert "reachability" in identity.decision_trace.features_used
    # Purely-informational features (zero confidence_impact) never appear.
    assert "domain_quality" not in identity.decision_trace.features_used
    assert "website_structure" not in identity.decision_trace.features_used


def test_decision_trace_missing_evidence_reports_reserved_signals():
    pool, candidate = _fully_verified_pool()
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    assert identity.decision_trace.missing_evidence  # non-empty -- website structure/email always missing today


def test_decision_trace_no_candidates_still_has_a_complete_empty_confidence_trace():
    business = BusinessCandidate(name="Nike")
    pool = _pool_with()

    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=None, chosen_url=None)

    assert identity.decision_trace.confidence_trace.final == 0.0
    assert len(identity.decision_trace.confidence_trace.stages) == 5
    assert identity.decision_trace.verification_bundle is None


def test_confidence_gated_to_zero_when_domain_never_reachable_even_with_brand_match():
    """The critical-verifier gate (Sprint 2B) applied end-to-end through
    GenericIdentityResolver: a candidate with a perfect brand match but
    that never passed reachability must resolve to zero confidence."""
    pool = _pool_with()
    candidate = pool.add_candidate(url="https://nike.com", provider="overpass", validated=False, rejection_reason="request_timed_out")
    candidate.evidence.add(brand_identity_evidence("Nike", candidate.domain, candidate.provider))
    business = BusinessCandidate(name="Nike", website="https://nike.com")

    # chosen=None because WebsiteResolver would never select an unvalidated
    # candidate -- but the group still exists in the pool for identity
    # resolution to reason about.
    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=None, chosen_url=None)

    assert identity.confidence == 0.0
    assert identity.verification_state == "unverified"


# -- Regression compatibility (Sprint 2B on top of Sprint 2A) ---------------


def test_sprint_2a_public_names_are_all_still_importable_and_unchanged():
    """The exact import this test file itself uses at the top, re-asserted
    explicitly: every Sprint 2A public name identity.py exposed must still
    exist, unrenamed, after the Sprint 2B refactor."""
    import application.discovery.identity as identity_module

    for name in (
        "Feature",
        "FeatureName",
        "WebsiteCandidateGroup",
        "group_website_candidates",
        "ProviderAgreementRecord",
        "compute_provider_agreement",
        "IdentityVerificationResult",
        "verify_identity_features",
        "DecisionTrace",
        "BusinessIdentity",
        "IdentityResolver",
        "GenericIdentityResolver",
        "normalize_business_name",
        "extract_features",
    ):
        assert hasattr(identity_module, name), f"{name} was removed or renamed"


def test_sprint_2a_verify_identity_features_still_works_unchanged():
    """Sprint 2A's own identity-feature verification function -- kept
    deliberately separate from the new verification.IdentityVerifier (see
    identity.py's module docstring) -- must still behave exactly as it did
    in Sprint 2A."""
    pool, candidate = _fully_verified_pool()
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    group = group_website_candidates(pool)[0]
    group.features = extract_features(business, group, "Mumbai")

    from application.discovery.identity import verify_identity_features

    result = verify_identity_features(group.features)
    assert result.verified is True


def test_group_website_candidates_and_identity_confidence_still_work_unchanged():
    """WebsiteCandidateGroup.confidence/identity_confidence (Sprint 2A)
    still compute the same way -- Sprint 2B added a parallel, richer
    system, it did not repurpose this one."""
    pool, candidate = _fully_verified_pool(address="1 Park St, Mumbai")
    business = BusinessCandidate(name="Nike", website="https://nike.com")
    group = group_website_candidates(pool)[0]
    group.features = extract_features(business, group, "Mumbai")

    assert group.confidence == group.evidence.confidence
    assert 0.0 <= group.identity_confidence <= 1.0


# ---------------------------------------------------------------------------
# Sprint 2C: identity.py itself is completely untouched
# ---------------------------------------------------------------------------
#
# Sprint 2C's new canonical object (application.discovery.digital_identity.
# VerifiedDigitalIdentity) is built entirely *outside* this module -- see
# digital_identity.py's own module docstring for why (avoiding a circular
# import: digital_identity.py imports identity.py, so identity.py must
# never import digital_identity.py back). These tests assert that
# guarantee directly: not one name, field, or behavior in identity.py
# changed to support Sprint 2C.


def test_sprint_2b_public_names_are_still_all_present_after_sprint_2c():
    """Re-run of the exact same check test_sprint_2a_public_names_are_all_
    still_importable_and_unchanged already performs, as an explicit
    Sprint 2C checkpoint: nothing was removed or renamed."""
    import application.discovery.identity as identity_module

    for name in (
        "Feature",
        "FeatureName",
        "WebsiteCandidateGroup",
        "group_website_candidates",
        "ProviderAgreementRecord",
        "compute_provider_agreement",
        "IdentityVerificationResult",
        "verify_identity_features",
        "DecisionTrace",
        "BusinessIdentity",
        "IdentityResolver",
        "GenericIdentityResolver",
        "normalize_business_name",
        "extract_features",
    ):
        assert hasattr(identity_module, name), f"{name} was removed or renamed"


def test_identity_module_does_not_import_digital_identity():
    """Structural guarantee against the circular import digital_identity.py's
    docstring calls out: identity.py must never depend on it. Checked at
    the source level (not just "was it imported this test run"), so this
    fails immediately if a future change ever introduces the cycle."""
    import inspect

    import application.discovery.identity as identity_module

    source = inspect.getsource(identity_module)
    assert "digital_identity" not in source


def test_generic_identity_resolver_has_no_new_required_constructor_arguments():
    """GenericIdentityResolver's own constructor -- already extended once
    in Sprint 2B with defaulted engine parameters -- gained nothing new in
    Sprint 2C: the canonical object is built by a separate
    DigitalIdentityBuilder (see website_resolver.py), not by this class."""
    resolver = GenericIdentityResolver()
    assert resolver.feature_engine is not None
    assert resolver.verification_pipeline is not None
    assert resolver.confidence_engine is not None


def test_generic_identity_resolver_resolve_behavior_is_completely_unaffected():
    """Same scenario, same assertions as Sprint 2B's own
    test_business_identity_confidence_is_now_propagated_but_still_verified_
    end_to_end, re-run here as an explicit Sprint 2C regression checkpoint."""
    pool, candidate = _fully_verified_pool(address="1 Park St, Mumbai")
    business = BusinessCandidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai")

    identity = GenericIdentityResolver().resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://nike.com/")

    assert identity.confidence == identity.decision_trace.confidence_trace.final
    assert identity.confidence > 0.0
    assert identity.decision_trace.verification_bundle.overall_passed is True
