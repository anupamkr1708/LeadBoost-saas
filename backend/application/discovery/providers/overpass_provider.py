"""
Overpass Provider (primary business search).

Queries the OpenStreetMap Overpass API for businesses matching a category
within a named location, returning as many normalized BusinessCandidates
as it can. Deterministic: category -> OSM tag mapping is a fixed table,
not an LLM classification.

Reuses application.utils.retry.with_retry (existing, generic) for
transient-failure resilience -- Overpass mirrors are occasionally slow or
rate-limited, so a couple of retries with backoff meaningfully improves
reliability without introducing a new resilience pattern.
"""

import os
from typing import Dict, List, Optional, Tuple

import aiohttp

from application.discovery.business_normalizer import normalize_overpass_element
from application.discovery.dto import BusinessCandidate
from application.discovery.exceptions import ProviderError
from application.discovery.providers.base import BusinessSearchProvider
from application.utils.retry import with_retry
from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.overpass")

# keyword (substring match against the lowercased category) -> (OSM key, value)
# Order matters: more specific phrases are checked before shorter/looser ones.
_CATEGORY_TAG_MAP: Dict[str, Tuple[str, str]] = {
    "real estate": ("office", "estate_agent"),
    "estate agen": ("office", "estate_agent"),
    "accounting": ("office", "accountant"),
    "accountant": ("office", "accountant"),
    "law firm": ("office", "lawyer"),
    "lawyer": ("office", "lawyer"),
    "attorney": ("office", "lawyer"),
    "travel agen": ("office", "travel_agent"),
    "insurance": ("office", "insurance"),
    "shoe": ("shop", "shoes"),
    "cloth": ("shop", "clothes"),
    "grocery": ("shop", "supermarket"),
    "supermarket": ("shop", "supermarket"),
    "bakery": ("shop", "bakery"),
    "book": ("shop", "books"),
    "electronics": ("shop", "electronics"),
    "jewel": ("shop", "jewelry"),
    "furniture": ("shop", "furniture"),
    "florist": ("shop", "florist"),
    "hairdresser": ("shop", "hairdresser"),
    "salon": ("shop", "hairdresser"),
    "barber": ("shop", "hairdresser"),
    "car repair": ("shop", "car_repair"),
    "mechanic": ("shop", "car_repair"),
    "car dealer": ("shop", "car"),
    "dentist": ("amenity", "dentist"),
    "hotel": ("tourism", "hotel"),
    "restaurant": ("amenity", "restaurant"),
    "cafe": ("amenity", "cafe"),
    "coffee": ("amenity", "cafe"),
    "pharmacy": ("amenity", "pharmacy"),
    "clinic": ("amenity", "clinic"),
    "hospital": ("amenity", "hospital"),
    "bank": ("amenity", "bank"),
    "bar": ("amenity", "bar"),
    "pub": ("amenity", "pub"),
    "school": ("amenity", "school"),
    "gas station": ("amenity", "fuel"),
    "petrol": ("amenity", "fuel"),
    "veterinary": ("amenity", "veterinary"),
    "vet ": ("amenity", "veterinary"),
    "gym": ("leisure", "fitness_centre"),
    "fitness": ("leisure", "fitness_centre"),
    "spa": ("leisure", "spa"),
}


def resolve_osm_tag(category: str) -> Tuple[Optional[str], Optional[str]]:
    """Deterministic category -> (OSM key, value) mapping. Returns
    (None, None) if nothing matches -- the caller then falls back to a
    name-text search instead of a tag match."""
    lowered = category.lower()
    for keyword, tag in _CATEGORY_TAG_MAP.items():
        if keyword in lowered:
            return tag
    return None, None


def _escape(value: str) -> str:
    """Overpass QL string literals are double-quoted; strip characters
    that would break out of the literal rather than trying to fully
    escape Overpass QL syntax."""
    return value.replace('"', "").replace("\\", "").replace("\n", " ").strip()


def build_overpass_query(category: str, location: str, result_limit: int) -> str:
    location_escaped = _escape(location)
    tag_key, tag_value = resolve_osm_tag(category)

    clauses = []
    if tag_key and tag_value:
        clauses.append(f'nwr["{tag_key}"="{tag_value}"](area.searchArea);')
    else:
        # No known tag mapping -- fall back to a case-insensitive name
        # search so unusual/unmapped categories still return *something*
        # rather than nothing.
        category_escaped = _escape(category)
        clauses.append(f'nwr["name"~"{category_escaped}",i](area.searchArea);')

    clauses_str = "\n  ".join(clauses)

    return (
        "[out:json][timeout:25];\n"
        f'area["name"="{location_escaped}"]->.searchArea;\n'
        "(\n"
        f"  {clauses_str}\n"
        ");\n"
        f"out center {result_limit};"
    )


class OverpassProvider(BusinessSearchProvider):
    name = "overpass"

    def __init__(self, api_url: Optional[str] = None, timeout: int = 30):
        self.api_url = api_url or os.getenv(
            "OVERPASS_API_URL", "https://overpass-api.de/api/interpreter"
        )
        self.timeout = timeout

    async def search(self, category: str, location: str, limit: int) -> List[BusinessCandidate]:
        # Overpass returns unnamed/duplicate-ish POIs fairly often; over
        # -fetch a bit so normalization/dedup/ranking downstream still has
        # `limit` good candidates to work with.
        result_limit = min(max(limit * 3, limit), 200)
        query = build_overpass_query(category, location, result_limit)

        try:
            elements = await self._fetch_with_retry(query)
        except Exception as e:
            raise ProviderError(self.name, f"Overpass search failed: {e}") from e

        candidates: List[BusinessCandidate] = []
        for element in elements:
            candidate = normalize_overpass_element(element, category)
            if candidate is not None:
                candidates.append(candidate)

        logger.info(
            "Overpass search completed",
            extra={
                "event": "discovery_provider_search",
                "provider": self.name,
                "category": category,
                "location": location,
                "elements_returned": len(elements),
                "candidates_normalized": len(candidates),
            },
        )
        return candidates

    @with_retry(exceptions=(aiohttp.ClientError, TimeoutError), attempts=3, min_wait=1.0, max_wait=8.0)
    async def _fetch_with_retry(self, query: str) -> list:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            async with session.post(self.api_url, data=query) as response:
                if response.status != 200:
                    raise aiohttp.ClientError(f"Overpass returned HTTP {response.status}")
                payload = await response.json(content_type=None)
                return payload.get("elements", [])
