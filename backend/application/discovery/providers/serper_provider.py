"""
Serper Provider (fallback website resolution ONLY).

Drop-in replacement for BraveWebsiteResolver as DiscoveryService's default
fallback WebsiteResolverProvider -- same interface, same role in the
pipeline (only ever called when Overpass has no website, or its website
failed validation), same "never fabricate, never scrape results" rules.
Brave requires a card on file to obtain a key; Serper offers a free tier
(2500 credits) with no card required, which is the only reason for the
switch.

Used exclusively when a business from the primary search provider has no
website, an invalid website, or an unreachable website.

If SERPER_API_KEY is not configured, this provider is inert: it logs once
and returns None for every call -- byte-for-byte the same
"fail gracefully, never crash Discovery" behavior BraveWebsiteResolver
already had.
"""

import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from application.discovery.providers import http_utils
from application.discovery.providers.base import WebsiteResolverProvider
from application.discovery.website_validator import domain_of, is_rejected_domain
from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.serper")

_SERPER_SEARCH_URL = "https://google.serper.dev/search"
_TOP_N_RESULTS = 5

# Serper-specific additions on top of website_validator.REJECTED_DOMAINS
# (reused, not duplicated -- see _is_acceptable_result below). These are
# rejected for *search-result scoring* purposes -- a Wikipedia page or a
# GitHub repo is a real, reachable page, just never "the business's
# official website."
_SERPER_EXTRA_REJECTED_DOMAINS = (
    "wikipedia.org",
    "github.com",
)

# Path/filename patterns that mark a result as an article/asset rather
# than a business's site itself (forums, blogs, news, PDFs). Substring
# checks on the lowercased path are enough here -- this is a coarse
# result-quality filter, not a security boundary.
_REJECTED_PATH_SUBSTRINGS = (
    "/blog/",
    "/news/",
    "/forum/",
    "/forums/",
    "/wiki/",
    "/press/",
    "/article/",
)
_REJECTED_EXTENSIONS = (".pdf", ".doc", ".docx")

_MIN_ACCEPTABLE_SCORE = 10.0


def _is_acceptable_result(url: str) -> bool:
    if is_rejected_domain(url):
        return False
    domain = domain_of(url)
    if not domain or any(rejected in domain for rejected in _SERPER_EXTRA_REJECTED_DOMAINS):
        return False

    parsed = urlparse(url)
    path_lower = parsed.path.lower()
    if any(pattern in path_lower for pattern in _REJECTED_PATH_SUBSTRINGS):
        return False
    if any(path_lower.endswith(ext) for ext in _REJECTED_EXTENSIONS):
        return False

    return True


def _score_result(result: Dict[str, Any], business_name: str, position: int) -> Optional[float]:
    """Deterministic scoring for one organic result. Returns None if the
    result should be rejected outright; otherwise a non-negative score
    where higher is a more likely official site."""
    url = result.get("link")
    if not url:
        return None
    if not _is_acceptable_result(url):
        return None

    parsed = urlparse(url)
    score = 0.0

    if parsed.scheme == "https":
        score += 10.0

    # Prefer the root domain over a subpage (e.g. apple.com over
    # apple.com/newsroom) when multiple results share the same domain.
    if parsed.path in ("", "/"):
        score += 15.0
    else:
        score += 5.0

    domain = domain_of(url)
    domain_root = domain.split(".")[0] if domain else ""
    name_words = [w.lower() for w in business_name.split() if len(w) > 2]
    if name_words and domain_root:
        if any(word == domain_root for word in name_words):
            score += 25.0  # exact brand match, e.g. "nike" == "nike.com"
        elif any(word in domain_root or domain_root in word for word in name_words):
            score += 12.0  # partial brand match

    title = (result.get("title") or "").lower()
    if business_name.lower() in title:
        score += 8.0

    # Small, capped boost for ranking higher in Serper's own results --
    # a tiebreaker, not the primary signal.
    score += max(0.0, 5.0 - position)

    return score


class SerperWebsiteResolver(WebsiteResolverProvider):
    name = "serper"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 10):
        self.api_key = api_key if api_key is not None else os.getenv("SERPER_API_KEY")
        self.timeout = timeout
        self._warned_missing_key = False

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def resolve_website(self, business_name: str, location: str) -> Optional[str]:
        if not self.is_configured():
            if not self._warned_missing_key:
                logger.info(
                    "SERPER_API_KEY not configured; website-resolution fallback is disabled",
                    extra={"event": "discovery_provider_unconfigured", "provider": self.name},
                )
                self._warned_missing_key = True
            return None

        query = (
            f"{business_name} {location} official website"
            if location
            else f"{business_name} official website"
        )

        start = time.time()
        try:
            organic_results = await self._search(query)
        except http_utils.ProviderHTTPError as e:
            self._log_http_error(e.status, query)
            return None
        except Exception as e:
            logger.warning(
                f"Serper search failed: {e}",
                extra={"event": "discovery_provider_error", "provider": self.name, "query": query},
            )
            return None
        response_time_ms = int((time.time() - start) * 1000)

        top_results = organic_results[:_TOP_N_RESULTS]
        logger.info(
            "Serper search completed",
            extra={
                "event": "discovery_provider_search",
                "provider": self.name,
                "query": query,
                "response_time_ms": response_time_ms,
                "results_returned": len(organic_results),
                "results_evaluated": len(top_results),
            },
        )

        best_url, best_score = None, 0.0
        for position, result in enumerate(top_results):
            score = _score_result(result, business_name, position)
            if score is not None and score > best_score and score >= _MIN_ACCEPTABLE_SCORE:
                best_score, best_url = score, result.get("link")

        if best_url is None:
            logger.info(
                "Serper found no acceptable website candidate among top results",
                extra={
                    "event": "discovery_website_resolution_failed",
                    "provider": self.name,
                    "business_name": business_name,
                },
            )
            return None

        logger.info(
            "Serper resolved a website candidate",
            extra={
                "event": "discovery_website_resolved",
                "provider": self.name,
                "business_name": business_name,
                "resolved_url": best_url,
                "chosen_domain": domain_of(best_url),
                "score": best_score,
            },
        )
        return best_url

    def _log_http_error(self, status: int, query: str) -> None:
        if status == 401:
            reason = "invalid or missing API key"
        elif status == 403:
            reason = "forbidden"
        elif status == 429:
            reason = "rate limited"
        elif status >= 500:
            reason = "Serper server error"
        else:
            reason = f"HTTP {status}"
        logger.warning(
            f"Serper search failed: {reason}",
            extra={
                "event": "discovery_provider_http_error",
                "provider": self.name,
                "status": status,
                "query": query,
            },
        )

    async def _search(self, query: str) -> List[Dict[str, Any]]:
        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = await http_utils.post_json(
            _SERPER_SEARCH_URL, headers=headers, json_body={"q": query}, timeout=self.timeout
        )
        return payload.get("organic") or []