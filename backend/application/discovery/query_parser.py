"""
Query Parser.

Deterministic parsing of a natural-language business search into
(category, location, limit, modifier). Pure regex + a small fixed
location dictionary (application.discovery.locations) -- no LLM, no
spaCy/NER model, no external calls, fully unit-testable.

Handles, in order of how the parse is attempted:

  1. Filler-phrase stripping: leading conversational filler ("I need",
     "Looking for", "Find", "Show me", ...) is removed before pattern
     matching, so "Need software companies near Bangalore" is parsed the
     same as "software companies near Bangalore".

  2. Preposition-based patterns (as before, but with a wider set of
     recognized prepositions): "in", "near", "around", "close to",
     "located in", "situated in" -- optionally prefixed with a leading
     count. The count can be spelled as "top <N>", "best <N>", or just a
     bare "<N>" -- "top"/"best" are informational synonyms, not required.
        "Top shoe stores in Mumbai"
        "Top 20 coffee shops around Pune"
        "Find me the best 10 gyms in Delhi"
        "Need law firms located in Chennai"
        "Restaurants close to Mumbai Airport"

  3. No-preposition, gazetteer-assisted split: when neither a filler
     phrase nor a recognized preposition is present, the query is split
     on a known city name (application.discovery.locations.KNOWN_LOCATIONS)
     found as a trailing or leading phrase.
        "Software companies Noida"      -> category="Software companies", location="Noida"
        "Noida software companies"      -> category="software companies", location="Noida"
        "Dental clinics Bangalore"
        "Real estate agents Kolkata"

  4. Placeholder-location rejection: a location that resolves to a
     non-place placeholder ("me", "here", "nearby", "my area", ...) is
     rejected with a clear QueryParseError rather than silently searched
     for -- Overpass/the fallback providers have no way to geocode "me",
     so accepting it would either return nothing or (worse) something
     ungrounded. The person is asked for an actual city/area name instead.

This is a deliberately small, fixed set of patterns/dictionary lookups --
not a general-purpose NLP parser -- so it stays fast, dependency-free, and
easy to reason about.
"""

import re
from typing import List, Optional, Tuple

from application.discovery.dto import ParsedQuery
from application.discovery.exceptions import QueryParseError
from application.discovery.locations import KNOWN_LOCATIONS

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

_FILLER_PREFIX_PATTERNS = [
    re.compile(r"^i\s+(?:want|need)\s+(?:to\s+find\s+)?", re.IGNORECASE),
    re.compile(r"^looking\s+for\s+", re.IGNORECASE),
    re.compile(r"^find\s+(?:me\s+)?", re.IGNORECASE),
    re.compile(r"^need\s+", re.IGNORECASE),
    re.compile(r"^show\s+me\s+", re.IGNORECASE),
    re.compile(r"^give\s+me\s+", re.IGNORECASE),
    re.compile(r"^search\s+for\s+", re.IGNORECASE),
    re.compile(r"^get\s+me\s+", re.IGNORECASE),
]

# Wider preposition set than the original "in"-only version. Order within
# the alternation doesn't matter for correctness here (each keyword has a
# distinct enough shape that Python's re won't cross-match them), but
# longer/more-specific phrases are listed first for readability.
_LOCATION_PREPOSITION = r"(?:located\s+in|situated\s+in|close\s+to|around|near|in)"

# "top"/"best" are interchangeable count qualifiers; the count itself may
# also appear with no qualifier word at all ("10 gyms in Delhi"). Matched
# as a single alternation so a category can never itself start by
# swallowing a leading digit unless one of these three shapes is present.
_QUALIFIER_N_PATTERN = re.compile(
    rf"^\s*(?:(?:top|best)\s+)?(\d+)\s+(.+?)\s+{_LOCATION_PREPOSITION}\s+(.+?)\s*$",
    re.IGNORECASE,
)
_TOP_PATTERN = re.compile(
    rf"^\s*(?:top|best)\s+(.+?)\s+{_LOCATION_PREPOSITION}\s+(.+?)\s*$", re.IGNORECASE
)
_PLAIN_PATTERN = re.compile(rf"^\s*(.+?)\s+{_LOCATION_PREPOSITION}\s+(.+?)\s*$", re.IGNORECASE)

