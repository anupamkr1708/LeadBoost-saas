"""
Shared HTTP helper for WebsiteResolverProvider implementations that call a
third-party JSON search API (Brave, Serper, ...).

Extracted so BraveWebsiteResolver and SerperWebsiteResolver don't each
reimplement the same aiohttp-session-plus-retry boilerplate. Not a new
HTTP client -- still aiohttp, the same library already used by
OverpassProvider and core.infrastructure.scraping.scraper, per "reuse the
project's existing async HTTP client."

Distinguishes two failure classes on purpose:
  - Transient errors (connection/timeout) -> retried via
    application.utils.retry.with_retry, the project's existing retry
    utility.
  - Non-200 HTTP responses (401/403/429/500/...) -> raised immediately as
    ProviderHTTPError, NOT retried here. A 401 (bad API key) or 429 (rate
    limited) retrying into the same failure a few hundred milliseconds
    later wastes the retry budget; callers should catch ProviderHTTPError
    and log/handle it based on `.status` (see serper_provider.py).
"""

from typing import Any, Dict, Optional

import aiohttp

from application.utils.retry import with_retry


class ProviderHTTPError(Exception):
    """Raised for any non-200 response. Carries the HTTP status so callers
    can distinguish 401 (bad key) / 403 (forbidden) / 429 (rate limited) /
    5xx (server error) for logging and error handling."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@with_retry(exceptions=(aiohttp.ClientError, TimeoutError), attempts=2, min_wait=1.0, max_wait=4.0)
async def get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Any]:
    """GET a JSON endpoint with retry on transient failures. Raises
    ProviderHTTPError on any non-200 response.

    `session`, if given, is reused (H1: connection reuse instead of a
    fresh TCP+TLS handshake per call) and is never closed here -- the
    caller that owns it is responsible for its lifecycle. When omitted,
    behavior is byte-identical to before: a session is opened for this
    one call and closed immediately after."""
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    if session is not None:
        async with session.get(url, headers=headers, params=params, timeout=request_timeout) as response:
            if response.status != 200:
                raise ProviderHTTPError(response.status, f"{url} returned HTTP {response.status}")
            return await response.json(content_type=None)
    async with aiohttp.ClientSession(timeout=request_timeout) as owned_session:
        async with owned_session.get(url, headers=headers, params=params) as response:
            if response.status != 200:
                raise ProviderHTTPError(response.status, f"{url} returned HTTP {response.status}")
            return await response.json(content_type=None)


@with_retry(exceptions=(aiohttp.ClientError, TimeoutError), attempts=2, min_wait=1.0, max_wait=4.0)
async def post_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = 10,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Any]:
    """POST a JSON body with retry on transient failures. Raises
    ProviderHTTPError on any non-200 response. See `get_json` for the
    `session` reuse contract -- identical here."""
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    if session is not None:
        async with session.post(url, headers=headers, json=json_body, timeout=request_timeout) as response:
            if response.status != 200:
                raise ProviderHTTPError(response.status, f"{url} returned HTTP {response.status}")
            return await response.json(content_type=None)
    async with aiohttp.ClientSession(timeout=request_timeout) as owned_session:
        async with owned_session.post(url, headers=headers, json=json_body) as response:
            if response.status != 200:
                raise ProviderHTTPError(response.status, f"{url} returned HTTP {response.status}")
            return await response.json(content_type=None)