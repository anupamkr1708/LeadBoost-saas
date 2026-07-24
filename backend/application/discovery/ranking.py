"""
Business Ranking.

Pure, deterministic scoring -- no AI. Used only when discovery finds more
validated businesses than the requested limit, to decide which ones to
keep. Every factor is simple and explainable by design (this mirrors the
same "no black-box outputs" spirit as application.explainability, just
without needing the DTO machinery since there's no LLM involved here).

Signals combined (per PART 5 of the discovery-quality brief -- never a
single score on its own):
  - has a validated website at all
  - category match (name/category text overlap with the query)
  - location match (address text overlap with the query)
  - domain quality / business-name similarity: how strongly the
    website's domain actually reflects the business's own name
    (application.discovery.grounding.brand_match_strength) -- this is
    what keeps a generic, low-information domain from outscoring a
    genuine brand match, without needing a hardcoded directory list here
    (that filtering already happened earlier, in website_validator.py).
  - rating / review count / contact completeness
"""

import math
from typing import List

from application.discovery import grounding
from application.discovery.dto import DiscoveredBusiness
from application.discovery.website_validator import domain_of

_HAS_WEBSITE_POINTS = 40.0
_CATEGORY_MATCH_POINTS = 20.0
_LOCATION_MATCH_POINTS = 15.0
_DOMAIN_BRAND_MATCH_MAX_POINTS = 15.0
_RATING_MAX_POINTS = 10.0
_REVIEW_COUNT_MAX_POINTS = 10.0
_CONTACT_COMPLETENESS_MAX_POINTS = 15.0


def score_business(business: DiscoveredBusiness, category: str, location: str) -> float:
    score = 0.0
    candidate = business.candidate
    website = business.resolution.website if business.resolution.validated else None

    if business.resolution.validated and website:
        score += _HAS_WEBSITE_POINTS

    if candidate.category and _text_overlap(candidate.category, category):
        score += _CATEGORY_MATCH_POINTS
    elif _text_overlap(candidate.name, category):
        score += _CATEGORY_MATCH_POINTS * 0.5

    location_matched = bool(candidate.address and _text_overlap(candidate.address, location))
    if location_matched:
        score += _LOCATION_MATCH_POINTS
    elif website and grounding.location_mentioned(website, location):
        # No structured address (e.g. a fallback-search-sourced
        # candidate), but the location is corroborated by the domain
        # itself (e.g. "metroshoesmumbai.com") -- worth a partial credit,
        # not the full bonus a real address gives.
        score += _LOCATION_MATCH_POINTS * 0.5

    if website:
        brand_strength = grounding.brand_match_strength(candidate.name, domain_of(website))
        score += brand_strength * _DOMAIN_BRAND_MATCH_MAX_POINTS

    if candidate.rating is not None:
        # Ratings are typically 0-5; scale to the points budget.
        score += min(max(candidate.rating, 0.0) / 5.0 * _RATING_MAX_POINTS, _RATING_MAX_POINTS)

    if candidate.review_count:
        # log-scaled so a handful of extra reviews doesn't dominate the score.
        score += min(math.log1p(candidate.review_count) * 2.0, _REVIEW_COUNT_MAX_POINTS)

    contact_fields_present = sum(1 for v in (candidate.phone, candidate.address, website) if v)
    score += (contact_fields_present / 3.0) * _CONTACT_COMPLETENESS_MAX_POINTS

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


def _text_overlap(text: str, query: str) -> bool:
    text_words = {w.lower() for w in text.split() if len(w) > 2}
    query_words = {w.lower() for w in query.split() if len(w) > 2}
    return bool(text_words & query_words)
