"""
HTTP-level tests for /api/v2 billing endpoints.

Focused on PART 8 of the brief: online payments aren't live yet, so
POST /upgrade must never actually change an organization's plan -- no
user should be able to unlock paid-tier limits or features for free by
calling it (or by changing frontend state).
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Bill"},
    )
    assert r.status_code == 200, r.text
    r = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_new_organization_starts_on_free_plan_with_50_leads_per_day(client):
    token = _register_and_login(client, "billing_signup@example.com")

    r = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_name"] == "free"
    assert body["max_leads_per_day"] == 50


def test_upgrade_endpoint_does_not_activate_the_plan(client):
    """The core PART 8 fix: calling /upgrade must not change the
    organization's actual plan -- only a verified payment flow should."""
    token = _register_and_login(client, "billing_upgrade_attempt@example.com")

    r = client.post(
        "/api/v2/upgrade",
        params={"plan_name": "enterprise"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 402
    body = r.json()
    assert body["activated"] is False
    assert "coming soon" in body["message"].lower()

    # The organization's actual usage/limits must be completely
    # unaffected -- still free-tier, not silently upgraded.
    usage = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token}"})
    assert usage.json()["plan_name"] == "free"
    assert usage.json()["max_leads_per_day"] == 50


def test_upgrade_endpoint_rejects_unknown_plan_name(client):
    token = _register_and_login(client, "billing_bad_plan@example.com")

    r = client.post(
        "/api/v2/upgrade",
        params={"plan_name": "super-deluxe-plan"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert r.status_code == 400


def test_upgrade_requires_auth(client):
    r = client.post("/api/v2/upgrade", params={"plan_name": "pro"})
    assert r.status_code in (401, 403)


def test_repeated_upgrade_calls_never_unlock_paid_features(client, monkeypatch):
    """Simulates someone hammering the endpoint (or repeatedly toggling
    frontend state) trying to sneak past the gate -- must never succeed."""
    monkeypatch.setenv("CAN_USE_AI_FREE", "false")
    monkeypatch.setenv("CAN_USE_AI_ENTERPRISE", "true")
    token = _register_and_login(client, "billing_repeat_attempt@example.com")

    for _ in range(5):
        client.post(
            "/api/v2/upgrade",
            params={"plan_name": "enterprise"},
            headers={"Authorization": f"Bearer {token}"},
        )

    usage = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token}"})
    assert usage.json()["plan_name"] == "free"
    assert usage.json()["can_use_ai"] is False
