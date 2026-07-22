"""
Tests for application.discovery.providers.http_utils.
"""

import aiohttp
import pytest

from application.discovery.providers import http_utils
from tests.application.discovery.fakes import FakeResponse, FakeSession, FakeSessionRaises


async def _instant_sleep(*args, **kwargs):
    return None


async def test_get_json_returns_parsed_payload(monkeypatch):
    fake_response = FakeResponse(status=200, json_data={"hello": "world"})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    result = await http_utils.get_json("https://example.com/api")
    assert result == {"hello": "world"}


async def test_post_json_returns_parsed_payload(monkeypatch):
    fake_response = FakeResponse(status=200, json_data={"ok": True})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    result = await http_utils.post_json("https://example.com/api", json_body={"q": "test"})
    assert result == {"ok": True}


async def test_get_json_raises_provider_http_error_on_non_200(monkeypatch):
    fake_response = FakeResponse(status=404, json_data={})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    with pytest.raises(http_utils.ProviderHTTPError) as exc_info:
        await http_utils.get_json("https://example.com/api")
    assert exc_info.value.status == 404


async def test_post_json_raises_provider_http_error_with_status_code(monkeypatch):
    fake_response = FakeResponse(status=429, json_data={})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    with pytest.raises(http_utils.ProviderHTTPError) as exc_info:
        await http_utils.post_json("https://example.com/api")
    assert exc_info.value.status == 429


async def test_transient_errors_are_retried(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)
    attempts = {"n": 0}

    class _FlakySession:
        def __init__(self, *a, **kw):
            attempts["n"] += 1

        def get(self, *a, **kw):
            if attempts["n"] < 2:
                raise aiohttp.ClientError("transient failure")
            return FakeResponse(status=200, json_data={"ok": True})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _FlakySession)

    result = await http_utils.get_json("https://example.com/api")
    assert result == {"ok": True}
    assert attempts["n"] == 2


async def test_provider_http_error_is_not_retried(monkeypatch):
    """A 401/403/429/5xx is a real failure, not a transient one -- it
    should raise immediately rather than burning retry attempts."""
    attempts = {"n": 0}

    class _CountingSession:
        def __init__(self, *a, **kw):
            attempts["n"] += 1

        def get(self, *a, **kw):
            return FakeResponse(status=401, json_data={})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _CountingSession)

    with pytest.raises(http_utils.ProviderHTTPError):
        await http_utils.get_json("https://example.com/api")

    assert attempts["n"] == 1  # no retry attempted


async def test_get_json_retries_then_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **kw: FakeSessionRaises(aiohttp.ClientError("down"))
    )

    with pytest.raises(aiohttp.ClientError):
        await http_utils.get_json("https://example.com/api")