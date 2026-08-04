"""
Grounding utilities.

Shared, deterministic checks used by both Serper's candidate-selection
scoring (providers/serper_provider.py) and the final business ranking
(ranking.py) to answer one question in two different contexts: "does this
website/domain plausibly belong to *this actual* business, in *this
actual* location" -- as opposed to a same-named-but-different business, a
generic directory, or an unrelated global brand that happens to share a
common word.

Consolidated here, rather than duplicated per-caller, specifically because
grounding quality was the highest-priority problem: naive substring
matching previously let a Mumbai shoe store named "Regal" match an
unrelated US cinema chain's domain (because the word "regal" appeared in
the result's title), and let "Hollywood Walk of Shame" match
walkoffame.com (because "walk" is a substring of "walkoffame"). Both are
now rejected by the stricter checks below.
"""

import re
from typing import List, Optional

from application.discovery import canonicalization

# Generic words that carry no brand-identifying signal on their own.
# Includes both generic corporate suffixes (store/company/pvt/ltd/...)
# and generic business-*type* descriptors (hospital/hotel/clinic/...):
# a domain containing "hospital" is not meaningfully more likely to
# belong to *this* hospital than to any other one -- these words appear
# in the name of nearly every business in their category, so treating
# them as a brand-matching signal would let a same-category-different-
# city business collide (e.g. a domain merely containing "hospital"
# should not count as evidence for "Guru Gobind Singh Hospital" without
# one of the *actual* distinguishing name words also matching). Mirrors
# the category vocabulary already used by
# application.discovery.providers.overpass_provider._CATEGORY_TAG_MAP,
# expressed as the natural-language words that appear in business names,
# rather than a second, separately-maintained list.
_STOPWORDS = {
    "the", "and", "of", "a", "an", "in", "at", "for", "&",
    "store", "stores", "shop", "shops", "company", "companies",
    "pvt", "ltd", "llc", "inc", "co",
    "hospital", "hospitals", "clinic", "clinics", "hotel", "hotels",
    "restaurant", "restaurants", "cafe", "cafes", "bank", "banks",
    "school", "schools", "pharmacy", "pharmacies", "gym", "gyms",
    "salon", "salons", "spa", "spas", "bar", "bars", "pub", "pubs",
    "firm", "firms", "agency", "agencies", "services", "solutions",
    "group", "dental", "dentist", "dentists", "law", "legal",
    "estate", "insurance", "travel", "electronics", "furniture",
    "jewelry", "jewellers", "bakery", "grocery", "supermarket",
    "clothing", "clothes", "fitness", "wellness", "care", "center",
    "centre", "centers", "centres",
}

# Words shorter than this are excluded from brand-matching entirely --
# short common words ("walk", "star", "one", "sun", "look") are far too
# likely to appear as an incidental substring of an unrelated domain.
_MIN_BRAND_WORD_LEN = 4

# Minimum length for the stronger prefix/suffix match tier (see
# brand_match_strength). Slightly higher than _MIN_BRAND_WORD_LEN: a
# 4-letter word being a coincidental *prefix* of an unrelated domain is
# still too easy (e.g. "walk" prefixes "walkoffame.com"), but 5+ letter
# prefixes/suffixes are a genuinely strong, low-false-positive signal for
# the very common "brand + descriptive suffix" domain pattern
# (mochishoes.com, woodlandworldwide.com, bataindia.com).
_MIN_PREFIX_WORD_LEN = 5


def significant_words(text: Optional[str]) -> List[str]:
    """Lowercased alphanumeric tokens with stopwords removed."""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z0-9]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) >= 2]


def domain_root(domain: str) -> str:
    """The brand-identifying label of `domain` -- e.g. "mochishoes" for
    both "mochishoes.com" and a subdomain-hosted "store.mochishoes.com".
    Delegates to canonicalization.root_label() (registrable-domain-aware,
    already handles compound suffixes like .co.in/.edu.in) rather than
    naively taking the first dot-separated label, which previously
    returned the subdomain itself ("store") for any subdomain-hosted
    site -- silently zeroing out brand_match_strength() for real,
    reachable, correctly-branded candidates."""
    return canonicalization.root_label(domain) if domain else ""


def brand_match_strength(business_name: str, domain: str) -> float:
    """
    Returns 0.0-1.0: how strongly the business name matches the domain's
    root token. Only words at least `_MIN_BRAND_WORD_LEN` characters long
    are eligible, and a partial (non-exact) match only counts when the
    shorter of {token, domain_root} makes up a clear majority of the
    longer one -- not merely "appears somewhere inside it". This is what
    stops "walk" from matching inside "walkoffame": "walk" (4 chars) is
    only ~36% of "walkoffame" (10 chars), well under the 0.6 threshold.

    Checks both individual words AND all significant words joined
    together, hyphens/underscores stripped from the domain root, so
    legitimate multi-word-name-to-concatenated-domain patterns
    ("Metro Shoes" -> metroshoes.com) and hyphenated domains
    ("Super Sale Shop" -> super-sale.com) still match correctly.
    """
    root = domain_root(domain)
    if not root:
        return 0.0
    root_compact = root.replace("-", "").replace("_", "")

    words = significant_words(business_name)
    eligible_words = [w for w in words if len(w) >= _MIN_BRAND_WORD_LEN]
    joined = "".join(words)

    candidates = list(eligible_words)
    if len(joined) >= _MIN_BRAND_WORD_LEN and joined not in candidates:
        candidates.append(joined)
    if not candidates:
        return 0.0

    best = 0.0
    for token in candidates:
        for target in (root, root_compact):
            if not target:
                continue
            if token == target:
                best = max(best, 1.0)
                continue
            # Strong signal: token is a genuine prefix or suffix of the
            # domain root -- covers the very common "brand + descriptive
            # suffix" domain pattern (mochishoes.com, woodlandworldwide.com,
            # bataindia.com). Requires a slightly higher minimum length
            # than the general substring check below, since a short/common
            # word as a *coincidental* prefix (e.g. "walk" of
            # "walkoffame") is not a reliable signal on its own.
            if len(token) >= _MIN_PREFIX_WORD_LEN and (
                target.startswith(token) or target.endswith(token)
            ):
                coverage = min(len(token) / len(target), 1.0)
                best = max(best, 0.75 + 0.15 * coverage)
                continue
            # Weaker signal: token appears anywhere within target (not
            # necessarily prefix/suffix), only counted when it makes up a
            # clear majority of the shorter/longer pair.
            shorter, longer = (token, target) if len(token) <= len(target) else (target, token)
            if shorter and shorter in longer and len(shorter) / len(longer) >= 0.6:
                best = max(best, (len(shorter) / len(longer)) * 0.85)
    return round(min(best, 1.0), 3)


def location_mentioned(text: Optional[str], location: str) -> bool:
    """Whether the location's primary (first, usually most specific) word
    appears in `text` -- e.g. "mumbai" out of "Mumbai Airport"."""
    if not text or not location:
        return False
    location_words = significant_words(location)
    if not location_words:
        return False
    return location_words[0] in text.lower()


def is_low_signal_business_name(business_name: str) -> bool:
    """True when the business name is short/generic enough (e.g. a single
    common word, or a name pattern like "<Person's Name> Hospital" that
    repeats identically across many cities) that a domain/title match
    alone is not trustworthy without a corroborating location signal."""
    words = significant_words(business_name)
    long_words = [w for w in words if len(w) >= _MIN_BRAND_WORD_LEN]
    return len(long_words) <= 1