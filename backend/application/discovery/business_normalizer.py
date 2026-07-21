"""
Business Normalizer.

Pure functions that turn a raw OpenStreetMap/Overpass element (a dict of
loosely-structured `tags`) into a clean, typed BusinessCandidate. No
network calls, no side effects -- easy to unit test against fixture JSON.
"""

from typing import Any, Dict, Optional

from application.discovery.dto import BusinessCandidate


def normalize_overpass_element(element: Dict[str, Any], category: str) -> Optional[BusinessCandidate]:
    """Convert one Overpass API `element` (a node/way/relation with `tags`)
    into a BusinessCandidate. Returns None if the element has no usable
    name (Overpass frequently returns unnamed POIs that match a tag filter
    but carry no business identity worth surfacing)."""
    tags = element.get("tags") or {}
    name = tags.get("name")
    if not name or not name.strip():
        return None

    lat, lon = _extract_coordinates(element)

    return BusinessCandidate(
        name=name.strip(),
        category=category,
        address=_build_address(tags),
        phone=_first_present(tags, ["phone", "contact:phone"]),
        website=_normalize_url(_first_present(tags, ["website", "contact:website", "url"])),
        latitude=lat,
        longitude=lon,
        rating=None,  # OpenStreetMap does not carry review ratings
        review_count=None,
        source="overpass",
    )


def _extract_coordinates(element: Dict[str, Any]) -> tuple:
    if "lat" in element and "lon" in element:
        return element.get("lat"), element.get("lon")
    center = element.get("center") or {}
    return center.get("lat"), center.get("lon")


def _build_address(tags: Dict[str, str]) -> Optional[str]:
    if tags.get("addr:full"):
        return tags["addr:full"].strip()

    parts = [
        tags.get("addr:housenumber"),
        tags.get("addr:street"),
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:postcode"),
    ]
    parts = [p.strip() for p in parts if p and p.strip()]
    return ", ".join(parts) if parts else None


def _first_present(tags: Dict[str, str], keys: list) -> Optional[str]:
    for key in keys:
        value = tags.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = f"https://{url}"
    return url
