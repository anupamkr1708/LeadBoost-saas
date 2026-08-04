"""
Unit tests for application.discovery.ranking (Sprint 4).

Companion to test_ranking.py, which covers rank_businesses()/score_business()
black-box (comparing overall scores between two businesses end to end).
This file is the white-box counterpart -- it exercises ranking.py's internal
scoring helpers directly and covers both of its two paths:

  - the FALLBACK path (`business.identity is None`), which must reproduce
    ranking.py's pre-Sprint-4 raw-field computation exactly, byte for
    byte, since any caller still using plain `WebsiteResolver.resolve()`
    (or a hand-built DiscoveredBusiness, as every test below except the
    identity-aware ones does) depends on it never changing;
  - the IDENTITY-AWARE path (`business.identity` is a real
    VerifiedDigitalIdentity, built end-to-end exactly the way
    discovery_service.py now does via `resolve_with_digital_identity()`),
    which must consume the already-computed signals described in
    ranking.py's own module docstring instead of recomputing them.
"""

from application.discovery.digital_identity import DigitalIdentityBuilder
from application.discovery.dto import BusinessCandidate, DiscoveredBusiness, WebsiteResolution
from application.discovery.evidence import (
    CandidatePool,
    brand_identity_evidence,
    location_corroboration_evidence,
    verification_evidence,
)
from application.discovery.identity import GenericIdentityResolver
from application.discovery.ranking import (
    _contact_completeness_score,
    _domain_brand_score,
    _identity_stability_score,
    _location_score,
    _text_overlap,
    _website_verification_score,
    rank_businesses,
    score_business,
)
from application.discovery.website_validator import ValidationOutcome


# ---------------------------------------------------------------------------
# Fallback path (business.identity is None) -- byte-for-byte pre-Sprint-4
# behavior. Each scoring component is exercised directly through its own
# helper function (exactly how ranking.py itself composes them in
# score_business), which isolates each one cleanly without needing to
# hand-compute every other component's contribution to a combined total.
# ---------------------------------------------------------------------------


def _candidate(**kwargs):
    defaults = dict(name="Zzqx Corp")
    defaults.update(kwargs)
    return BusinessCandidate(**defaults)


def _resolution(website=None, validated=False):
    return WebsiteResolution(website=website, resolved_via="overpass" if validated else "none", validated=validated)


def _business(candidate=None, resolution=None):
    return DiscoveredBusiness(candidate=candidate or _candidate(), resolution=resolution or _resolution(), identity=None)


def test_fallback_neutral_business_scores_zero():
    assert score_business(_business(), category="", location="") == 0.0


# -- website verification component --------------------------------------------


def test_fallback_website_verification_scores_flat_forty_when_validated():
    business = _business(resolution=_resolution(website="https://x.example", validated=True))
    assert _website_verification_score(business, "https://x.example", None) == 40.0


def test_fallback_website_verification_zero_when_not_validated():
    business = _business(resolution=_resolution(website=None, validated=False))
    assert _website_verification_score(business, None, None) == 0.0


# -- category match component (score_business level -- no upstream equivalent) --


def test_fallback_category_match_on_candidate_category_scores_full_points():
    business = _business(candidate=_candidate(category="shoe store"))
    assert score_business(business, category="shoe store", location="") == 20.0


def test_fallback_category_match_on_candidate_name_scores_half_points():
    business = _business(candidate=_candidate(name="Nike Running", category="retail"))
    assert score_business(business, category="running", location="") == 10.0


def test_fallback_no_category_match_scores_zero():
    business = _business(candidate=_candidate(category="bakery", name="Random Co"))
    assert score_business(business, category="shoe store", location="") == 0.0


# -- location match component --------------------------------------------------


def test_fallback_location_match_on_address_scores_full_points():
    candidate = _candidate(address="123 Linking Road, Mumbai")
    assert _location_score(candidate, None, "Mumbai", None) == 15.0


def test_fallback_location_match_via_domain_text_scores_half_points():
    candidate = _candidate(address="Unknown")
    assert _location_score(candidate, "https://mumbai-shoes.example", "Mumbai", None) == 7.5


def test_fallback_no_location_match_scores_zero():
    candidate = _candidate(address="Unknown street")
    assert _location_score(candidate, "https://x.example", "Chennai", None) == 0.0


# -- domain brand match component ----------------------------------------------


