"""
Shared fake aiohttp primitives for discovery tests. No test in this
package makes a real network call -- every external provider (Overpass,
Brave, and the website validator's own HTTP check) is mocked via these.
"""

from typing import Any, Dict, Optional


class FakeResponse:
    def __init__(
        self,
        status: int = 200,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        url: str = "https://example.com",
    ):
        self.status = status
        self._json_data = json_data if json_data is not None else {}
        self.headers = headers or {"Content-Type": "text/html"}
        self.url = url

    async def json(self, content_type=None):
        return self._json_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSession:
    def __init__(self, response: FakeResponse):
        self._response = response

    def post(self, *args, **kwargs):
        return self._response

    def get(self, *args, **kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeSessionRaises:
    """Simulates a connection-level failure (e.g. DNS/timeout)."""

    def __init__(self, exception: Exception):
        self._exception = exception

    def post(self, *args, **kwargs):
        raise self._exception

    def get(self, *args, **kwargs):
        raise self._exception

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False
