"""
Business Ranking.

Pure, deterministic scoring -- no AI. Used only when discovery finds more
validated businesses than the requested limit, to decide which ones to
keep. Every factor is simple and explainable by design (this mirrors the
same "no black-box outputs" spirit as application.explainability, just
without needing the DTO machinery since there's no LLM involved here).

Signals combined (never a single score on its own):
  - has a validated website at all, and how strongly it verified
  - category match (name/category text overlap with the query)
  - location match (address text overlap with the query)
  - domain quality / business-name similarity: how strongly the
    website's domain actually reflects the business's own name
  - rating / review count / contact completeness
  - how internally stable/uncontested the website selection itself was

Sprint 4 (Discovery Quality Refinement)
-----------------------------------------
`business.identity` (`DiscoveredBusiness.identity`, populated by
`discovery_service.py`'s `_resolve_and_validate` via
`WebsiteResolver.resolve_with_digital_identity()`) is the canonical
`application.discovery.digital_identity.VerifiedDigitalIdentity` for this
business -- already computed, already verified, already scored, upstream
of ranking entirely. Every component below reuses a signal already
sitting on it instead of recomputing an equivalent one from raw
`candidate`/`resolution` fields a second time:

  - website verification: `identity.verification_quality.strength`
    (Sprint 2C) replaces a flat, binary "has a validated website" bonus
    with the actual, continuous strength of the verification evidence
    behind it.
  - domain/brand match: `identity.feature_store`'s own
    `FeatureId.BRAND_SIMILARITY` (Sprint 2B) replaces a second call to
    `grounding.brand_match_strength` for the exact same (business name,
    domain) pair already evaluated once during resolution.
  - location match: `FeatureId.LOCATION_CONSISTENCY` (Sprint 2B) replaces
    ranking's own independent address/domain-text overlap check with the
    identical full-credit/half-credit signal
    `evidence.location_corroboration_evidence` already derived for this
    exact (location, domain, address) triple -- see that function's own
    docstring, which already noted the two were computing the same thing
    twice.
  - contact/record completeness: `identity.identity_completeness.
    overall_score` (Sprint 2C, 8 weighted dimensions: website, phone,
    address, coordinates, provider support, verification coverage,
    evidence coverage, confidence coverage) replaces ranking's own
    narrower 3-field (phone/address/website) presence count.
  - identity stability (new component, no pre-Sprint-4 equivalent):
    `identity.quality.identity_stability` (Sprint 3/4,
    `identity_resolution_engine.py` -- competition margin, consensus,
    evidence diversity, and conflict combined) rewards a business whose
    website selection was a clear, uncontested pick over an equally
    -scored one that was a close, conflicted call. This is the one
    genuinely new signal (goal #1's "...and IdentityResolution outputs"),
    not a replacement for an existing computation.

Category match (does this business match what was searched for) and
rating/review-count scoring have no upstream equivalent anywhere in the
Evidence/Feature/Verification/Confidence pipeline -- that pipeline is
entirely about website *identity*, never about search-query relevance or
Overpass-sourced rating data -- so both stay exactly as before.

Every component gracefully falls back to the exact, original raw-field
computation whenever `business.identity` is absent (a caller still using
plain `resolve()`, or a hand-constructed `DiscoveredBusiness` in a test)
-- `score_business`/`rank_businesses`'s public signatures are completely
unchanged, and neither requires an identity to be present. This is a
pure quality improvement, not a new abstraction: no new class, no new
layer, just reading a richer, already-computed number instead of a
cruder, freshly-recomputed one when it's available.
"""

import math
from typing import List, Optional

from application.discovery import grounding
from application.discovery.dto import DiscoveredBusiness
from application.discovery.features import FeatureId
from application.discovery.website_validator import domain_of

_HAS_WEBSITE_POINTS = 40.0
_CATEGORY_MATCH_POINTS = 20.0
_LOCATION_MATCH_POINTS = 15.0
_DOMAIN_BRAND_MATCH_MAX_POINTS = 15.0
_RATING_MAX_POINTS = 10.0
_REVIEW_COUNT_MAX_POINTS = 10.0
_CONTACT_COMPLETENESS_MAX_POINTS = 15.0
# New in Sprint 4 -- see module docstring's "identity stability" bullet.
# Kept modest relative to the other components (well under any one of
# them) so it can only ever refine an ordering that's already close, the
# same "bounded nudge" discipline every weight in the discovery package
# already follows.
_IDENTITY_STABILITY_MAX_POINTS = 10.0

# A validated website is always worth at least this fraction of
# _HAS_WEBSITE_POINTS, scaling up to the full amount for a strongly
# verified one -- so a business with a genuinely weak-but-passing
# verification never scores below a majority of what the old flat bonus
# gave every validated site, while a strongly verified one now scores
# meaningfully higher. Fixed and documented, never learned.
_VERIFIED_WEBSITE_MIN_FRACTION = 0.6


