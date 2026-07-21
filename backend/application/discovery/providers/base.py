"""
Provider abstraction.

DiscoveryService depends on these interfaces, never on a concrete provider
class -- swapping or adding a search/resolution backend later means
implementing one of these, not touching DiscoveryService.

Two distinct roles, matching the spec exactly:
  BusinessSearchProvider    -> primary business discovery (Overpass)
  WebsiteResolverProvider   -> website resolution ONLY, used as a fallback
                                when the search provider has no website for
                                a business (Brave)
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from application.discovery.dto import BusinessCandidate


class BusinessSearchProvider(ABC):
    name: str = "base_search_provider"

    @abstractmethod
    async def search(self, category: str, location: str, limit: int) -> List[BusinessCandidate]:
        """Return up to `limit` businesses matching `category` in `location`.
        Must not raise for "no results" (return an empty list); may raise
        ProviderError for a genuine backend failure after its own retries."""


class WebsiteResolverProvider(ABC):
    name: str = "base_resolver_provider"

    @abstractmethod
    async def resolve_website(self, business_name: str, location: str) -> Optional[str]:
        """Return the single most likely official website URL for a named
        business, or None if no confident candidate was found. Must never
        fabricate a URL. Must not scrape the result -- only return the URL
        found in the search provider's own result metadata."""
