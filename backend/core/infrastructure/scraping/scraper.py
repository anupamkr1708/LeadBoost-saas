"""
Hybrid, multi-tier web scraping infrastructure.

Design goals
------------
This module extracts high-quality company/contact intelligence from modern
company websites (static, JS-rendered, and anti-bot-protected) while staying
resource-efficient. It escalates through progressively heavier tiers, only
paying the cost of a later tier when an earlier one fails, is blocked, or
returns low-confidence data:

  Tier 1/2  static fetch (aiohttp)   -> deep schema.org/JSON-LD parsing +
                                         OpenGraph/Twitter/meta tags from a
                                         single fetched page
  Tier 3    curl_cffi impersonation  -> TLS/JA3 browser-fingerprint spoofing,
                                         defeats fingerprint-based bot filters
                                         without paying for a full browser
  Tier 4    Playwright (headless)    -> full JS rendering for SPAs, shared
                                         browser pool, tracker/asset blocking
  Tier 5    multi-page enrichment    -> intelligently scored, budget-bounded,
                                         concurrently fetched About/Contact/
                                         Team/Careers/... pages, merged with
                                         sitemap-discovered URLs
  Tier 6    requests fallback        -> last-resort synchronous fetch,
                                         offloaded to a thread so it never
                                         blocks the event loop


Everything else in this module (anything prefixed with `_`) is a private
implementation detail and is free to evolve.

Output-compatibility note
--------------------------
`ScrapingResult.data` is a loosely-typed `Dict[str, Any]`. This revision adds
many new, additively-merged keys (e.g. `sales_email`, `city`, `technologies`,
`company_name`) without ever removing or renaming a key the previous
implementation produced. Every field the previous implementation guaranteed
(`name`, `title`, `description`, `email`, `phone`, `linkedin_url`,
`twitter_url`, `facebook_url`, `links`, `text_content`, `jsonld_raw`,
`jsonld`, `potential_company_name`, ...) is still populated the same way, by
the same underlying helper (`_extract_contact_info` is untouched, verbatim).
New keys are only ever added, never substituted for old ones, so any caller
reading a known key by name keeps working unmodified.
"""

import asyncio
import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import aiohttp
from bs4 import BeautifulSoup

from core.infrastructure.logging import get_logger

logger = get_logger(__name__)


class ScrapingMethod(str, Enum):
    JSON_LD = "json_ld"
    STRUCTURED_DATA = "structured_data"
    CURL_CFFI = "curl_cffi"
    PLAYWRIGHT = "playwright"
    MULTI_PAGE = "multi_page"
    REQUESTS = "requests"
    BEAUTIFULSOUP = "beautifulsoup"


@dataclass
class ScrapingResult:
    success: bool
    data: Dict[str, Any]
    method: ScrapingMethod
    confidence: float
    processing_time: float
    error_message: Optional[str] = None
    # Additive fields (default values keep this backward compatible with
    # any caller constructed against the previous dataclass shape).
    pages_scraped: int = 1
    blocked_detected: bool = False
    tiers_attempted: List[str] = field(default_factory=list)


@dataclass
class _TierOutcome:
    """Internal, per-tier bundle used only while the pipeline escalates
    between tiers inside `scrape()`. Never exposed to callers -- the public
    return type of `scrape()` is always `ScrapingResult`."""

    result: ScrapingResult
    blocked: bool
    anchors: List[Tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Anti-bot resilience helpers: header rotation & block detection
# ---------------------------------------------------------------------------

_USER_AGENT_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

_ACCEPT_LANGUAGES = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.9,es;q=0.5",
]

_VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
]

_TIMEZONES = ["America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London"]


def _random_headers(referer: Optional[str] = None) -> Dict[str, str]:
    """Build a realistic, rotating browser-like header set."""
    ua = random.choice(_USER_AGENT_POOL)
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none" if not referer else "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }
    if "Chrome" in ua or "Edg" in ua:
        headers["sec-ch-ua"] = (
            '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
        )
        headers["sec-ch-ua-mobile"] = "?0"
        headers["sec-ch-ua-platform"] = '"Windows"' if "Windows" in ua else '"macOS"'
    if referer:
        headers["Referer"] = referer
    return headers


_BLOCK_MARKERS = [
    "checking your browser",
    "just a moment",
    "attention required",
    "cloudflare",
    "captcha",
    "access denied",
    "request blocked",
    "enable javascript and cookies",
    "unusual traffic",
    "are you a robot",
    "verify you are a human",
    "ddos protection by",
    "perimeterx",
    "bot detection",
    "please wait while we verify",
    "sorry, you have been blocked",
    "akamai",
    "incapsula",
    "sucuri website firewall",
    "imunify360",
    "reference #",
    "edgesuite",
    "distil networks",
    "datadome",
    "kasada",
    "one more step",
    "checking if the site connection is secure",
    "ray id",
]


def _looks_blocked(status: int, html: str) -> bool:
    """Heuristically detect anti-bot challenge/interstitial pages."""
    if status in (403, 406, 429, 503):
        return True
    if not html:
        return False
    lowered = html[:6000].lower()
    hits = sum(1 for marker in _BLOCK_MARKERS if marker in lowered)
    if hits >= 1:
        return True
    if len(html.strip()) < 400 and "<script" in lowered and "challenge" in lowered:
        return True
    return False