def score_business(business: DiscoveredBusiness, category: str, location: str) -> float:
    score = 0.0
    candidate = business.candidate
    identity = business.identity  # Sprint 4: Optional[VerifiedDigitalIdentity]
    website = business.resolution.website if business.resolution.validated else None

    score += _website_verification_score(business, website, identity)

    if candidate.category and _text_overlap(candidate.category, category):
        score += _CATEGORY_MATCH_POINTS
    elif _text_overlap(candidate.name, category):
        score += _CATEGORY_MATCH_POINTS * 0.5

    score += _location_score(candidate, website, location, identity)
    score += _domain_brand_score(candidate, website, identity)

    if candidate.rating is not None:
        # Ratings are typically 0-5; scale to the points budget.
        score += min(max(candidate.rating, 0.0) / 5.0 * _RATING_MAX_POINTS, _RATING_MAX_POINTS)

    if candidate.review_count:
        # log-scaled so a handful of extra reviews doesn't dominate the score.
        score += min(math.log1p(candidate.review_count) * 2.0, _REVIEW_COUNT_MAX_POINTS)

    score += _contact_completeness_score(candidate, website, identity)
    score += _identity_stability_score(identity)

    return round(score, 2)


def rank_businesses(
    businesses: List[DiscoveredBusiness], category: str, location: str
) -> List[DiscoveredBusiness]:
    """Scores every business in place (`rank_score`) and returns a new list
    sorted best-first. Does not filter -- callers decide what to do with
    duplicates/unvalidated entries before or after ranking."""
    for business in businesses:
        business.rank_score = score_business(business, category, location)
    return sorted(businesses, key=lambda b: b.rank_score, reverse=True)


# ---------------------------------------------------------------------------
# Scoring components -- each one identity-aware-with-fallback, per the
# module docstring above.
# ---------------------------------------------------------------------------


def _website_verification_score(business: DiscoveredBusiness, website: Optional[str], identity) -> float:
    if identity is not None:
        if identity.verification_quality.passed and identity.selected_website:
            strength = max(0.0, min(identity.verification_quality.strength, 1.0))
            fraction = _VERIFIED_WEBSITE_MIN_FRACTION + (1.0 - _VERIFIED_WEBSITE_MIN_FRACTION) * strength
            return _HAS_WEBSITE_POINTS * fraction
        return 0.0
    if business.resolution.validated and website:
        return _HAS_WEBSITE_POINTS
    return 0.0


def _selected_feature(identity, feature_id: FeatureId):
    """The selected website's own already-extracted feature, or None when
    unavailable -- never fabricated, never re-extracted."""
    if identity is None or not identity.selected_website:
        return None
    feature_set = identity.feature_store.get(domain_of(identity.selected_website))
    if feature_set is None:
        return None
    return feature_set.by_id(feature_id)


def _location_score(candidate, website: Optional[str], location: str, identity) -> float:
    if identity is not None:
        feature = _selected_feature(identity, FeatureId.LOCATION_CONSISTENCY)
        if feature is not None:
            return (feature.normalized_value or 0.0) * _LOCATION_MATCH_POINTS
        return 0.0
    # Fallback: original raw-field computation, byte-for-byte unchanged.
    if candidate.address and _text_overlap(candidate.address, location):
        return _LOCATION_MATCH_POINTS
    if website and grounding.location_mentioned(website, location):
        return _LOCATION_MATCH_POINTS * 0.5
    return 0.0


def _domain_brand_score(candidate, website: Optional[str], identity) -> float:
    if identity is not None:
        feature = _selected_feature(identity, FeatureId.BRAND_SIMILARITY)
        if feature is not None:
            return (feature.normalized_value or 0.0) * _DOMAIN_BRAND_MATCH_MAX_POINTS
        return 0.0
    if website:
        brand_strength = grounding.brand_match_strength(candidate.name, domain_of(website))
        return brand_strength * _DOMAIN_BRAND_MATCH_MAX_POINTS
    return 0.0


def _contact_completeness_score(candidate, website: Optional[str], identity) -> float:
    if identity is not None:
        return identity.identity_completeness.overall_score * _CONTACT_COMPLETENESS_MAX_POINTS
    contact_fields_present = sum(1 for v in (candidate.phone, candidate.address, website) if v)
    return (contact_fields_present / 3.0) * _CONTACT_COMPLETENESS_MAX_POINTS


def _identity_stability_score(identity) -> float:
    """New in Sprint 4 -- see module docstring. Zero (not a penalty)
    whenever no identity is available, so this is a purely additive
    signal relative to pre-Sprint-4 scoring."""
    if identity is None:
        return 0.0
    return identity.quality.identity_stability * _IDENTITY_STABILITY_MAX_POINTS


def _text_overlap(text: str, query: str) -> bool:
    text_words = {w.lower() for w in text.split() if len(w) > 2}
    query_words = {w.lower() for w in query.split() if len(w) > 2}
    return bool(text_words & query_words)