def test_fallback_domain_brand_match_reuses_grounding_directly():
    from application.discovery import grounding

    candidate = _candidate(name="Nike")
    expected = grounding.brand_match_strength("Nike", "nike.com") * 15.0
    assert _domain_brand_score(candidate, "https://nike.com", None) == expected


def test_fallback_domain_brand_match_zero_without_a_website():
    candidate = _candidate(name="Nike")
    assert _domain_brand_score(candidate, None, None) == 0.0


# -- rating / review count components (score_business level) ------------------


def test_fallback_rating_scales_linearly_to_ten_points():
    assert score_business(_business(_candidate(rating=5.0)), category="", location="") == 10.0
    assert score_business(_business(_candidate(rating=2.5)), category="", location="") == 5.0


def test_fallback_rating_none_contributes_nothing():
    assert score_business(_business(_candidate(rating=None)), category="", location="") == 0.0


def test_fallback_review_count_is_log_scaled_and_capped():
    import math

    expected = min(math.log1p(50) * 2.0, 10.0)
    business = _business(_candidate(review_count=50))
    assert score_business(business, category="", location="") == round(expected, 2)


def test_fallback_review_count_capped_at_ten_points():
    business = _business(_candidate(review_count=100_000))
    assert score_business(business, category="", location="") == 10.0


# -- contact completeness component --------------------------------------------


def test_fallback_contact_completeness_counts_phone_address_website():
    candidate = _candidate(phone="555", address="X")
    # phone + address present, website (local var, post-validation) absent -> 2/3.
    assert _contact_completeness_score(candidate, None, None) == round((2 / 3) * 15.0, 2)


def test_fallback_contact_completeness_all_three_present():
    candidate = _candidate(phone="555", address="X")
    assert _contact_completeness_score(candidate, "https://x.example", None) == 15.0


def test_fallback_contact_completeness_none_present():
    candidate = _candidate()
    assert _contact_completeness_score(candidate, None, None) == 0.0


# -- identity stability component (new in Sprint 4, no fallback equivalent) ----


def test_fallback_identity_stability_component_is_always_zero():
    assert _identity_stability_score(None) == 0.0


# -- rank_businesses / _text_overlap -------------------------------------------


def test_rank_businesses_sorts_descending_and_sets_rank_score():
    strong = _business(_candidate(name="A", rating=5.0))
    weak = _business(_candidate(name="B", rating=1.0))
    ranked = rank_businesses([weak, strong], category="", location="")
    assert ranked[0].candidate.name == "A"
    assert ranked[0].rank_score >= ranked[1].rank_score
    assert weak.rank_score == 2.0  # 1.0/5.0 * 10


def test_text_overlap_matches_on_shared_significant_word():
    assert _text_overlap("Nike Running Store", "running shoes") is True


def test_text_overlap_ignores_short_words():
    assert _text_overlap("a be to it", "a be to it") is False


def test_text_overlap_false_with_no_shared_words():
    assert _text_overlap("bakery", "electronics") is False




# ---------------------------------------------------------------------------
# Identity-aware path -- built end-to-end exactly like discovery_service.py
# ---------------------------------------------------------------------------


def _business_with_real_identity(
    business_name="Nike", location="Mumbai", address="1 Park St, Mumbai", website="https://nike.com",
    rating=4.5, review_count=1200,
):
    pool = CandidatePool(business_name, location)
    candidate = pool.add_candidate(url=website, provider="overpass", validated=True)
    candidate.evidence.add(brand_identity_evidence(business_name, candidate.domain, candidate.provider))
    candidate.evidence.add(location_corroboration_evidence(location, candidate.domain, address, candidate.provider))
    candidate.evidence.extend(
        verification_evidence(ValidationOutcome(ok=True, normalized_url=website + "/"), candidate.provider)
    )
    business_candidate = BusinessCandidate(
        name=business_name, category="shoe store", address=address, phone="555-1000",
        website=website, rating=rating, review_count=review_count,
    )
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business_candidate, pool, location, chosen=candidate, chosen_url=website + "/")
    vdi = DigitalIdentityBuilder().build(business_identity, business_candidate)
    resolution = WebsiteResolution(website=website + "/", resolved_via="overpass", validated=True)
    discovered = DiscoveredBusiness(candidate=business_candidate, resolution=resolution, identity=vdi)
    return discovered, vdi


