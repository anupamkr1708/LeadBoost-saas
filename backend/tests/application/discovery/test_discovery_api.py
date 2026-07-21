"""
HTTP-level test for POST /api/v2/discovery/search.

DiscoveryService itself is already extensively tested in
test_discovery_service.py; this test only verifies the endpoint's request
validation, auth wiring, and response shape, so DiscoveryService is
monkeypatched to a stub that returns a canned DiscoveryResponse instantly.
"""

import pytest
from fastapi.testclient import TestClient

from application.discovery.dto import DiscoveryResponse, LeadCreationOutcome


class _StubDiscoveryService:
    def __init__(self, db):
        pass

    async def discover_and_create_leads(self, query, organization_id, owner_id, limit=None):
        return DiscoveryResponse(
            query=query,
            category="shoe stores",
            location="Mumbai",
            requested_limit=limit or 20,
            businesses_found=1,
            businesses=[
                LeadCreationOutcome(
                    name="Shoe World",
                    website="https://shoeworld.example.com",
                    status="validated",
                    lead_id=1,
                    pipeline_status="SUCCESS",
                )
            ],
            duration_ms=42,
        )


@pytest.fixture()
def client_with_stubbed_discovery(monkeypatch):
    import api.endpoints.discovery as discovery_endpoint
    import main

    monkeypatch.setattr(discovery_endpoint, "DiscoveryService", _StubDiscoveryService)
    with TestClient(main.app) as client:
        yield client


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Disc"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_discovery_search_returns_expected_shape(client_with_stubbed_discovery):
    client = client_with_stubbed_discovery
    token = _register_and_login(client, "discovery_api_test@example.com")

    r = client.post(
        "/api/v2/discovery/search",
        json={"query": "Top shoe stores in Mumbai", "limit": 20},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "Top shoe stores in Mumbai"
    assert body["businesses_found"] == 1
    assert body["businesses"][0]["status"] == "validated"
    assert body["businesses"][0]["pipeline_status"] == "SUCCESS"


def test_discovery_search_requires_auth(client_with_stubbed_discovery):
    client = client_with_stubbed_discovery
    r = client.post("/api/v2/discovery/search", json={"query": "Hotels in Goa"})
    assert r.status_code in (401, 403)


def test_discovery_search_rejects_too_short_query(client_with_stubbed_discovery):
    client = client_with_stubbed_discovery
    token = _register_and_login(client, "discovery_api_test2@example.com")

    r = client.post(
        "/api/v2/discovery/search",
        json={"query": "ab"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422


def test_discovery_search_rejects_limit_above_50(client_with_stubbed_discovery):
    client = client_with_stubbed_discovery
    token = _register_and_login(client, "discovery_api_test3@example.com")

    r = client.post(
        "/api/v2/discovery/search",
        json={"query": "Top shoe stores in Mumbai", "limit": 500},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 422
