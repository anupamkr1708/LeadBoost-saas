"""
Duplicate Detector.

In-batch deduplication only (pure, no DB access) -- keyed on normalized
website domain first, falling back to name+phone when no website is
available. Cross-run duplicate checking against leads already stored for
the organization is handled separately by DiscoveryService, which reuses
the existing core.infrastructure.database.crud.get_lead_by_url exactly
the way the manual lead-creation endpoints already do.
"""

import re
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

from application.discovery.dto import DiscoveredBusiness


def normalize_domain(url: Optional[str]) -> Optional[str]:
    """'https://www.nike.com/shoes?x=1' -> 'nike.com'"""
    if not url:
        return None
    try:
        netloc = urlparse(url if "://" in url else f"//{url}").netloc.lower()
    except Exception:
        return None
    if not netloc:
        return None
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc or None


def _normalize_phone(phone: Optional[str]) -> Optional[str]:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits or None


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class DuplicateDetector:
    """Stateful across one discovery run: call `is_duplicate` in the order
    businesses should be kept-if-first-seen, then `mark_seen` for the ones
    you keep."""

    def __init__(self):
        self._seen_domains: Set[str] = set()
        self._seen_name_phone: Set[Tuple[str, str]] = set()

    def dedup_key(self, business: DiscoveredBusiness) -> Optional[str]:
        domain = normalize_domain(business.resolution.website)
        if domain:
            return f"domain:{domain}"
        phone = _normalize_phone(business.candidate.phone)
        if phone:
            return f"name_phone:{_normalize_name(business.candidate.name)}:{phone}"
        return None

    def is_duplicate(self, business: DiscoveredBusiness) -> bool:
        domain = normalize_domain(business.resolution.website)
        if domain and domain in self._seen_domains:
            return True

        phone = _normalize_phone(business.candidate.phone)
        if phone:
            key = (_normalize_name(business.candidate.name), phone)
            if key in self._seen_name_phone:
                return True

        return False

    def mark_seen(self, business: DiscoveredBusiness) -> None:
        domain = normalize_domain(business.resolution.website)
        if domain:
            self._seen_domains.add(domain)

        phone = _normalize_phone(business.candidate.phone)
        if phone:
            self._seen_name_phone.add((_normalize_name(business.candidate.name), phone))

    def dedup(self, businesses: List[DiscoveredBusiness]) -> List[DiscoveredBusiness]:
        """Convenience: mark `is_duplicate`/`duplicate_key` on every
        business in order, keeping the first occurrence of each key."""
        for business in businesses:
            if self.is_duplicate(business):
                business.is_duplicate = True
                business.duplicate_key = self.dedup_key(business)
            else:
                self.mark_seen(business)
        return businesses