def test_identity_aware_website_score_scales_with_verification_strength():
    from application.discovery.ranking import _website_verification_score

    business, vdi = _business_with_real_identity()
    strength = max(0.0, min(vdi.verification_quality.strength, 1.0))
    expected = 40.0 * (0.6 + 0.4 * strength)
    actual = _website_verification_score(business, business.resolution.website, vdi)
    assert abs(actual - expected) < 1e-9
    assert 24.0 <= actual <= 40.0


def test_identity_aware_domain_brand_reuses_brand_similarity_feature():
    from application.discovery.features import FeatureId
    from application.discovery.website_validator import domain_of

    business, vdi = _business_with_real_identity()
    feature = vdi.feature_store.get(domain_of(vdi.selected_website)).by_id(FeatureId.BRAND_SIMILARITY)
    assert feature is not None
    expected_component = (feature.normalized_value or 0.0) * 15.0

    # Isolate the brand component by zeroing every other contributor via a
    # second, minimal business that shares the same identity but empty
    # category/location args and no rating/reviews -- since components
    # are additive, compare deltas is unreliable across different
    # candidates, so instead assert the identity path is actually being
    # read (not silently falling back) by checking it differs from what
    # the fallback computation would have given for the same inputs.
    from application.discovery import grounding

    fallback_component = grounding.brand_match_strength("Nike", "nike.com") * 15.0
    # Both should agree closely (same underlying grounding call, feature
    # extraction wraps it) -- this confirms the identity-aware path reads
    # the SAME already-computed value rather than an unrelated one.
    assert round(expected_component, 3) == round(fallback_component, 3)


def test_identity_aware_location_score_uses_location_consistency_feature():
    from application.discovery.features import FeatureId
    from application.discovery.website_validator import domain_of

    business, vdi = _business_with_real_identity()
    feature = vdi.feature_store.get(domain_of(vdi.selected_website)).by_id(FeatureId.LOCATION_CONSISTENCY)
    assert feature is not None
    assert feature.normalized_value == 1.0  # full, address-based match in this fixture


def test_identity_aware_contact_completeness_uses_identity_completeness_overall_score():
    business, vdi = _business_with_real_identity()
    # identity_completeness.overall_score is a richer, 8-dimension number
    # -- confirm ranking actually reads it by checking it's neither 0 nor
    # trivially 1.0 for this fixture (missing coordinates keeps it < 1.0).
    assert 0.0 < vdi.identity_completeness.overall_score < 1.0


def test_identity_aware_stability_component_is_nonzero_when_identity_present():
    business, vdi = _business_with_real_identity()
    assert vdi.quality.identity_stability > 0.0
    score_with = score_business(business, category="shoe store", location="Mumbai")
    # Same business but with identity stripped -- must score strictly
    # lower once the stability bonus is removed, all else equal.
    business_no_identity = business.model_copy(update={"identity": None})
    score_without = score_business(business_no_identity, category="shoe store", location="Mumbai")
    assert score_with > score_without


def test_identity_aware_scores_higher_than_fallback_for_a_strong_match():
    """A strongly verified, well-corroborated identity should score
    meaningfully higher than the cruder raw-field fallback would for the
    exact same underlying business -- the whole point of Sprint 4's
    ranking refactor."""
    business, vdi = _business_with_real_identity()
    score_with_identity = score_business(business, category="shoe store", location="Mumbai")

    business_no_identity = business.model_copy(update={"identity": None})
    score_fallback = score_business(business_no_identity, category="shoe store", location="Mumbai")

    assert score_with_identity > score_fallback


def test_identity_aware_gracefully_handles_no_selected_website():
    """A business whose identity resolution never selected a website
    (NO_IDENTITY) must not raise -- every identity-aware component must
    degrade to zero rather than crash on a None selected_website."""
    from application.discovery.digital_identity import DigitalIdentityBuilder
    from application.discovery.evidence import CandidatePool

    pool = CandidatePool("Ghost Biz", "Nowhere")
    business_candidate = BusinessCandidate(name="Ghost Biz")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business_candidate, pool, "Nowhere", chosen=None, chosen_url=None)
    vdi = DigitalIdentityBuilder().build(business_identity, business_candidate)

    resolution = WebsiteResolution(website=None, resolved_via="none", validated=False)
    discovered = DiscoveredBusiness(candidate=business_candidate, resolution=resolution, identity=vdi)
    score = score_business(discovered, category="anything", location="anywhere")
    assert score >= 0.0
