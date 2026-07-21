"""
Query Parser.

Deterministic parsing of a natural-language business search into
(category, location, limit, modifier). Pure regex -- no LLM, no spaCy (a
model-download dependency would be overkill for a fixed "<category> in
<location>" sentence shape), no external calls, fully unit-testable.

Handles:
    "Top shoe stores in Mumbai"          -> category="shoe stores", location="Mumbai", modifier="top"
    "Top 20 shoe stores in Mumbai"       -> limit=20, modifier="top"
    "Dentists in Pune"                   -> category="dentists", location="Pune"
    "Hotels in Goa"
    "Restaurants in Jaipur"
    "Real estate agencies in Bangalore"
    "Accounting firms in Chennai"
"""

import re

from application.discovery.dto import ParsedQuery
from application.discovery.exceptions import QueryParseError

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100

# Tried in order, most specific first.
_TOP_N_PATTERN = re.compile(r"^\s*top\s+(\d+)\s+(.+?)\s+in\s+(.+?)\s*$", re.IGNORECASE)
_TOP_PATTERN = re.compile(r"^\s*top\s+(.+?)\s+in\s+(.+?)\s*$", re.IGNORECASE)
_PLAIN_PATTERN = re.compile(r"^\s*(.+?)\s+in\s+(.+?)\s*$", re.IGNORECASE)


class QueryParser:
    """Parses natural-language business-discovery queries. Stateless."""

    def parse(self, query: str, limit_override: "int | None" = None) -> ParsedQuery:
        if not query or not query.strip():
            raise QueryParseError("Query must not be empty")

        raw = query.strip()

        match = _TOP_N_PATTERN.match(raw)
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
                if not match:
                    raise QueryParseError(
                        f"Could not parse query: '{query}'. Expected a shape like "
                        f"'<category> in <location>' (optionally prefixed with 'top' "
                        f"or 'top <N>')."
                    )
                category, location = match.groups()
                limit = _DEFAULT_LIMIT
                modifier = None

        category = self._clean(category)
        location = self._clean(location)

        if not category or not location:
            raise QueryParseError(f"Could not extract both a category and a location from: '{query}'")

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
    def _clean(text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        # Strip a handful of common trailing/leading filler words that
        # don't change the search intent.
        text = re.sub(r"^(the|a|an)\s+", "", text, flags=re.IGNORECASE)
        return text.strip(" .,")
