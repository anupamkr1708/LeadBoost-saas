"""
Full-stack integration test: a real HTTP request through TestClient hits
the real DiscoveryService (not a stub, unlike test_discovery_api.py) --
only the network layer (Overpass, Brave, website validator HTTP calls) and
the LeadPipeline execution are mocked. This is the closest thing to a
live end-to-end run possible without real internet access or a real LLM.
"""

import aiohttp
import pytest
from fastapi.testclient import TestClient

from tests.application.discovery.fakes import FakeResponse, FakeSession


@pytest.fixture()
def client(monkeypatch):
    import application.workflows.lead_pipeline as pipeline_module
    import main

    async def _fake_run_lead_pipeline(lead_id):
        return {"lead_id": lead_id, "status": "SUCCESS", "errors": []}

    monkeypatch.setattr(pipeline_module, "run_lead_pipeline", _fake_run_lead_pipeline)
    # api/endpoints/discovery.py imports run_lead_pipeline indirectly via
    # DiscoveryService -> application.discovery.discovery_service, which
    # imported the *name* at module load time -- patch it there too.
    import application.discovery.discovery_service as discovery_service_module

    monkeypatch.setattr(discovery_service_module, "run_lead_pipeline", _fake_run_lead_pipeline)

    with TestClient(main.app) as test_client:
        yield test_client


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Full"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_full_discovery_flow_via_http(client, monkeypatch):
    overpass_response = FakeResponse(
        status=200,
        json_data={
            "elements": [
                {
                    "type": "node",
                    "lat": 19.07,
                    "lon": 72.87,
                    "tags": {
                        "name": "Mumbai Shoe World",
                        "shop": "shoes",
                        "website": "shoeworld.example.com",
                        "phone": "+91 22 5555 1234",
                    },
                },
                {
                    "type": "node",
                    "lat": 19.08,
                    "lon": 72.88,
                    "tags": {"name": "No Website Shoes", "shop": "shoes"},
                },
            ]
        },
    )
    # Website validator's GET request; used for both the Overpass-supplied
    # website and (if reached) any Brave-resolved candidate.
    validator_response = FakeResponse(
        status=200, headers={"Content-Type": "text/html"}, url="https://shoeworld.example.com/"
    )

    def fake_session_factory(*args, **kwargs):
        # aiohttp.ClientSession is constructed fresh per call in this
        # codebase; route based on which one gets used first is not
        # possible generically, so use one session stub that answers with
        # the *validator* shape by default, and special-case the Overpass
        # POST via response inspection isn't feasible here -- instead we
        # rely on OverpassProvider always POSTing and WebsiteValidator
        # always GETting, and return the right canned response per verb.
        class _Router:
            def post(self, *a, **kw):
                return overpass_response

            def get(self, *a, **kw):
                return validator_response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        return _Router()

    monkeypatch.setattr(aiohttp, "ClientSession", fake_session_factory)

    token = _register_and_login(client, "full_flow_test@example.com")
    r = client.post(
        "/api/v2/discovery/search",
        json={"query": "Top shoe stores in Mumbai", "limit": 20},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["businesses_found"] == 2

    validated = [b for b in body["businesses"] if b["status"] == "validated"]
    assert len(validated) == 1
    assert validated[0]["website"] == "https://shoeworld.example.com/"
    assert validated[0]["pipeline_status"] == "SUCCESS"
    assert validated[0]["lead_id"] is not None

    no_website = [b for b in body["businesses"] if b["status"] == "no_website"]
    assert len(no_website) == 1
    assert no_website[0]["name"] == "No Website Shoes"

    # Discovery analytics endpoint should now reflect this run.
    r2 = client.get("/api/v2/analytics/discovery-metrics", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    metrics = r2.json()
    assert metrics["total_discovery_runs"] == 1
    assert metrics["total_businesses_found"] == 2
    assert metrics["total_leads_created"] == 1