# Words/phrases that are grammatically valid "location" slots but are not
# an actual place -- accepting them would send an ungrounded location like
# "me" or "here" straight to Overpass/the fallback search, which would
# either silently return nothing or (worse) whatever happens to be tagged
# with that literal string. Checked case-insensitively, after cleaning.
_NON_LOCATION_PLACEHOLDERS = {
    "me", "here", "there", "nearby", "us", "you",
    "my area", "my city", "my location", "this area", "this city",
    "my region", "around me", "near me",
}


class QueryParser:
    """Parses natural-language business-discovery queries. Stateless."""

    def parse(self, query: str, limit_override: "int | None" = None) -> ParsedQuery:
        if not query or not query.strip():
            raise QueryParseError("Query must not be empty")

        raw = self._strip_filler_prefix(query.strip())
        # A leading article can be left behind by filler-stripping alone
        # ("Find me the best 10 gyms..." -> "the best 10 gyms...") --
        # strip it here too (not just in _clean, which only runs on the
        # already-split category/location) so it doesn't break the
        # qualifier/number pattern's anchor at the start of the string.
        raw = re.sub(r"^(?:the|a|an)\s+", "", raw, flags=re.IGNORECASE)

        match = _QUALIFIER_N_PATTERN.match(raw)
        if match:
            limit_str, category, location = match.groups()
            limit = int(limit_str)
            modifier = "top"
        else:
            match = _TOP_PATTERN.match(raw)
            if match:
                category, location = match.groups()
                limit = _DEFAULT_LIMIT
                modifier = "top"
            else:
                match = _PLAIN_PATTERN.match(raw)
                if match:
                    category, location = match.groups()
                    limit = _DEFAULT_LIMIT
                    modifier = None
                else:
                    split = self._split_without_preposition(raw)
                    if split is None:
                        raise QueryParseError(
                            f"Could not parse query: '{query}'. Expected a shape like "
                            f"'<category> in <location>' (optionally prefixed with 'top', "
                            f"'best', or a plain number, e.g. 'top 10' / 'best 10' / '10'; "
                            f"'in' can also be 'near', 'around', 'close to', 'located in', "
                            f"or 'situated in'), or '<category> <location>' when the "
                            f"location is a recognized city."
                        )
                    category, location = split
                    limit = _DEFAULT_LIMIT
                    modifier = None

        category = self._clean(category)
        location = self._clean(location)

        if not category or not location:
            raise QueryParseError(f"Could not extract both a category and a location from: '{query}'")

        if location.lower() in _NON_LOCATION_PLACEHOLDERS:
            raise QueryParseError(
                f"'{location}' isn't a specific place I can search. Please include a city "
                f"or area name instead, e.g. '{category} in Mumbai'."
            )

        if limit_override is not None:
            limit = limit_override
        limit = max(1, min(limit, _MAX_LIMIT))

        return ParsedQuery(
            category=category,
            location=location,
            limit=limit,
            modifier=modifier,
            raw_query=query,
        )

    @staticmethod
    def _strip_filler_prefix(text: str) -> str:
        for pattern in _FILLER_PREFIX_PATTERNS:
            stripped = pattern.sub("", text, count=1)
            if stripped != text:
                return stripped.strip()
        return text

    @staticmethod
    def _split_without_preposition(text: str) -> Optional[Tuple[str, str]]:
        """Gazetteer-assisted split for queries with no preposition at
        all. Tries a trailing location phrase first (2 words, then 1),
        then a leading location phrase (2 words, then 1)."""
        words = text.split()
        if len(words) < 2:
            return None

        for n in (2, 1):
            if len(words) > n:
                candidate_location = " ".join(words[-n:])
                if candidate_location.lower() in KNOWN_LOCATIONS:
                    category = " ".join(words[:-n])
                    if category.strip():
                        return category, candidate_location

        for n in (2, 1):
            if len(words) > n:
                candidate_location = " ".join(words[:n])
                if candidate_location.lower() in KNOWN_LOCATIONS:
                    category = " ".join(words[n:])
                    if category.strip():
                        return category, candidate_location

        return None

    @staticmethod
    def _clean(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        # Strip a handful of common trailing/leading filler words that
        # don't change the search intent.
        text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.IGNORECASE)
        return text.strip(" .,")