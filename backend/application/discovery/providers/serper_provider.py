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

Grounding
---------
Result scoring is built on application.discovery.grounding, not naive
substring matching. Naive matching (checking whether any word of the
business name appears anywhere inside the domain, or vice versa) is what
previously let a Mumbai shoe store named "Regal" resolve to
regmovies.com (an unrelated US cinema chain -- "regal" doesn't even
appear as a substring of "regmovies", it was the generic "https + root
path" quality score alone that cleared the old, too-low acceptance bar)
and let "Hollywood Walk of Shame" resolve to walkoffame.com. Two changes
fix this:

  1. Brand relevance is now a *gate*, not just an additive bonus: a result
     is only scored at all if grounding.brand_match_strength finds a real
     brand/domain relationship, or the business name literally appears in
     the result title. Generic page-quality signals (https, root domain)
     are no longer, by themselves, enough to pass.
  2. A generic/low-signal business name (grounding.is_low_signal_business_name
     -- e.g. a name pattern like "<Person> Hospital" that repeats near-
     identically across many cities) additionally requires the location to
     be corroborated somewhere in the result (title/snippet/URL) before
     being trusted -- brand match alone can't tell "this city's branch"
     apart from a same-named business elsewhere.
"""

import os
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from application.discovery import grounding
from application.discovery.providers import http_utils
from application.discovery.providers.base import BusinessSearchProvider, WebsiteResolverProvider
from application.discovery.dto import BusinessCandidate
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

# Aggregator/listicle/review sites that legitimately rank a real page for
# almost any business+location query, but are never *the business's own*
# site -- relevant only to SerperBusinessSearchProvider's category-level
# search (below), where there's no specific business name yet to score
# against, so _is_acceptable_result's generic checks wouldn't catch them.
_AGGREGATOR_DOMAINS = (
    "clutch.co",
    "g2.com",
    "capterra.com",
    "builtin.com",
    "crunchbase.com",
    "glassdoor.com",
    "goodfirms.co",
    "designrush.com",
    "themanifest.com",
    "indeed.com",
    "angel.co",
    "wellfound.com",
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

_MIN_ACCEPTABLE_SCORE = 15.0

# Minimum brand_match_strength to treat a result as "the business's own
# domain" without needing the name to also appear in the title verbatim.
_BRAND_GATE_THRESHOLD = 0.55
# Above this, a low-signal name is trusted without corroborating location
# text -- an (almost) exact brand/domain match is strong enough evidence
# on its own even for a generic name.
_LOW_SIGNAL_BRAND_OVERRIDE = 0.95

# Title patterns that mark a listicle/directory-style result ("Top 10 X in
# Y", "Best AI startups in Noida") rather than one specific company's own
# page -- only relevant to the category-level business search below.
_LISTICLE_TITLE_PATTERN = re.compile(
    r"\btop\s+\d+\b|\bbest\s+\d+\b|\b\d+\s+best\b", re.IGNORECASE
)


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


def _score_result(
    result: Dict[str, Any], business_name: str, position: int, location: str = ""
) -> Optional[float]:
    """Deterministic scoring for one organic result. Returns None if the
    result should be rejected outright; otherwise a non-negative score
    where higher is a more likely official site.

    Two grounding gates run *before* any score is computed -- generic
    page-quality signals (https, root domain) are never, by themselves,
    enough to accept a result:

      1. There must be real evidence connecting this domain/page to the
         actual business: either grounding.brand_match_strength finds a
         genuine brand/domain relationship, or the business name appears
         verbatim in the result title. Without either, the result is
         rejected outright regardless of how "clean" the URL looks.
      2. If the business name is low-signal/generic (a pattern that
         repeats near-identically across many cities, e.g. "<Person>
         Hospital"), the location must additionally be corroborated
         somewhere in the result -- unless the brand match is strong
         enough to stand on its own.
    """
    url = result.get("link")
    if not url:
        return None
    if not _is_acceptable_result(url):
        return None

    domain = domain_of(url)
    title = result.get("title") or ""
    snippet = result.get("snippet") or ""

    brand_strength = grounding.brand_match_strength(business_name, domain)
    title_has_name = bool(business_name) and business_name.lower() in title.lower()

    if brand_strength < _BRAND_GATE_THRESHOLD and not title_has_name:
        return None

    if grounding.is_low_signal_business_name(business_name) and brand_strength < _LOW_SIGNAL_BRAND_OVERRIDE:
        location_evidence = (
            grounding.location_mentioned(title, location)
            or grounding.location_mentioned(snippet, location)
            or grounding.location_mentioned(url, location)
        )
        if not location_evidence:
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

    # Continuous brand-match contribution replaces the old binary
    # exact/partial (25/12) bump -- a real fuzzy match (e.g. "Metro
    # Shoes" -> metroshoes.com) is rewarded proportionally rather than as
    # an all-or-nothing substring check.
    score += brand_strength * 25.0

    if title_has_name:
        score += 8.0

    if grounding.location_mentioned(title, location) or grounding.location_mentioned(snippet, location):
        score += 10.0  # corroborating location signal, when available

    # Small, capped boost for ranking higher in Serper's own results --
    # a tiebreaker, not the primary signal.
    score += max(0.0, 5.0 - position)

    return score


class SerperWebsiteResolver(WebsiteResolverProvider):
    name = "serper"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 10,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("SERPER_API_KEY")
        self.timeout = timeout
        self._warned_missing_key = False
        # H1: connection reuse -- see DiscoveryService._ensure_shared_session.
        self.session = session

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
            score = _score_result(result, business_name, position, location)
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
            _SERPER_SEARCH_URL, headers=headers, json_body={"q": query}, timeout=self.timeout,
            session=self.session,
        )
        return payload.get("organic") or []


def _looks_like_listicle(title: str) -> bool:
    """True for directory/roundup-style titles ("Top 10 AI Startups in
    Noida", "15 Best SaaS Companies") -- these are a real, reachable page,
    just an aggregator's article about many companies, not one company's
    own site."""
    return bool(_LISTICLE_TITLE_PATTERN.search(title or ""))


def _derive_business_name(title: str, domain: str) -> str:
    """Best-effort business name for a candidate whose only source is a
    web-search result, not a structured business listing. Prefers the
    lead segment of the page title (before a separator like '|' or '-',
    the common "Brand | Tagline" pattern); falls back to a titleized
    domain root so a name is never left empty."""
    if title:
        primary = re.split(r"\s+[|\-\u2013\u2014:]\s+", title, maxsplit=1)[0].strip()
        if primary and 1 < len(primary) <= 60:
            return primary
    root = grounding.domain_root(domain)
    return root.replace("-", " ").replace("_", " ").title() if root else domain


class SerperBusinessSearchProvider(BusinessSearchProvider):
    """Fallback business-discovery source, used ONLY when Overpass (OSM)
    returns zero results for a category that typically has no physical,
    map-able presence -- SaaS, startups, software/IT/AI companies, and
    similar (see DiscoveryService._is_startup_like_category, the single
    place that decides when this fires). Overpass remains the primary
    source and is always tried first; this never replaces it.

    Unlike SerperWebsiteResolver (which resolves *one already-known*
    business's website), this searches the category+location query itself
    and treats each acceptable organic result as a distinct company's own
    site -- there is no per-business name to score against yet, so
    filtering here leans on domain/path rejection (directories,
    aggregators, listicle titles) rather than grounding.brand_match_strength.
    Each resulting BusinessCandidate carries its website directly (the
    result *is* the candidate's site), so it flows through the existing,
    unmodified resolve/validate/dedupe/rank pipeline exactly like an
    Overpass candidate with a `website` tag already set -- no special-
    casing needed downstream.
    """

    name = "serper_business_search"

    def __init__(
        self,
        api_key: Optional[str] = None,
        timeout: int = 10,
        session: Optional[aiohttp.ClientSession] = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("SERPER_API_KEY")
        self.timeout = timeout
        self._warned_missing_key = False
        # H1: connection reuse -- see DiscoveryService._ensure_shared_session.
        self.session = session

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, category: str, location: str, limit: int) -> List[BusinessCandidate]:
        if not self.is_configured():
            if not self._warned_missing_key:
                logger.info(
                    "SERPER_API_KEY not configured; startup-category search fallback is disabled",
                    extra={"event": "discovery_provider_unconfigured", "provider": self.name},
                )
                self._warned_missing_key = True
            return []

        query = f"{category} in {location}" if location else category
        try:
            organic_results = await http_utils.post_json(
                _SERPER_SEARCH_URL,
                headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
                json_body={"q": query, "num": max(limit * 2, 10)},
                timeout=self.timeout,
                session=self.session,
            )
        except Exception as e:
            logger.warning(
                f"Serper business search failed: {e}",
                extra={"event": "discovery_provider_error", "provider": self.name, "query": query},
            )
            return []

        organic = organic_results.get("organic") or []
        candidates: List[BusinessCandidate] = []
        seen_domains = set()

        for result in organic:
            url = result.get("link")
            title = result.get("title") or ""
            if not url or not _is_acceptable_result(url):
                continue
            if _looks_like_listicle(title):
                continue
            domain = domain_of(url)
            if not domain or any(agg in domain for agg in _AGGREGATOR_DOMAINS):
                continue
            if domain in seen_domains:
                continue
            seen_domains.add(domain)

            candidates.append(
                BusinessCandidate(
                    name=_derive_business_name(title, domain),
                    category=category,
                    address=None,
                    phone=None,
                    website=url,
                    rating=None,
                    review_count=None,
                    source=self.name,
                )
            )
            if len(candidates) >= limit:
                break

        logger.info(
            "Serper business-search fallback completed",
            extra={
                "event": "discovery_provider_search",
                "provider": self.name,
                "category": category,
                "location": location,
                "results_returned": len(organic),
                "candidates_built": len(candidates),
            },
        )
        return candidates