_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
try {
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
      ? Promise.resolve({ state: Notification.permission })
      : originalQuery(parameters)
  );
} catch (e) {}
"""

_EXTRACTION_JS = """
() => {
    const result = {};

    result.title = document.title || null;
    result.url = window.location.href || null;

    const metaDesc = document.querySelector("meta[name='description']");
    result.meta_description = metaDesc ? metaDesc.content : null;

    const ogDesc = document.querySelector("meta[property='og:description']");
    result.og_description = ogDesc ? ogDesc.content : null;

    const ogTitle = document.querySelector("meta[property='og:title']");
    result.og_title = ogTitle ? ogTitle.content : null;

    result.jsonld = Array.from(
        document.querySelectorAll("script[type='application/ld+json']")
    ).map(s => {
        try { return JSON.parse(s.innerText); }
        catch (e) { return null; }
    }).filter(Boolean);

    result.text_content = (document.body ? document.body.innerText : "").slice(0, 10000);

    result.links = Array.from(document.querySelectorAll("a[href]"))
        .map(a => a.href)
        .filter(href => href && href.startsWith('http'));

    const mailtoLinks = Array.from(document.querySelectorAll("a[href^='mailto:']"))
        .map(a => a.getAttribute('href').replace('mailto:', '').split('?')[0]);
    if (mailtoLinks.length) result.mailto_email = mailtoLinks[0];

    const telLinks = Array.from(document.querySelectorAll("a[href^='tel:']"))
        .map(a => a.getAttribute('href').replace('tel:', ''));
    if (telLinks.length) result.tel_phone = telLinks[0];

    const domain = window.location.hostname.replace('www.', '');
    result.potential_company_name = domain.split('.')[0];

    return result;
}
"""


# ---------------------------------------------------------------------------
# Multi-page discovery: link scoring, exclusions, sitemap support
# ---------------------------------------------------------------------------

# category -> (weight, keyword list). Weight reflects how valuable that page
# type typically is for company/contact intelligence.
_PAGE_CATEGORIES: Dict[str, Dict[str, Any]] = {
    "about": {"weight": 1.0, "keywords": ["about-us", "aboutus", "about", "our-story", "ourstory", "who-we-are", "whoweare", "company"]},
    "contact": {"weight": 1.0, "keywords": ["contact-us", "contactus", "contact", "get-in-touch", "reach-us"]},
    "team": {"weight": 0.85, "keywords": ["leadership", "our-team", "team", "our-people", "people", "management", "founders"]},
    "careers": {"weight": 0.45, "keywords": ["careers", "career", "jobs", "join-us", "joinus", "work-with-us", "hiring"]},
    "pricing": {"weight": 0.55, "keywords": ["pricing", "plans", "price"]},
    "products": {"weight": 0.55, "keywords": ["products", "product", "solutions", "platform", "features"]},
    "customers": {"weight": 0.45, "keywords": ["customers", "case-studies", "casestudies", "success-stories", "clients"]},
    "partners": {"weight": 0.35, "keywords": ["partners", "partnership", "partner-program"]},
    "security": {"weight": 0.4, "keywords": ["security", "trust", "compliance", "privacy"]},
    "developers": {"weight": 0.4, "keywords": ["api", "developers", "developer", "docs", "documentation"]},
    "press": {"weight": 0.45, "keywords": ["press", "media", "news", "newsroom"]},
    "blog": {"weight": 0.25, "keywords": ["blog", "insights", "resources"]},
    "investor": {"weight": 0.35, "keywords": ["investor", "investors", "/ir", "ir/"]},
    "locations": {"weight": 0.45, "keywords": ["locations", "offices", "office"]},
    "legal": {"weight": 0.15, "keywords": ["terms", "legal", "tos"]},
    "support": {"weight": 0.3, "keywords": ["support", "help", "helpdesk", "faq"]},
}

_EXCLUDE_URL_SUBSTRINGS = [
    "/logout", "/login", "/signin", "sign-in", "/signup", "sign-up",
    "/cart", "/checkout", "/search?", "/search/", "/admin", "wp-admin",
    "cdn-cgi", "/assets/", "/static/", "/media/", "/video/", "/videos/",
    ".pdf", ".zip", ".rar", ".7z", ".dmg", ".exe", ".jpg", ".jpeg", ".png",
    ".gif", ".svg", ".webp", ".mp4", ".mp3", ".mov", ".avi", ".woff",
    ".woff2", ".ttf", ".css", ".js", "javascript:", "mailto:", "tel:",
    "/wp-json/", "/feed/", "/rss",
]

_SOCIAL_DOMAIN_MAP: Dict[str, Tuple[str, ...]] = {
    "linkedin_url": ("linkedin.com",),
    "twitter_url": ("twitter.com", "x.com"),
    "facebook_url": ("facebook.com", "fb.com"),
    "instagram_url": ("instagram.com",),
    "youtube_url": ("youtube.com", "youtu.be"),
    "github_url": ("github.com",),
    "crunchbase_url": ("crunchbase.com",),
    "glassdoor_url": ("glassdoor.com",),
}

_EMAIL_PREFIX_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "sales_email": ("sales", "biz", "business", "partnerships"),
    "support_email": ("support", "help", "helpdesk", "care"),
    "press_email": ("press", "media", "pr", "publicity"),
    "privacy_email": ("privacy", "dpo", "gdpr"),
    "careers_email": ("careers", "jobs", "hr", "recruiting", "talent"),
    "contact_email": ("contact", "hello", "info", "enquiries", "inquiries", "team"),
}

_EXCLUDED_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "yourdomain.com",
    "email.com", "domain.com", "test.com", "sentry.io", "yourcompany.com",
    "company.com", "wixpress.com", "godaddy.com", "schema.org", "w3.org",
    "godaddysites.com", "sentry-next.wixpress.com",
}

_TRACKER_URL_PATTERNS = [
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "hotjar.com", "segment.io", "segment.com", "fullstory.com",
    "intercom.io", "intercomcdn.com", "drift.com", "driftt.com",
    "connect.facebook.net", "clarity.ms", "mixpanel.com", "amplitude.com",
    "hs-analytics.net", "hsforms.net", "snap.licdn.com", "px.ads.linkedin.com",
    "criteo.com", "adroll.com", "taboola.com", "outbrain.com",
]

_TECH_SIGNATURES: Dict[str, Tuple[str, ...]] = {
    "Shopify": ("cdn.shopify.com", "shopify.com/s/"),
    "WordPress": ("wp-content", "wp-includes"),
    "Webflow": ("webflow.com", "website-files.com"),
    "Squarespace": ("squarespace.com", "static1.squarespace.com"),
    "Wix": ("wix.com", "wixstatic.com"),
    "HubSpot": ("hs-scripts.com", "hsforms.net", "hubspot.com"),
    "Salesforce": ("force.com",),
    "Next.js": ("__next", "_next/static"),
    "React": ("react-dom", "data-reactroot"),
    "Google Tag Manager": ("googletagmanager.com",),
    "Cloudflare": ("cdnjs.cloudflare.com",),
    "Intercom": ("intercom.io",),
    "Zendesk": ("zdassets.com",),
}

_ORG_SCHEMA_TYPES = {
    "organization", "corporation", "localbusiness", "ngo",
    "educationalorganization", "onlinebusiness", "professionalservice",
}
_PRODUCT_SCHEMA_TYPES = {"product", "softwareapplication", "webapplication", "mobileapplication"}

# Phrases that mark a string as a marketing headline rather than a company
# name (e.g. "AI Masterclass 2026: On-demand workshops..."). Used both to
# reject bad company_name candidates and to judge whether an enrichment
# subpage's description reads as campaign copy vs. real company context.
_PROMO_PATTERNS = re.compile(
    r"\b(free trial|sign up|sign in|get started|log ?in|learn more|% off|"
    r"save \d|book (a |your )?demo|start(?:ing)? (a |your )?(free )?trial|"
    r"on[- ]demand|webinar|masterclass|workshop\b|guide to|how to|"
    r"best practices|ultimate guide|checklist|e-?book|case stud|"
    r"now available|coming soon|subscribe|new feature)\b",
    re.IGNORECASE,
)


class _RobotsCache:
    """Soft, cached robots.txt checker.

    Disabled by default (RESPECT_ROBOTS_TXT=false) so the platform can
    fulfil user-initiated lead lookups reliably; deployments that need
    strict robots.txt compliance can opt in via the environment variable.
    Fails open (allows) on any fetch error so it can never itself cause
    a scrape to fail.
    """

    def __init__(self):
        self._cache: Dict[str, bool] = {}

    async def is_allowed(self, session: aiohttp.ClientSession, url: str) -> bool:
        if os.getenv("RESPECT_ROBOTS_TXT", "false").lower() != "true":
            return True

        try:
            parsed = urlparse(url)
            root = f"{parsed.scheme}://{parsed.netloc}"
            if root in self._cache:
                return self._cache[root]

            import urllib.robotparser as robotparser

            robots_url = urljoin(root, "/robots.txt")
            async with session.get(
                robots_url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status != 200:
                    self._cache[root] = True
                    return True
                text = await resp.text(errors="ignore")

            rp = robotparser.RobotFileParser()
            rp.parse(text.splitlines())
            allowed = rp.can_fetch("*", url)
            self._cache[root] = allowed
            return allowed
        except Exception:
            return True


# ---------------------------------------------------------------------------
# Shared Playwright browser pool
# ---------------------------------------------------------------------------


class _BrowserPool:
    """Lazily-initialized, shared Playwright Chromium instance.

    Launching a fresh browser process per scrape is slow, resource-hungry,
    and produces an easily fingerprinted cold-start pattern. A single shared
    browser with a per-request isolated context is the standard production
    pattern for headless-browser scraping fleets, and lets us bound
    concurrency with a semaphore.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._browser_lock = asyncio.Lock()
        max_pages = max(1, int(os.getenv("SCRAPER_MAX_CONCURRENT_PAGES", "4")))
        self._semaphore = asyncio.Semaphore(max_pages)

    async def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return
        async with self._browser_lock:
            if self._browser is not None and self._browser.is_connected():
                return
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars",
                    "--disable-extensions",
                    "--disable-gpu",
                ],
            )
            logger.info("Playwright browser pool initialized")

    async def new_page(self):
        await self._ensure_browser()
        await self._semaphore.acquire()
        try:
            ua = random.choice(_USER_AGENT_POOL)
            context = await self._browser.new_context(
                user_agent=ua,
                viewport=random.choice(_VIEWPORTS),
                locale="en-US",
                timezone_id=random.choice(_TIMEZONES),
                extra_http_headers={
                    "Accept-Language": random.choice(_ACCEPT_LANGUAGES),
                },
            )
            await context.add_init_script(_STEALTH_INIT_SCRIPT)
            page = await context.new_page()
            page._lb_context = context  # convenience handle for cleanup
            return page
        except Exception:
            self._semaphore.release()
            raise

    async def release_page(self, page) -> None:
        try:
            ctx = getattr(page, "_lb_context", None)
            await page.close()
            if ctx is not None:
                await ctx.close()
        except Exception as e:
            logger.debug(f"Error releasing Playwright page: {e}")
        finally:
            self._semaphore.release()

    async def close(self) -> None:
        try:
            if self._browser:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception as e:
            logger.debug(f"Error closing Playwright browser pool: {e}")
        finally:
            self._browser = None
            self._playwright = None


_pool_singleton: Optional[_BrowserPool] = None
_pool_singleton_lock = asyncio.Lock()


async def get_browser_pool() -> _BrowserPool:
    global _pool_singleton
    async with _pool_singleton_lock:
        if _pool_singleton is None:
            _pool_singleton = _BrowserPool()
        return _pool_singleton


