"""
Website Validator.

A cheap, single-request reachability + sanity check -- deliberately much
lighter than core.infrastructure.scraping.scraper.TieredScraper. This is
not the real scrape (that still happens exactly as before, once the
resulting Lead enters LeadPipeline); it only answers "is this plausibly a
real, reachable, non-directory business website" before we bother creating
a Lead for it at all.
"""

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import aiohttp

from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.website_validator")

# Directory/social/aggregator domains are rejected by default -- a
# Facebook or JustDial page is not "the business's website" for the
# purposes of the existing scraping/enrichment pipeline, even though it is
# a real, reachable page.
REJECTED_DOMAINS = (
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "yelp.com",
    "justdial.com",
    "indiamart.com",
    "tripadvisor.com",
    "yellowpages.com",
    "google.com",
    "maps.google.com",
    "youtube.com",
    "pinterest.com",
    "sulekha.com",
)

_ACCEPTED_STATUS_CODES = (200, 301, 302)


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        return netloc[4:] if netloc.startswith("www.") else netloc
    except Exception:
        return ""


def is_rejected_domain(url: str) -> bool:
    domain = domain_of(url)
    return any(rejected in domain for rejected in REJECTED_DOMAINS)


@dataclass
class ValidationOutcome:
    ok: bool
    normalized_url: Optional[str] = None
    reason: Optional[str] = None


class WebsiteValidator:
    def __init__(self, timeout: int = 10, allow_directories: bool = False):
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
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as session:
                async with session.get(
                    url,
                    allow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; LeadBoostDiscovery/1.0)"},
                ) as response:
                    if response.status not in _ACCEPTED_STATUS_CODES and response.status != 200:
                        # allow_redirects already resolved 301/302 to a
                        # final status, so in practice only the final
                        # status matters here.
                        return ValidationOutcome(
                            ok=False, reason=f"unreachable_http_{response.status}"
                        )

                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" not in content_type and content_type:
                        return ValidationOutcome(
                            ok=False, reason=f"non_html_content_type_{content_type}"
                        )

                    final_url = str(response.url)
                    final_domain = domain_of(final_url)
                    if not self.allow_directories and final_domain and is_rejected_domain(final_url):
                        # The site redirected into a directory/social page
                        # (e.g. a dead domain parked and redirected to a
                        # marketplace) -- reject after the fact too.
                        return ValidationOutcome(
                            ok=False, reason="redirected_to_directory_or_social_domain"
                        )

                    return ValidationOutcome(ok=True, normalized_url=final_url)

        except aiohttp.ClientConnectorDNSError:
            return ValidationOutcome(ok=False, reason="dns_resolution_failed")
        except aiohttp.ClientError as e:
            return ValidationOutcome(ok=False, reason=f"connection_error: {str(e)[:100]}")
        except Exception as e:
            return ValidationOutcome(ok=False, reason=f"validation_error: {str(e)[:100]}")
