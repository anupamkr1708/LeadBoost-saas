"""
Brave Provider (fallback website resolution ONLY).

Used exclusively when a business from the primary search provider has no
website, an invalid website, or an unreachable website. Never used for
business discovery itself, and never scrapes the search results -- it
only reads the URL field already present in Brave's own result metadata
and hands that single URL back for validation.

If BRAVE_API_KEY is not configured, this provider is inert: it logs once
and returns None for every call, which the caller (website_resolver)
treats the same as "no candidate found" -- website resolution failure is
a documented "continue" case, not a hard error.

Note: as of this revision, DiscoveryService's *default* fallback provider
is SerperWebsiteResolver (see serper_provider.py), not this one -- Brave's
API now requires a card on file, whereas Serper offers a no-card free
tier. This class is left fully intact and still usable (constructor
-injectable into DiscoveryService) for anyone who does have a Brave key.
"""

import os
from typing import List, Optional

from application.discovery.providers import http_utils
from application.discovery.providers.base import WebsiteResolverProvider
from application.discovery.website_validator import domain_of, is_rejected_domain
from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.brave")


def _looks_official(url: str, business_name: str) -> bool:
    if is_rejected_domain(url):
        return False
    domain = domain_of(url)
    if not domain:
        return False
    # Cheap relevance check: at least one significant word of the business
    # name should appear in the domain (avoids picking an unrelated top
    # result for an ambiguous business name).
    name_words = [w.lower() for w in business_name.split() if len(w) > 2]
    domain_root = domain.split(".")[0]
    return any(word in domain_root or domain_root in word for word in name_words) if name_words else True


class BraveWebsiteResolver(WebsiteResolverProvider):
    name = "brave"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 10):
        self.api_key = api_key if api_key is not None else os.getenv("BRAVE_API_KEY")
        self.timeout = timeout
        self._warned_missing_key = False

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def resolve_website(self, business_name: str, location: str) -> Optional[str]:
        if not self.is_configured():
            if not self._warned_missing_key:
                logger.info(
                    "BRAVE_API_KEY not configured; website-resolution fallback is disabled",
                    extra={"event": "discovery_provider_unconfigured", "provider": self.name},
                )
                self._warned_missing_key = True
            return None

        query = f"{business_name} {location} official website"
        try:
            results = await self._search(query)
        except http_utils.ProviderHTTPError as e:
            logger.warning(
                f"Brave search returned HTTP {e.status}",
                extra={
                    "event": "discovery_provider_http_error",
                    "provider": self.name,
                    "status": e.status,
                },
            )
            return None
        except Exception as e:
            logger.warning(
                f"Brave search failed: {e}",
                extra={"event": "discovery_provider_error", "provider": self.name},
            )
            return None

        for result in results:
            url = result.get("url")
            if url and _looks_official(url, business_name):
                logger.info(
                    "Brave resolved a website candidate",
                    extra={
                        "event": "discovery_website_resolved",
                        "provider": self.name,
                        "business_name": business_name,
                        "resolved_url": url,
                    },
                )
                return url

        return None

    async def _search(self, query: str) -> List[dict]:
        url = "https://api.search.brave.com/res/v1/web/search"
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": self.api_key,
        }
        payload = await http_utils.get_json(
            url, headers=headers, params={"q": query, "count": 5}, timeout=self.timeout
        )
        return (payload.get("web") or {}).get("results") or []