async def close_scraper_resources() -> None:
    """Gracefully close shared scraping resources.

    Additive, optional hook intended to be called once from the
    application's shutdown lifecycle (see main.py). Safe to call even if
    no browser was ever launched.

    Closes both the shared Playwright browser pool AND the aiohttp
    ClientSession held by the get_scraper() singleton (previously only the
    browser pool was closed here, which is why "Unclosed client session"
    warnings showed up even in test runs that never touched Playwright).
    """
    global _pool_singleton, scraper_instance
    if _pool_singleton is not None:
        await _pool_singleton.close()
        _pool_singleton = None
    if scraper_instance is not None and scraper_instance.session is not None:
        try:
            await scraper_instance.session.close()
        except Exception as e:
            logger.debug(f"Error closing shared scraper session: {e}")
        finally:
            scraper_instance.session = None


class TieredScraper:
    """
    Hybrid tiered scraper. Escalates through static -> TLS-impersonation ->
    headless-browser -> multi-page -> synchronous-fallback tiers, stopping
    as soon as a tier returns sufficiently confident data.
    """

    def __init__(self, timeout: int = 25, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session: Optional[aiohttp.ClientSession] = None
        self._owns_session = False
        self._robots = _RobotsCache()
        self._sitemap_cache: Dict[str, List[str]] = {}

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ttl_dns_cache=300, limit=20),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
            self._owns_session = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session and self._owns_session:
            try:
                await self.session.close()
            except Exception as e:
                logger.debug(f"Error closing aiohttp session: {e}")
            finally:
                self.session = None

    # -- Public API ---------------------------------------------------------

    async def scrape(self, url: str) -> ScrapingResult:
        """Main entry point implementing the tiered, hybrid approach."""
        start_time = time.time()
        url = self._normalize_url(url)
        tiers_attempted: List[str] = []

        if self.session is None:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ttl_dns_cache=300, limit=20),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
            self._owns_session = True

        if not await self._robots.is_allowed(self.session, url):
            return ScrapingResult(
                success=False,
                data={},
                method=ScrapingMethod.STRUCTURED_DATA,
                confidence=0.0,
                processing_time=time.time() - start_time,
                error_message="Disallowed by robots.txt",
            )

        # Tier 1/2: single static fetch, deep schema-aware extraction
        best = await self._fetch_and_extract_static(url)
        tiers_attempted.append(best.result.method.value)

        if best.result.success and best.result.confidence > 0.7 and not best.blocked:
            return self._finish(best.result, start_time, tiers_attempted, best.blocked)

        # Tier 3: curl_cffi TLS-impersonation fetch, when blocked or weak
        if best.blocked or best.result.confidence < 0.5:
            curl_outcome = await self._scrape_with_curl_cffi(url)
            tiers_attempted.append(ScrapingMethod.CURL_CFFI.value)
            if curl_outcome.result.confidence > best.result.confidence:
                best = curl_outcome
            if best.result.success and best.result.confidence > 0.65 and not best.blocked:
                return self._finish(best.result, start_time, tiers_attempted, best.blocked)

        # Tier 4: Playwright headless rendering for JS-heavy sites
        if best.result.confidence < 0.65:
            pw_outcome = await self._scrape_with_playwright(url)
            tiers_attempted.append(ScrapingMethod.PLAYWRIGHT.value)
            if pw_outcome.result.confidence > best.result.confidence:
                best = pw_outcome

        # Tier 5: intelligent, budget-bounded, concurrent multi-page enrichment
        if (
            best.result.success
            and best.result.confidence < 0.9
            and not best.blocked
            and self._needs_enrichment(best.result.data)
        ):
            enriched = await self._enrich_from_subpages(url, best)
            if enriched is not None:
                best = enriched
                tiers_attempted.append(ScrapingMethod.MULTI_PAGE.value)

        # Tier 6: last-resort synchronous fallback
        if not best.result.success or best.result.confidence < 0.2:
            fallback = await self._scrape_with_requests_fallback(url)
            tiers_attempted.append(ScrapingMethod.REQUESTS.value)
            if fallback.result.confidence > best.result.confidence:
                best = fallback

        return self._finish(best.result, start_time, tiers_attempted, best.blocked)

    # -- Tier 1/2: static fetch ---------------------------------------------

    async def _fetch_and_extract_static(self, url: str) -> _TierOutcome:
        html, status, fetch_error = await self._http_get_with_retries(url)

        if html is None:
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.STRUCTURED_DATA,
                    confidence=0.0,
                    processing_time=0.0,
                    error_message=fetch_error,
                ),
                blocked=False,
            )

        blocked = _looks_blocked(status or 200, html)
        soup = BeautifulSoup(html, "html.parser")
        data = self._parse_page(soup, html, url, include_links=True)
        anchors = self._collect_anchor_pairs(soup, url)

        json_ld_conf = self._calculate_json_ld_confidence(data.get("jsonld_raw") or {})
        meta_conf = self._calculate_meta_confidence(data)
        confidence = max(json_ld_conf, meta_conf)
        confidence = self._apply_contact_bonus(confidence, data)
        confidence = self._cap_confidence_if_blocked(confidence, blocked)

        method = (
            ScrapingMethod.JSON_LD
            if data.get("jsonld_raw") and json_ld_conf >= meta_conf
            else ScrapingMethod.STRUCTURED_DATA
        )
        success = bool(data) and confidence > 0.0 and not blocked

        result = ScrapingResult(
            success=success,
            data=data,
            method=method,
            confidence=confidence,
            processing_time=0.0,
            error_message="Anti-bot challenge detected" if blocked else fetch_error,
            blocked_detected=blocked,
        )
        return _TierOutcome(result=result, blocked=blocked, anchors=anchors)

    async def _http_get_with_retries(
        self, url: str
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """GET with rotating headers, exponential backoff, and jitter."""
        if self.session is None:
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ttl_dns_cache=300, limit=20),
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            )
            self._owns_session = True

        last_error: Optional[str] = None
        last_status: Optional[int] = None

        for attempt in range(self.max_retries + 1):
            try:
                headers = _random_headers()
                async with self.session.get(
                    url, headers=headers, allow_redirects=True
                ) as response:
                    last_status = response.status

                    if response.status == 200:
                        text = await response.text(errors="ignore")
                        return text, response.status, None

                    if response.status in (429, 503) and attempt < self.max_retries:
                        delay = self._backoff_delay(
                            attempt, response.headers.get("Retry-After")
                        )
                        logger.warning(
                            f"Retryable status {response.status} for {url}, "
                            f"waiting {delay:.1f}s (attempt {attempt + 1})"
                        )
                        await asyncio.sleep(delay)
                        continue

                    # Capture body even on non-200: some anti-bot responses
                    # return a real (challenge) page with a 403/503 status,
                    # and 404-style pages sometimes still carry usable meta.
                    text = await response.text(errors="ignore")
                    return text, response.status, f"HTTP {response.status}"

            except asyncio.TimeoutError:
                last_error = "Request timed out"
            except aiohttp.ClientError as e:
                last_error = f"Client error: {str(e)}"
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                await asyncio.sleep(self._backoff_delay(attempt))

        return None, last_status, last_error or "Failed after retries"

    @staticmethod
    def _backoff_delay(attempt: int, retry_after: Optional[str] = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 10.0)
            except ValueError:
                pass
        base = min(1.5 * (2**attempt), 8.0)
        return base + random.uniform(0, 0.5)

    # -- Tier 3: curl_cffi TLS impersonation ---------------------------------

    async def _scrape_with_curl_cffi(self, url: str) -> _TierOutcome:
        start = time.time()
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.CURL_CFFI,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message="curl_cffi not installed",
                ),
                blocked=False,
            )

        try:
            async with AsyncSession() as session:
                resp = await session.get(
                    url,
                    impersonate="chrome",
                    timeout=self.timeout,
                    headers={"Accept-Language": random.choice(_ACCEPT_LANGUAGES)},
                    allow_redirects=True,
                )
                html = resp.text
                status = resp.status_code
        except Exception as e:
            logger.warning(f"curl_cffi fetch failed for {url}: {str(e)}")
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.CURL_CFFI,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message=str(e),
                ),
                blocked=False,
            )

        if not html:
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.CURL_CFFI,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message=f"HTTP {status}, empty body",
                ),
                blocked=False,
            )

        blocked = _looks_blocked(status, html)
        soup = BeautifulSoup(html, "html.parser")
        data = self._parse_page(soup, html, url, include_links=True)
        anchors = self._collect_anchor_pairs(soup, url)

        confidence = max(
            self._calculate_json_ld_confidence(data.get("jsonld_raw") or {}),
            self._calculate_meta_confidence(data),
        )
        confidence = self._apply_contact_bonus(confidence, data)
        confidence = self._cap_confidence_if_blocked(confidence, blocked)

        result = ScrapingResult(
            success=bool(data) and not blocked,
            data=data,
            method=ScrapingMethod.CURL_CFFI,
            confidence=confidence,
            processing_time=time.time() - start,
            error_message="Anti-bot challenge detected" if blocked else None,
            blocked_detected=blocked,
        )
        return _TierOutcome(result=result, blocked=blocked, anchors=anchors)

    # -- Tier 4: Playwright ---------------------------------------------------

    async def _scrape_with_playwright(self, url: str) -> _TierOutcome:
        start = time.time()
        try:
            import playwright.async_api  # noqa: F401  (availability check)
        except ImportError:
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.PLAYWRIGHT,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message="Playwright not installed",
                ),
                blocked=False,
            )

        page = None
        pool = None
        try:
            pool = await get_browser_pool()
            page = await pool.new_page()

            async def _route_handler(route):
                req = route.request
                req_url = req.url.lower()
                if req.resource_type in ("image", "font", "media"):
                    await route.abort()
                    return
                if any(pat in req_url for pat in _TRACKER_URL_PATTERNS):
                    await route.abort()
                    return
                await route.continue_()

            await page.route("**/*", _route_handler)

            response = None
            try:
                response = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.timeout * 1000
                )
            except Exception as e:
                logger.warning(f"Playwright navigation issue for {url}: {str(e)}")

            if response is not None and response.status in (404, 410, 500, 502):
                return _TierOutcome(
                    result=ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.PLAYWRIGHT,
                        confidence=0.0,
                        processing_time=time.time() - start,
                        error_message=f"HTTP {response.status}",
                    ),
                    blocked=False,
                )

            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass  # many sites never go fully idle; proceed regardless

            await page.wait_for_timeout(1200)

            js_data = await page.evaluate(_EXTRACTION_JS)
            html_for_check = await page.content()
            status_code = response.status if response else 200
            blocked = _looks_blocked(status_code, html_for_check)

            soup = BeautifulSoup(html_for_check, "html.parser")
            # include_links=False: js_data already collected `links` via the
            # live DOM (post-JS-execution), which is richer than a static
            # BeautifulSoup pass over the same content would be.
            parsed = self._parse_page(soup, html_for_check, url, include_links=False)
            anchors = self._collect_anchor_pairs(soup, url)

            data: Dict[str, Any] = dict(js_data or {})
            if data.get("mailto_email"):
                data.setdefault("email", data["mailto_email"])
            if data.get("tel_phone"):
                data.setdefault("phone", data["tel_phone"])

            for k, v in parsed.items():
                if v and not data.get(k):
                    data[k] = v
            if parsed.get("text_content"):
                # trust the boilerplate-stripped extractor over raw innerText
                data["text_content"] = parsed["text_content"]

            confidence = self._calculate_playwright_confidence(data)
            confidence = self._cap_confidence_if_blocked(confidence, blocked)

            result = ScrapingResult(
                success=bool(data) and not blocked,
                data=data,
                method=ScrapingMethod.PLAYWRIGHT,
                confidence=confidence,
                processing_time=time.time() - start,
                error_message="Anti-bot challenge detected" if blocked else None,
                blocked_detected=blocked,
            )
            return _TierOutcome(result=result, blocked=blocked, anchors=anchors)

        except Exception as e:
            logger.error(f"Playwright scraping failed for {url}: {str(e)}")
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.PLAYWRIGHT,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message=str(e),
                ),
                blocked=False,
            )
        finally:
            if page is not None and pool is not None:
                await pool.release_page(page)

    # -- Tier 5: multi-page enrichment ---------------------------------------

    def _needs_enrichment(self, data: Dict[str, Any]) -> bool:
        contact_thin = not (
            data.get("email") or data.get("phone") or self._has_social_link(data)
        )
        company_fields = ("industry", "founded_year", "employee_count", "legal_name")
        company_thin = sum(1 for f in company_fields if data.get(f)) == 0
        description_len = len(str(data.get("description") or data.get("text_content") or ""))
        description_thin = description_len < 200
        return contact_thin or company_thin or description_thin

    @staticmethod
    def _has_social_link(data: Dict[str, Any]) -> bool:
        for key in ("linkedin_url", "twitter_url", "facebook_url"):
            if data.get(key):
                return True
        for link in data.get("links", []) or []:
            if any(s in link for s in ("linkedin.com", "twitter.com", "x.com", "facebook.com")):
                return True
        return False

    async def _enrich_from_subpages(
        self, base_url: str, base_outcome: _TierOutcome
    ) -> Optional[_TierOutcome]:
        max_pages = max(1, int(os.getenv("SCRAPER_MAX_PAGES", "6")))
        remaining_budget = max(0, max_pages - 1)
        if remaining_budget <= 0:
            return None

        sitemap_urls: List[str] = []
        try:
            sitemap_urls = await self._get_sitemap_urls(base_url)
        except Exception as e:
            logger.debug(f"Sitemap discovery failed for {base_url}: {e}")

        anchors = base_outcome.anchors or [
            (link, "") for link in (base_outcome.result.data.get("links") or [])
        ]
        candidates = self._discover_candidate_pages(base_url, anchors, sitemap_urls, remaining_budget)
        if not candidates:
            return None

        concurrency = max(1, int(os.getenv("SCRAPER_ENRICHMENT_CONCURRENCY", "3")))
        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_one(link: str, category: str):
            async with semaphore:
                try:
                    html, status, _err = await self._http_get_with_retries(link)
                    if not html or (status and status >= 400):
                        return None
                    if _looks_blocked(status or 200, html):
                        return None
                    page_soup = BeautifulSoup(html, "html.parser")
                    page_data = self._parse_page(page_soup, html, link, include_links=False)
                    return category, link, page_data
                except Exception as e:
                    logger.debug(f"Enrichment fetch failed for {link}: {e}")
                    return None

        fetched = await asyncio.gather(*[_fetch_one(u, c) for u, c in candidates])

        merged_data = dict(base_outcome.result.data)
        pages_scraped = 1
        categories_hit: Set[str] = set()
        corroborated_names: Set[str] = set()
        if merged_data.get("company_name"):
            corroborated_names.add(str(merged_data["company_name"]).strip().lower())

        for item in fetched:
            if not item:
                continue
            category, _link, page_data = item
            pages_scraped += 1
            categories_hit.add(category)
            for k, v in page_data.items():
                if not v:
                    continue
                if k in ("links", "jsonld", "jsonld_raw"):
                    continue  # don't let a subpage's raw jsonld dump crowd out the homepage's
                if k == "company_name":
                    # Merge priority fix: don't just fill gaps -- a homepage
                    # that fell back to a domain guess or a weak title
                    # shouldn't keep winning over a subpage whose name
                    # actually came from Organization metadata. Only
                    # replace when the current value looks weak and the
                    # new one looks like a real name.
                    current = merged_data.get(k)
                    current_is_weak = not current or not self._looks_like_company_name(str(current))
                    if current_is_weak and self._looks_like_company_name(str(v)):
                        merged_data[k] = v
                    elif not current:
                        merged_data[k] = v
                    continue
                if k == "description" and category == "about":
                    # Prefer an About page's summary over thin or clearly
                    # promotional homepage hero/campaign copy.
                    current_desc = merged_data.get("description")
                    if (
                        not current_desc
                        or len(str(current_desc)) < 80
                        or self._reads_as_promotional(str(current_desc))
                    ):
                        merged_data[k] = v
                    continue
                if not merged_data.get(k):
                    merged_data[k] = v
            cname = page_data.get("company_name")
            if cname:
                corroborated_names.add(str(cname).strip().lower())

        if pages_scraped == 1:
            return None

        corroboration_bonus = 0.05 if len(corroborated_names) == 1 else 0.0
        new_confidence = self._calculate_composite_confidence(
            merged_data, pages_scraped, categories_hit, base_outcome.result.confidence
        )
        new_confidence = min(1.0, new_confidence + corroboration_bonus)

        result = ScrapingResult(
            success=True,
            data=merged_data,
            method=ScrapingMethod.MULTI_PAGE,
            confidence=new_confidence,
            processing_time=base_outcome.result.processing_time,
            pages_scraped=pages_scraped,
            blocked_detected=base_outcome.blocked,
        )
        return _TierOutcome(result=result, blocked=base_outcome.blocked, anchors=base_outcome.anchors)

    # -- Sitemap discovery ----------------------------------------------------

    async def _get_sitemap_urls(self, base_url: str) -> List[str]:
        if os.getenv("SCRAPER_FETCH_SITEMAP", "true").lower() != "true":
            return []
        parsed = urlparse(base_url)
        root = f"{parsed.scheme}://{parsed.netloc}"
        if root in self._sitemap_cache:
            return self._sitemap_cache[root]

        sitemap_locations: List[str] = []
        try:
            robots_url = urljoin(root, "/robots.txt")
            async with self.session.get(
                robots_url, timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    text = await resp.text(errors="ignore")
                    for line in text.splitlines():
                        if line.strip().lower().startswith("sitemap:"):
                            sitemap_locations.append(line.split(":", 1)[1].strip())
        except Exception:
            pass

        if not sitemap_locations:
            sitemap_locations.append(urljoin(root, "/sitemap.xml"))

        urls: List[str] = []
        for sm_url in sitemap_locations[:2]:
            urls.extend(await self._fetch_and_parse_sitemap(sm_url, depth=0))
            if len(urls) >= 300:
                break

        self._sitemap_cache[root] = urls
        return urls

    async def _fetch_and_parse_sitemap(self, sitemap_url: str, depth: int) -> List[str]:
        if depth > 1:
            return []
        try:
            async with self.session.get(
                sitemap_url, timeout=aiohttp.ClientTimeout(total=6)
            ) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text(errors="ignore")
        except Exception:
            return []

        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.IGNORECASE | re.DOTALL)
        if not locs:
            return []

        if "<sitemapindex" in text.lower():
            child_urls: List[str] = []
            for child in locs[:3]:
                child_urls.extend(await self._fetch_and_parse_sitemap(child.strip(), depth + 1))
                if len(child_urls) >= 300:
                    break
            return child_urls

        return [u.strip() for u in locs if u.strip()][:300]

    # -- Intelligent link scoring ---------------------------------------------

    def _score_link(self, url: str, anchor_text: str, base_domain: str) -> Tuple[float, Optional[str]]:
        """Returns (score, category). score <= 0 means "not a priority page"
        or "excluded" -- caller should skip it either way."""
        try:
            parsed = urlparse(url)
        except Exception:
            return -1.0, None
        if parsed.scheme not in ("http", "https", ""):
            return -1.0, None
        if parsed.netloc and parsed.netloc.replace("www.", "") != base_domain.replace("www.", ""):
            return -1.0, None

        lowered_url = url.lower()
        if any(pat in lowered_url for pat in _EXCLUDE_URL_SUBSTRINGS):
            return -1.0, None

        path = parsed.path.lower()
        anchor = (anchor_text or "").lower()
        best_score = 0.0
        best_category: Optional[str] = None
        for category, meta in _PAGE_CATEGORIES.items():
            weight = meta["weight"]
            for kw in meta["keywords"]:
                if kw in path or kw in anchor:
                    score = weight + (0.1 if kw in path else 0.0)
                    if score > best_score:
                        best_score = score
                        best_category = category

        if best_score <= 0.0:
            return 0.0, None

        depth = len([seg for seg in path.split("/") if seg])
        best_score -= max(0, depth - 1) * 0.05
        return max(best_score, 0.0), best_category

    def _discover_candidate_pages(
        self,
        base_url: str,
        anchors: List[Tuple[str, str]],
        sitemap_urls: List[str],
        max_pages: int,
    ) -> List[Tuple[str, str]]:
        """Score, dedupe, and rank candidate pages, capping how many pages
        of any single category get through so a handful of blog posts can't
        crowd out the higher-value about/contact/team pages."""
        base_domain = urlparse(base_url).netloc
        scored: Dict[str, Tuple[float, str]] = {}

        for link, text in anchors:
            score, category = self._score_link(link, text, base_domain)
            if score > 0 and category:
                norm = self._normalize_link(link)
                if norm not in scored or score > scored[norm][0]:
                    scored[norm] = (score, category)

        for link in sitemap_urls:
            score, category = self._score_link(link, "", base_domain)
            if score > 0 and category:
                norm = self._normalize_link(link)
                if norm not in scored or score > scored[norm][0]:
                    scored[norm] = (score, category)

        ranked = sorted(scored.items(), key=lambda kv: kv[1][0], reverse=True)
        seen_categories: Dict[str, int] = {}
        selected: List[Tuple[str, str]] = []
        for link, (_score, category) in ranked:
            cat_count = seen_categories.get(category, 0)
            if cat_count >= 2:  # at most 2 pages per category
                continue
            selected.append((link, category))
            seen_categories[category] = cat_count + 1
            if len(selected) >= max_pages:
                break
        return selected

    @staticmethod
    def _normalize_link(url: str) -> str:
        try:
            parsed = urlparse(url)
            return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))
        except Exception:
            return url

    @staticmethod
    def _collect_anchor_pairs(soup: BeautifulSoup, base_url: str) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith(("http://", "https://")):
                abs_url = href
            elif href.startswith("/"):
                abs_url = urljoin(base_url, href)
            else:
                continue
            text = a.get_text(" ", strip=True)[:80]
            pairs.append((abs_url, text))
        return pairs

    # -- Tier 6: synchronous requests fallback -------------------------------

    async def _scrape_with_requests_fallback(self, url: str) -> _TierOutcome:
        start = time.time()
        try:
            html, status, err = await asyncio.to_thread(self._sync_requests_fetch, url)
        except Exception as e:
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.REQUESTS,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message=str(e),
                ),
                blocked=False,
            )

        if html is None:
            return _TierOutcome(
                result=ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.REQUESTS,
                    confidence=0.0,
                    processing_time=time.time() - start,
                    error_message=err,
                ),
                blocked=False,
            )

        blocked = _looks_blocked(status or 200, html)
        soup = BeautifulSoup(html, "html.parser")
        data = self._parse_page(soup, html, url, include_links=True)
        anchors = self._collect_anchor_pairs(soup, url)

        confidence = self._calculate_playwright_confidence(data) * 0.85
        confidence = self._cap_confidence_if_blocked(confidence, blocked)

        result = ScrapingResult(
            success=bool(data) and not blocked,
            data=data,
            method=ScrapingMethod.REQUESTS,
            confidence=confidence,
            processing_time=time.time() - start,
            error_message="Anti-bot challenge detected" if blocked else None,
            blocked_detected=blocked,
        )
        return _TierOutcome(result=result, blocked=blocked, anchors=anchors)

    @staticmethod
    def _sync_requests_fetch(
        url: str,
    ) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """Runs on a worker thread via asyncio.to_thread so it never blocks
        the event loop."""
        import requests

        last_err = None
        for attempt in range(2):
            try:
                resp = requests.get(url, headers=_random_headers(), timeout=25)
                return resp.text, resp.status_code, None
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(1.0 + attempt)
        return None, None, last_err or "requests fallback failed"

    # -- Unified page parsing pipeline ----------------------------------------

    def _parse_page(
        self, soup: BeautifulSoup, html: str, url: str, include_links: bool = True
    ) -> Dict[str, Any]:
        """Single shared extraction pipeline used by every tier: meta/OG/
        Twitter tags, deep schema.org JSON-LD (many types), contact info
        (original + extended/categorized), company info, and clean main-text
        extraction. Every field the previous implementation produced is
        still produced the same way; this only ever adds new keys."""
        data: Dict[str, Any] = {}

        data.update(self._parse_meta(soup, url, include_links=include_links))

        json_ld_blocks = self._extract_json_ld_blocks(soup)
        if json_ld_blocks:
            flattened: Dict[str, Any] = {}
            for block in json_ld_blocks:
                flattened.update(self._flatten_json(block))
            data.update({k: v for k, v in flattened.items() if v})
            data["jsonld_raw"] = flattened
            data["jsonld"] = json_ld_blocks

            semantic = self._semantic_from_jsonld_blocks(json_ld_blocks)
            data.update({k: v for k, v in semantic.items() if v and not data.get(k)})
            if semantic.get("org_name"):
                data["name"] = semantic["org_name"]  # prefer the typed Organization name

        # Original, untouched contact extraction (email/phone/social priority
        # order preserved exactly as before).
        data.update(self._extract_contact_info(soup, html))

        # New: categorized emails, extra social platforms, fax, address parts.
        extended_contact = self._extract_extended_contacts(soup, html)
        for k, v in extended_contact.items():
            if v and not data.get(k):
                data[k] = v

        company = self._extract_company_info(soup, html, data, url)
        for k, v in company.items():
            if v and not data.get(k):
                data[k] = v

        data["text_content"] = self._extract_main_text(html, url)
        return data

    # -- Schema.org / JSON-LD -------------------------------------------------

    @staticmethod
    def _extract_json_ld_blocks(soup: BeautifulSoup) -> List[Dict[str, Any]]:
        """Parses every JSON-LD script tag into a flat list of dict blocks,
        expanding @graph containers and JSON arrays."""
        blocks: List[Dict[str, Any]] = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                parsed = json.loads(script.string)
            except Exception:
                continue
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        blocks.append(item)
            elif isinstance(parsed, dict):
                if "@graph" in parsed and isinstance(parsed["@graph"], list):
                    for item in parsed["@graph"]:
                        if isinstance(item, dict):
                            blocks.append(item)
                    wrapper = {k: v for k, v in parsed.items() if k != "@graph"}
                    if wrapper.get("@type"):
                        blocks.append(wrapper)
                else:
                    blocks.append(parsed)
        return blocks

    def _semantic_from_jsonld_blocks(self, blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Type-aware extraction across Organization/LocalBusiness/
        Corporation, Product/SoftwareApplication, FAQPage, BreadcrumbList,
        ContactPoint, PostalAddress, WebSite/WebPage schema blocks."""
        out: Dict[str, Any] = {}
        products: List[str] = []
        faq_topics: List[str] = []
        breadcrumbs: List[str] = []

        for block in blocks:
            raw_type = block.get("@type")
            if isinstance(raw_type, list):
                types = {str(t).lower() for t in raw_type}
            elif isinstance(raw_type, str):
                types = {raw_type.lower()}
            else:
                types = set()

            if types & _ORG_SCHEMA_TYPES:
                if block.get("name") and not out.get("org_name"):
                    out["org_name"] = block["name"]
                if block.get("legalName") and not out.get("legal_name"):
                    out["legal_name"] = block["legalName"]
                if block.get("description") and not out.get("description"):
                    out["description"] = block["description"]
                if block.get("foundingDate") and not out.get("founded_year"):
                    year = self._extract_year(str(block["foundingDate"]))
                    if year:
                        out["founded_year"] = year
                if block.get("numberOfEmployees") and not out.get("employee_count"):
                    count = self._stringify_employee_count(block["numberOfEmployees"])
                    if count:
                        out["employee_count"] = count
                if block.get("logo") and not out.get("logo"):
                    logo = self._extract_image_url(block["logo"])
                    if logo:
                        out["logo"] = logo
                if block.get("slogan") and not out.get("tagline"):
                    out["tagline"] = block["slogan"]

                same_as = block.get("sameAs")
                if same_as:
                    same_as_list = same_as if isinstance(same_as, list) else [same_as]
                    for link in same_as_list:
                        self._classify_social_link(str(link), out)

                addr = block.get("address")
                if addr and not out.get("address"):
                    out.update({k: v for k, v in self._parse_postal_address(addr).items() if v})

                contact_points = block.get("contactPoint")
                if contact_points:
                    cp_list = contact_points if isinstance(contact_points, list) else [contact_points]
                    for cp in cp_list:
                        self._classify_contact_point(cp, out)

            if types & _PRODUCT_SCHEMA_TYPES:
                name = block.get("name")
                if name:
                    products.append(str(name))

            if "faqpage" in types:
                main_entity = block.get("mainEntity")
                entities = main_entity if isinstance(main_entity, list) else ([main_entity] if main_entity else [])
                for q in entities:
                    if isinstance(q, dict) and q.get("name"):
                        faq_topics.append(str(q["name"]))

            if "breadcrumblist" in types:
                items = block.get("itemListElement") or []
                for it in items if isinstance(items, list) else []:
                    if not isinstance(it, dict):
                        continue
                    label = it.get("name")
                    if not label and isinstance(it.get("item"), dict):
                        label = it["item"].get("name")
                    if label:
                        breadcrumbs.append(str(label))

            # A WebSite/WebPage schema's `name` is a much weaker signal than
            # a genuine Organization/Corporation block's `name` -- it's
            # frequently auto-generated from whatever headline/campaign
            # content is on the page at render time (this is exactly how a
            # promotional banner string like "AI Masterclass 2026: ..." can
            # end up here). Since org_name outranks og:site_name in
            # _extract_company_info's priority chain, an unguarded value
            # here would win over a clean og:site_name. Validate it the same
            # way the title-tag fallback is validated.
            if (
                not out.get("org_name")
                and (types & {"website", "webpage"})
                and block.get("name")
                and self._looks_like_company_name(str(block["name"]))
            ):
                out.setdefault("org_name", block["name"])

        if products:
            out["products"] = list(dict.fromkeys(products))[:10]
        if faq_topics:
            out["faq_topics"] = faq_topics[:5]
        if breadcrumbs:
            out["breadcrumb_trail"] = breadcrumbs[:6]

        return out

    @staticmethod
    def _extract_year(text: str) -> Optional[int]:
        m = re.search(r"(19|20)\d{2}", text)
        return int(m.group(0)) if m else None

    @staticmethod
    def _stringify_employee_count(value: Any) -> Optional[str]:
        if isinstance(value, dict):
            lo = value.get("minValue") or value.get("value")
            hi = value.get("maxValue")
            if lo and hi:
                return f"{lo}-{hi}"
            if lo:
                return str(lo)
            return None
        if isinstance(value, (int, float)):
            return str(int(value))
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _extract_image_url(value: Any) -> Optional[str]:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return value.get("url")
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                return first.get("url")
        return None

    @staticmethod
    def _classify_social_link(link: str, out: Dict[str, Any]) -> None:
        lowered = link.lower()
        for field_name, domains in _SOCIAL_DOMAIN_MAP.items():
            if out.get(field_name):
                continue
            if any(d in lowered for d in domains):
                out[field_name] = link
                return

    @staticmethod
    def _parse_postal_address(addr: Any) -> Dict[str, Any]:
        if isinstance(addr, str):
            return {"address": addr}
        if not isinstance(addr, dict):
            return {}
        street = addr.get("streetAddress")
        city = addr.get("addressLocality")
        region = addr.get("addressRegion")
        postal = addr.get("postalCode")
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")

        parts = [p for p in (street, city, region, postal, country) if p]
        result: Dict[str, Any] = {}
        if parts:
            result["address"] = ", ".join(str(p) for p in parts)
        if city:
            result["city"] = city
        if country:
            result["country"] = country
        if postal:
            result["postal_code"] = postal
        return result

    @staticmethod
    def _classify_contact_point(cp: Any, out: Dict[str, Any]) -> None:
        if not isinstance(cp, dict):
            return
        contact_type = str(cp.get("contactType") or "").lower()
        email = cp.get("email")
        phone = cp.get("telephone")

        field_prefix = None
        if "sales" in contact_type:
            field_prefix = "sales"
        elif "support" in contact_type or "customer service" in contact_type or "technical" in contact_type:
            field_prefix = "support"
        elif "press" in contact_type or "media" in contact_type:
            field_prefix = "press"
        elif "billing" in contact_type:
            field_prefix = "billing"

        if field_prefix:
            if email and not out.get(f"{field_prefix}_email"):
                out[f"{field_prefix}_email"] = email
            if phone and not out.get(f"{field_prefix}_phone"):
                out[f"{field_prefix}_phone"] = phone
        else:
            if email and not out.get("email"):
                out["email"] = email
            if phone and not out.get("phone"):
                out["phone"] = phone

    # -- Meta / OpenGraph / Twitter tags --------------------------------------

    def _parse_meta(self, soup: BeautifulSoup, url: str, include_links: bool = True) -> Dict[str, Any]:
        data: Dict[str, Any] = {}

        title_tag = soup.find("title")
        if title_tag:
            data["title"] = title_tag.get_text().strip()

        desc_tag = soup.find("meta", attrs={"name": "description"})
        if desc_tag:
            data["description"] = desc_tag.get("content", "").strip()

        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            data["canonical_url"] = canonical["href"]

        app_name = soup.find("meta", attrs={"name": "application-name"})
        if app_name and app_name.get("content", "").strip():
            data["application_name"] = app_name["content"].strip()

        og_tags = soup.find_all("meta", property=re.compile(r"^og:"))
        for tag in og_tags:
            prop = tag.get("property", "").replace("og:", "")
            if prop:
                data[f"og_{prop}"] = tag.get("content", "").strip()

        twitter_tags = soup.find_all("meta", attrs={"name": re.compile(r"^twitter:")})
        for tag in twitter_tags:
            name = tag.get("name", "").replace("twitter:", "")
            if name:
                data[f"twitter_{name}"] = tag.get("content", "").strip()

        if include_links:
            links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith(("http://", "https://")):
                    links.append(href)
                elif href.startswith("/"):
                    links.append(urljoin(url, href))
            data["links"] = links

        return data

    # -- Contact extraction ----------------------------------------------------

    def _extract_contact_info(self, soup: BeautifulSoup, html: str) -> Dict[str, Any]:
        """Extract email/phone/social links, including obfuscated & link-based
        patterns that simple body-text regexes miss. Unchanged from the
        previous implementation -- kept verbatim so the priority order for
        `email`/`phone`/`linkedin_url`/`twitter_url`/`facebook_url` never
        shifts under existing callers."""
        data: Dict[str, Any] = {}

        mailto = soup.find("a", href=re.compile(r"^mailto:", re.IGNORECASE))
        if mailto:
            email = mailto["href"].split(":", 1)[1].split("?")[0].strip()
            if email:
                data["email"] = email

        tel = soup.find("a", href=re.compile(r"^tel:", re.IGNORECASE))
        if tel:
            phone = tel["href"].split(":", 1)[1].strip()
            if phone:
                data["phone"] = phone

        if "email" not in data:
            body_text = soup.get_text(" ", strip=True)[:20000]
            email_match = re.search(
                r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", body_text
            )
            if email_match:
                data["email"] = email_match.group(0)

        if "phone" not in data:
            body_text = soup.get_text(" ", strip=True)[:20000]
            phone_match = re.search(
                r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", body_text
            )
            if phone_match:
                data["phone"] = phone_match.group(0).strip()

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "linkedin.com" in href and "linkedin_url" not in data:
                data["linkedin_url"] = href
            elif ("twitter.com" in href or "x.com" in href) and "twitter_url" not in data:
                data["twitter_url"] = href
            elif "facebook.com" in href and "facebook_url" not in data:
                data["facebook_url"] = href

        return data

    @staticmethod
    def _email_prefix_matches(local_part: str, prefix: str) -> bool:
        """True if `local_part` starts with `prefix` at a boundary -- i.e.
        the match isn't just the leading letters of a longer, unrelated
        word. Prevents e.g. "care" (support_email) from swallowing
        "careers@..." (careers_email), or "hr" from swallowing a person's
        initials like "hrivera@...")."""
        if local_part == prefix:
            return True
        if local_part.startswith(prefix):
            remainder = local_part[len(prefix):]
            if not remainder or not remainder[0].isalpha():
                return True
        return False

    def _extract_extended_contacts(self, soup: BeautifulSoup, html: str) -> Dict[str, Any]:
        """Categorized emails (sales/support/press/privacy/careers/contact),
        fax numbers, and additional social platforms (Instagram, YouTube,
        GitHub, Crunchbase, Glassdoor). Purely additive -- never touches the
        `email`/`phone`/`linkedin_url`/`twitter_url`/`facebook_url` keys that
        `_extract_contact_info` already owns."""
        out: Dict[str, Any] = {}

        # Categorized emails can appear either as mailto links OR as plain
        # body text (very common on About/Contact pages) -- scan both so a
        # page that lists "press@x.com" as plain text still gets picked up.
        found_emails: List[str] = []
        for a in soup.find_all("a", href=re.compile(r"^mailto:", re.IGNORECASE)):
            email = a["href"].split(":", 1)[1].split("?")[0].strip()
            if email:
                found_emails.append(email)
        body_text_for_emails = soup.get_text(" ", strip=True)[:20000]
        found_emails.extend(
            re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", body_text_for_emails)
        )

        for email in found_emails:
            if "@" not in email:
                continue
            domain = email.rsplit("@", 1)[-1].lower()
            if domain in _EXCLUDED_EMAIL_DOMAINS:
                continue
            local_part = email.split("@")[0].lower()
            for field_name, prefixes in _EMAIL_PREFIX_CATEGORIES.items():
                if out.get(field_name):
                    continue
                if any(self._email_prefix_matches(local_part, p) for p in prefixes):
                    out[field_name] = email
                    break

        # Fax: look only in the text immediately following the word "fax"
        # within its own text node, so a nearby (but unrelated) phone number
        # earlier in the same paragraph doesn't get mistaken for the fax.
        fax_label = soup.find(string=re.compile(r"\bfax\b", re.IGNORECASE))
        if fax_label:
            fax_text = str(fax_label)
            fax_kw = re.search(r"\bfax\b", fax_text, re.IGNORECASE)
            if fax_kw:
                window = fax_text[fax_kw.end(): fax_kw.end() + 40]
                fax_match = re.search(
                    r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", window
                )
                if fax_match:
                    out["fax"] = fax_match.group(0).strip()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            lowered = href.lower()
            for field_name, domains in _SOCIAL_DOMAIN_MAP.items():
                if field_name in out:
                    continue
                if any(d in lowered for d in domains):
                    out[field_name] = href

        return out

    # -- Company info extraction ------------------------------------------------

    @staticmethod
    def _looks_like_company_name(candidate: str) -> bool:
        """True if `candidate` reads like a company/brand name rather than a
        marketing headline or sentence. Guards the <title>/og:title fallback
        used by `_extract_company_info` -- structured sources (JSON-LD,
        og:site_name, application-name) are never run through this, since
        they're authored as names already."""
        text = (candidate or "").strip()
        if not text or len(text) > 60:
            return False
        if len(text.split()) > 6:
            return False
        # A period/colon/semicolon/! or ? followed by more text means this is
        # a sentence or headline, not a name (e.g. "Masterclass 2026: On-...").
        if re.search(r"[.:;!?]\s*\S", text):
            return False
        if _PROMO_PATTERNS.search(text):
            return False
        return True

    @staticmethod
    def _reads_as_promotional(text: str) -> bool:
        """Lighter-weight version of `_looks_like_company_name` for longer
        free text (e.g. a homepage hero description) -- flags campaign/CTA
        copy so it can be preferred against by a more substantive About-page
        summary during multi-page merge."""
        return bool(_PROMO_PATTERNS.search(text or ""))

    @staticmethod
    def _extract_logo_company_name(soup: BeautifulSoup) -> Optional[str]:
        """Some sites never expose an Organization name in JSON-LD/meta but
        do put it in the header logo's alt text (e.g. alt="Acme Corp logo").
        Best-effort, low-risk: only used when nothing more structured is
        available, and only when the alt text itself looks like a name."""
        try:
            logo_img = soup.select_one(
                'img[class*="logo" i], img[id*="logo" i], img[src*="logo" i]'
            )
        except Exception:
            logo_img = None
        if not logo_img:
            return None
        alt = (logo_img.get("alt") or "").strip()
        if not alt:
            return None
        cleaned = re.sub(r"\s*(logo|icon)\s*$", "", alt, flags=re.IGNORECASE).strip()
        return cleaned if TieredScraper._looks_like_company_name(cleaned) else None

    def _extract_company_info(
        self, soup: BeautifulSoup, html: str, existing_data: Dict[str, Any], url: str
    ) -> Dict[str, Any]:
        """Best-effort, additive company signals: clean company_name,
        founded_year, employee_count, revenue_hint, tagline, mission
        statement, and a lightweight technology fingerprint. Everything here
        only fills gaps `existing_data` doesn't already have."""
        out: Dict[str, Any] = {}

        domain = urlparse(url).netloc.replace("www.", "")
        domain_root = domain.split(".")[0] if domain else None

        # Ranked fallback chain for company_name. `org_name` is trusted as-is
        # -- it only ever comes from a genuine Organization/Corporation
        # block, or from a WebSite/WebPage block that has already passed
        # `_looks_like_company_name` (see `_semantic_from_jsonld_blocks`).
        # `name`, in contrast, is populated by the raw JSON-LD flatten pass
        # in `_parse_page` from *any* block's top-level `name` field
        # (Product, ImageObject, WebPage, whatever) with no type-awareness
        # at all, so it gets the same validation as the title fallback --
        # this is precisely the gap that let the promotional Zendesk string
        # ("AI Masterclass 2026: ...") through even after org_name itself
        # was fixed: it was reaching `company_name` via the unguarded `name`
        # key, not via `org_name`.
        candidate_name = existing_data.get("org_name")
        if not candidate_name:
            raw_name = existing_data.get("name")
            if raw_name and self._looks_like_company_name(str(raw_name)):
                candidate_name = raw_name
        if not candidate_name:
            candidate_name = (
                existing_data.get("og_site_name")
                or existing_data.get("application_name")
                or self._extract_logo_company_name(soup)
            )
        if not candidate_name:
            og_title = existing_data.get("og_title")
            title = existing_data.get("title")
            for raw_title in (og_title, title):
                if not raw_title:
                    continue
                cleaned = re.split(r"\s*[|\-\u2013]\s*", raw_title, maxsplit=1)[0].strip()
                if self._looks_like_company_name(cleaned):
                    candidate_name = cleaned
                    break
        if not candidate_name and domain_root:
            candidate_name = domain_root.capitalize()
        if candidate_name:
            out["company_name"] = candidate_name

        if domain_root:
            out.setdefault("potential_company_name", domain_root)

        text_sample = str(existing_data.get("text_content") or "")[:5000]
        if not text_sample:
            text_sample = soup.get_text(" ", strip=True)[:5000]

        if not existing_data.get("founded_year"):
            founded_match = re.search(
                r"(?:founded|established|since|est\.)\s*(?:in\s*)?((?:19|20)\d{2})",
                text_sample, re.IGNORECASE,
            )
            if founded_match:
                out["founded_year"] = int(founded_match.group(1))

        if not existing_data.get("employee_count"):
            emp_match = re.search(
                r"(\d{1,3}(?:,\d{3})?\+?)\s*(?:employees|team members)\b",
                text_sample, re.IGNORECASE,
            )
            if emp_match:
                out["employee_count"] = emp_match.group(1)

        revenue_match = re.search(
            r"\$\s?\d+(?:\.\d+)?\s?(?:million|billion|M|B)\b",
            text_sample, re.IGNORECASE,
        )
        if revenue_match:
            window = text_sample[max(0, revenue_match.start() - 40): revenue_match.end() + 40].lower()
            if "revenue" in window:
                out["revenue_hint"] = revenue_match.group(0).strip()

        if not existing_data.get("tagline"):
            og_title = existing_data.get("og_title")
            title = existing_data.get("title")
            if og_title and title and og_title != title and len(og_title) < 100:
                parts = re.split(r"\s*[|\-\u2013]\s*", og_title, maxsplit=1)
                if len(parts) > 1 and parts[1].strip():
                    out["tagline"] = parts[1].strip()

        mission_match = re.search(
            r"(?:our\s+mission(?:\s+is)?|mission\s*:)\s*([^.]{20,240}\.)",
            text_sample, re.IGNORECASE,
        )
        if mission_match:
            out["mission_statement"] = mission_match.group(1).strip()

        technologies = []
        haystack = html[:20000].lower()
        for tech, signatures in _TECH_SIGNATURES.items():
            if any(sig in haystack for sig in signatures):
                technologies.append(tech)
        if technologies:
            out["technologies"] = technologies

        return out

    # -- Main text extraction ---------------------------------------------------

    @staticmethod
    def _extract_main_text(html: str, url: str) -> str:
        """Boilerplate-free main content extraction. Falls back to a
        semantic-container-aware BeautifulSoup pass (main/article/[role=main]
        /#content/.content/section), then a plain body dump, if trafilatura
        is unavailable or yields nothing (e.g. near-empty SPA shells)."""
        try:
            import trafilatura

            extracted = trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            if extracted and len(extracted.strip()) > 50:
                return extracted[:10000]
        except Exception:
            pass

        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all(["script", "style", "nav", "footer", "header", "form", "noscript"]):
                tag.decompose()
            try:
                for tag in soup.select('[class*="cookie" i], [id*="cookie" i], [class*="banner" i]'):
                    tag.decompose()
            except Exception:
                pass  # older soupsieve without case-insensitive attr selectors

            container = None
            for selector in ("main", "article", "[role=main]", "#content", ".content", "section"):
                try:
                    found = soup.select_one(selector)
                except Exception:
                    found = None
                if found and len(found.get_text(strip=True)) > 200:
                    container = found
                    break

            target = container or soup.body or soup
            text = target.get_text(" ", strip=True) if target else ""
            return text[:8000]
        except Exception:
            return ""

    # -- Shared parsing helpers ----------------------------------------------

    def _flatten_json(
        self, obj: Any, parent_key: str = "", sep: str = "_"
    ) -> Dict[str, Any]:
        """Flatten nested JSON structure"""
        items = []

        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                if isinstance(v, (dict, list)):
                    items.extend(self._flatten_json(v, new_key, sep=sep).items())
                else:
                    items.append((new_key, v))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new_key = f"{parent_key}{sep}{i}" if parent_key else str(i)
                if isinstance(v, (dict, list)):
                    items.extend(self._flatten_json(v, new_key, sep=sep).items())
                else:
                    items.append((new_key, v))

        return dict(items)

    @staticmethod
    def _apply_contact_bonus(confidence: float, data: Dict[str, Any]) -> float:
        """Small corroborating bonus for direct email/phone presence, used
        by the static-fetch confidence tiers (json_ld/meta), which -- unlike
        the Playwright/fallback confidence function -- don't already score
        contact fields. Shared here so the static and curl_cffi tiers don't
        duplicate this exact snippet."""
        if data.get("email") or data.get("phone"):
            return min(1.0, confidence + 0.05)
        return confidence

    @staticmethod
    def _cap_confidence_if_blocked(confidence: float, blocked: bool) -> float:
        """An anti-bot interstitial means whatever got parsed came from a
        challenge page, not real content, so confidence is hard-capped
        regardless of how "complete" the parsed fields look. Shared across
        every tier instead of repeating the same `if blocked: ...` check."""
        return min(confidence, 0.2) if blocked else confidence

    def _calculate_json_ld_confidence(self, data: Dict[str, Any]) -> float:
        score = 0.0
        if "name" in data or "legalName" in data:
            score += 0.3
        if "description" in data:
            score += 0.2
        if "url" in data:
            score += 0.1
        if "email" in data or "telephone" in data:
            score += 0.1
        if "address" in data:
            score += 0.2
        if "foundingDate" in data:
            score += 0.1

        business_properties = [
            "employeeCount",
            "revenue",
            "founded",
            "industry",
            "contactPoint",
            "location",
            "logo",
        ]
        lowered = str(data).lower()
        for prop in business_properties:
            if prop.lower() in lowered:
                score += 0.1

        return min(score, 1.0)

    def _calculate_meta_confidence(self, data: Dict[str, Any]) -> float:
        score = 0.0
        if data.get("title"):
            score += 0.3
        if data.get("description"):
            score += 0.3
        if data.get("og_title") or data.get("og_description"):
            score += 0.2
        if len(data.get("links", []) or []) > 0:
            score += 0.1
        if data.get("og_image"):
            score += 0.1
        return min(score, 1.0)

    def _calculate_playwright_confidence(self, data: Dict[str, Any]) -> float:
        # The 0.3 baseline is meant to reward "rendering produced usable
        # content," not "rendering ran." A result carrying nothing but a
        # domain-derived company_name/potential_company_name (e.g. the
        # cloudflare.com case: {"company_name": "Cloudflare"} and nothing
        # else) was previously scored ~0.3-0.4 -- indistinguishable from a
        # page that actually yielded a title/description/contact info. Gate
        # the baseline on at least one real content signal being present.
        has_real_signal = bool(
            data.get("title")
            or data.get("meta_description")
            or data.get("og_description")
            or data.get("email")
            or data.get("phone")
            or (data.get("links") and len(data["links"]) > 5)
        )
        score = 0.3 if has_real_signal else 0.05
        if data.get("title"):
            score += 0.2
        if data.get("meta_description") or data.get("og_description"):
            score += 0.2
        if data.get("email"):
            score += 0.2
        if data.get("phone"):
            score += 0.1
        if data.get("links") and len(data["links"]) > 5:
            score += 0.1
        if data.get("potential_company_name"):
            score += 0.1
        return min(score, 1.0)

    def _calculate_composite_confidence(
        self,
        data: Dict[str, Any],
        pages_scraped: int,
        categories_hit: Set[str],
        base_confidence: float,
    ) -> float:
        """Confidence that factors in contact/company completeness and
        cross-page corroboration, used after multi-page enrichment."""
        confidence = base_confidence

        contact_fields = ("email", "phone", "linkedin_url", "address")
        contact_score = sum(1 for f in contact_fields if data.get(f)) / len(contact_fields)
        confidence += contact_score * 0.12

        company_fields = ("company_name", "description", "industry", "founded_year", "employee_count", "legal_name")
        company_score = sum(1 for f in company_fields if data.get(f)) / len(company_fields)
        confidence += company_score * 0.12

        if pages_scraped > 1:
            confidence += min(0.1, 0.03 * (pages_scraped - 1))

        if len(categories_hit) >= 2:
            confidence += 0.03

        return min(1.0, max(0.0, confidence))

    @staticmethod
    def _normalize_url(url: str) -> str:
        url = url.strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = f"https://{url}"
        return url

    @staticmethod
    def _finish(
        result: ScrapingResult,
        start_time: float,
        tiers_attempted: List[str],
        blocked: bool,
    ) -> ScrapingResult:
        result.processing_time = time.time() - start_time
        result.tiers_attempted = tiers_attempted
        result.blocked_detected = blocked or result.blocked_detected
        return result


# Global scraper instance for reuse (kept for backward compatibility with
# any existing caller of get_scraper(); new code should prefer
# `async with TieredScraper() as scraper: ...`).
scraper_instance: Optional[TieredScraper] = None


async def get_scraper() -> TieredScraper:
    """Get or create a global scraper instance"""
    global scraper_instance
    if scraper_instance is None:
        scraper_instance = TieredScraper()
    return scraper_instance