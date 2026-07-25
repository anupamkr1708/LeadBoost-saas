"""
Tests for the Prometheus /metrics endpoint and core.observability.prometheus_metrics
(SECTION 7 of the production-polish brief).
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def test_metrics_endpoint_returns_prometheus_text_format(client):
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "# HELP http_requests_total" in r.text
    assert "# TYPE http_requests_total counter" in r.text


def test_metrics_endpoint_records_the_request_that_fetched_it(client):
    # The very act of calling /metrics is itself an HTTP request that
    # should show up (recorded by the middleware before this handler runs).
    # /live has no external dependencies, so its status is deterministic
    # regardless of whether Redis/Postgres are reachable in this
    # environment (unlike /health, which reflects their availability).
    client.get("/live")
    r = client.get("/metrics")
    assert 'http_requests_total{method="GET",path="/live",status_code="200"}' in r.text


def test_metrics_never_contains_customer_content(client):
    """No emails, business names, or URLs a customer entered -- only
    counts, durations, and status codes, per the brief's explicit privacy
    requirement."""
    client.post("/api/v2/register", json={"email": "metrics_privacy_check@example.com", "password": "x"})
    r = client.get("/metrics")
    assert "metrics_privacy_check" not in r.text


def test_login_success_increments_auth_success_counter(client):
    email = "metrics_auth_success@example.com"
    client.post("/api/v2/register", json={"email": email, "password": "TestPass123!", "first_name": "M"})
    client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})

    r = client.get("/metrics")
    assert 'auth_attempts_total{result="success"}' in r.text


def test_login_failure_increments_auth_failure_counter(client):
    client.post(
        "/api/v2/login", data={"username": "nonexistent_metrics_user@example.com", "password": "wrong"}
    )

    r = client.get("/metrics")
    assert 'auth_attempts_total{result="failure"}' in r.text


def test_dynamic_route_params_never_appear_as_raw_path_labels(client, db_session):
    """A path label like '/leads/{lead_id}' (the route template) must be
    used instead of e.g. '/leads/42' -- otherwise every distinct ID ever
    requested becomes a new, unbounded time series."""
    from core.domain.schemas.lead import LeadCreate
    from core.infrastructure.database import crud

    email = "metrics_route_template@example.com"
    client.post("/api/v2/register", json={"email": email, "password": "TestPass123!", "first_name": "M"})
    token = client.post(
        "/api/v2/login", data={"username": email, "password": "TestPass123!"}
    ).json()["access_token"]
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()

    lead = crud.create_lead(
        db_session,
        LeadCreate(website="https://route-template-check.example.com", organization_id=me["organization_id"], owner_id=me["id"]),
    )

    client.get(f"/api/v2/leads/{lead.id}", headers={"Authorization": f"Bearer {token}"})

    r = client.get("/metrics")
    assert f"/leads/{lead.id}" not in r.text
    assert '"/leads/{lead_id}"' in r.text
