import aiohttp
import pytest

from application.discovery.exceptions import ProviderError
from application.discovery.providers.overpass_provider import (
    OverpassProvider,
    build_overpass_query,
    resolve_osm_tag,
)
from tests.application.discovery.fakes import FakeResponse, FakeSession, FakeSessionRaises


async def _instant_sleep(*args, **kwargs):
    return None


@pytest.mark.parametrize(
    "category,expected_tag",
    [
        ("shoe stores", ("shop", "shoes")),
        ("dentists", ("amenity", "dentist")),
        ("hotels", ("tourism", "hotel")),
        ("restaurants", ("amenity", "restaurant")),
        ("real estate agencies", ("office", "estate_agent")),
        ("accounting firms", ("office", "accountant")),
    ],
)
def test_resolve_osm_tag_known_categories(category, expected_tag):
    assert resolve_osm_tag(category) == expected_tag


def test_resolve_osm_tag_unknown_category_returns_none():
    assert resolve_osm_tag("widget makers") == (None, None)


def test_build_overpass_query_contains_tag_clause():
    query = build_overpass_query("shoe stores", "Mumbai", 20)
    assert 'area["name"="Mumbai"]' in query
    assert 'nwr["shop"="shoes"]' in query
    assert "out center 20;" in query


def test_build_overpass_query_falls_back_to_name_search():
    query = build_overpass_query("widget makers", "Delhi", 20)
    assert 'nwr["name"~"widget makers",i]' in query


def test_build_overpass_query_strips_quote_injection():
    query = build_overpass_query("shoes", 'Delhi"; drop area;', 20)
    assert '";' not in query.split("area[")[1].split("->")[0]


async def test_search_returns_normalized_candidates(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "elements": [
                {"type": "node", "lat": 1.0, "lon": 2.0, "tags": {"name": "Shoe World", "shop": "shoes"}},
                {"type": "node", "lat": 1.1, "lon": 2.1, "tags": {}},  # unnamed, dropped
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    provider = OverpassProvider()
    results = await provider.search("shoe stores", "Mumbai", 20)

    assert len(results) == 1
    assert results[0].name == "Shoe World"


async def test_search_raises_provider_error_on_http_failure(monkeypatch):
    fake_response = FakeResponse(status=503, json_data={})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    provider = OverpassProvider()
    with pytest.raises(ProviderError):
        await provider.search("shoe stores", "Mumbai", 20)


async def test_search_raises_provider_error_on_connection_failure(monkeypatch):
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda *a, **kw: FakeSessionRaises(aiohttp.ClientError("boom")),
    )
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    provider = OverpassProvider()
    with pytest.raises(ProviderError):
        await provider.search("shoe stores", "Mumbai", 20)
