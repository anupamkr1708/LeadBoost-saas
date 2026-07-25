"""
Website Validator.

A cheap, single-request-per-attempt reachability + sanity check --
deliberately much lighter than
core.infrastructure.scraping.scraper.TieredScraper. This is not the real
scrape (that still happens exactly once, inside the existing
TieredScraper/LeadPipeline, once the resulting Lead enters the pipeline);
it only answers "is this plausibly a real, reachable, non-directory
business website" before a Lead is created for it at all.

Hardening notes (from production log review):
  - A bare `User-Agent`-only header set reads as an obvious non-browser
    request to some sites' bot detection, producing false 403 rejections
    for genuinely legitimate official sites (observed for real store
    pages). Headers now mirror a realistic browser request.
  - `asyncio.TimeoutError` stringifies to `''`, so the previous
    catch-all `except Exception` produced useless, unexplained
    "validation_error: " log/reason strings (observed repeatedly for
    bata.com, which was consistently timing out, not actually invalid).
    Timeouts now get their own clear, explicit reason.
  - A single request had no retry at all, so a transient 429/503/timeout
    permanently rejected an otherwise-valid site. One retry (reusing the
    existing generic retry utility) now covers that.
  - Default timeout raised from 10s to 15s based on observed real-world
    latency from legitimate (slow-to-respond) official sites.
"""

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from application.utils.retry import with_retry
from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.website_validator")

# Directory/social/marketplace/aggregator domains are rejected by default
# -- a Facebook, JustDial, or TradeIndia page is not "the business's
# website" for the purposes of the existing scraping/enrichment pipeline,
# even though it is a real, reachable page. TradeIndia was confirmed
# missing from this list in production (a shoe store was validated
# against a TradeIndia listing page instead of being rejected).
REJECTED_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "yelp.com",
    "justdial.com",
    "indiamart.com",
    "tradeindia.com",
    "exportersindia.com",
    "tripadvisor.com",
    "yellowpages.com",
    "yellowpages.in",
    "google.com",
    "maps.google.com",
    "youtube.com",
    "pinterest.com",
    "sulekha.com",
    "alibaba.com",
    "amazon.com",
    "amazon.in",
    "flipkart.com",
    "meesho.com",
    "olx.in",
    "quikr.com",
    "wikipedia.org",
    # Travel/booking aggregators: these routinely rank well for
    # "<business name> <city>" searches (e.g. "hotels/attractions near
    # X") and their page titles often contain the business's exact name
    # for SEO purposes -- which otherwise looks like a strong match --
    # without being anywhere close to that business's own site.
    "agoda.com",
    "booking.com",
    "practo.com",
    "medindia.net",
    "expedia.com",
    "makemytrip.com",
    "goibibo.com",
    "trivago.com",
    "zomato.com",
    "swiggy.com",
)

_ACCEPTED_STATUS_CODES = (200, 301, 302)

# A realistic browser header set (mirrors the pattern already used in
# core.infrastructure.scraping.scraper for the same reason: a bare
# User-Agent alone is a strong non-browser signal that trips some sites'
# bot detection even for entirely legitimate requests).
#
# Accept-Encoding deliberately excludes "br" (Brotli): aiohttp still
# advertises whatever is listed here regardless of whether a Brotli
# decoder is actually installed, and this environment has none. Many
# modern sites/CDNs (Cloudflare, Vercel, etc.) will then compress the
# response with Brotli anyway, which aiohttp cannot decode when draining
# the response to free the connection -- even though this validator never
# reads the body -- and the request fails with "Can not decode
# content-encoding: brotli (br)". That was silently rejecting a large
# fraction of genuinely valid websites as unreachable. Requesting only
# gzip/deflate (which every server supports) avoids the failure mode
# entirely with no new dependency.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

_DEFAULT_TIMEOUT = int(os.getenv("DISCOVERY_VALIDATOR_TIMEOUT_SECONDS", "15"))


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def is_rejected_domain(url: str) -> bool:
    domain = domain_of(url)
    return any(rejected in domain for rejected in REJECTED_DOMAINS)


def _describe_exception(e: BaseException) -> str:
    """`str(asyncio.TimeoutError())` is `''`, which produced useless,
    unexplained validation reasons. Falls back to the exception's class
    name whenever str() is empty."""
    text = str(e)
    return text if text else type(e).__name__


@dataclass
class ValidationOutcome:
    ok: bool
    normalized_url: Optional[str] = None
    reason: Optional[str] = None


class WebsiteValidator:
    def __init__(self, timeout: int = _DEFAULT_TIMEOUT, allow_directories: bool = False):
        self.timeout = timeout
        self.allow_directories = allow_directories

    async def validate(self, url: Optional[str]) -> ValidationOutcome:
        if not url or not url.strip():
            return ValidationOutcome(ok=False, reason="empty_url")

        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            url = f"https://{url}"

        domain = domain_of(url)
        if not domain:
            return ValidationOutcome(ok=False, reason="invalid_url")

        if not self.allow_directories and is_rejected_domain(url):
            return ValidationOutcome(ok=False, reason="directory_or_social_domain")

        try:
            status, headers, final_url = await self._fetch_with_retry(url)
        except (TimeoutError, aiohttp.ServerTimeoutError):
            return ValidationOutcome(ok=False, reason="request_timed_out")
        except aiohttp.ClientConnectorDNSError:
            return ValidationOutcome(ok=False, reason="dns_resolution_failed")
        except aiohttp.ClientError as e:
            return ValidationOutcome(ok=False, reason=f"connection_error: {_describe_exception(e)}")
        except Exception as e:
            return ValidationOutcome(ok=False, reason=f"validation_error: {_describe_exception(e)}")

        if status not in _ACCEPTED_STATUS_CODES and status != 200:
            return ValidationOutcome(ok=False, reason=f"unreachable_http_{status}")

        content_type = headers.get("Content-Type", "")
        if "text/html" not in content_type and content_type:
            return ValidationOutcome(ok=False, reason=f"non_html_content_type_{content_type}")

        final_domain = domain_of(final_url)
        if not self.allow_directories and final_domain and is_rejected_domain(final_url):
            # The site redirected into a directory/social/marketplace page
            # (e.g. a dead domain parked and redirected to a marketplace)
            # -- reject after the fact too.
            return ValidationOutcome(ok=False, reason="redirected_to_directory_or_social_domain")

        return ValidationOutcome(ok=True, normalized_url=final_url)

    @with_retry(
        exceptions=(aiohttp.ClientError, TimeoutError, aiohttp.ServerTimeoutError),
        attempts=2,
        min_wait=1.0,
        max_wait=4.0,
    )
    async def _fetch_with_retry(self, url: str):
        """One retry on transient failures (429/503/timeout/connection
        reset) -- the previous single-attempt validator permanently
        rejected otherwise-valid sites on a one-off blip."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        ) as session:
            async with session.get(
                url, allow_redirects=True, headers=_DEFAULT_HEADERS
            ) as response:
                # 429/503 are worth retrying (transient); read nothing else
                # from the response in that case, just signal for retry.
                if response.status in (429, 503):
                    raise aiohttp.ClientError(f"Retryable status {response.status}")
                return response.status, dict(response.headers), str(response.url)