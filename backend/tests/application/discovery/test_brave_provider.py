import aiohttp
import pytest

from application.discovery.providers.brave_provider import BraveWebsiteResolver, _looks_official
from tests.application.discovery.fakes import FakeResponse, FakeSession


async def _instant_sleep(*args, **kwargs):
    return None


async def test_returns_none_when_api_key_not_configured():
    resolver = BraveWebsiteResolver(api_key=None)
    assert resolver.is_configured() is False
    result = await resolver.resolve_website("Acme Shoes", "Mumbai")
    assert result is None


async def test_resolves_first_official_looking_result(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "web": {
                "results": [
                    {"url": "https://www.facebook.com/acmeshoes", "title": "Acme Shoes on Facebook"},
                    {"url": "https://acmeshoes.example.com", "title": "Acme Shoes - Official Site"},
                ]
            }
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = BraveWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Acme Shoes", "Mumbai")

    assert result == "https://acmeshoes.example.com"


async def test_skips_directory_and_social_results_entirely(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "web": {
                "results": [
                    {"url": "https://www.yelp.com/biz/acme-shoes", "title": "Acme Shoes | Yelp"},
                    {"url": "https://www.justdial.com/acme-shoes", "title": "Acme Shoes"},
                ]
            }
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = BraveWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Acme Shoes", "Mumbai")

    assert result is None


async def test_returns_none_on_http_failure(monkeypatch):
    fake_response = FakeResponse(status=500, json_data={})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    resolver = BraveWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Acme Shoes", "Mumbai")

    assert result is None  # never raises -- resolution failure is a "continue" case


def test_looks_official_rejects_unrelated_domain():
    assert _looks_official("https://totally-unrelated-brand.com", "Acme Shoes") is False


def test_looks_official_accepts_matching_domain():
    assert _looks_official("https://acmeshoes.com", "Acme Shoes") is True
