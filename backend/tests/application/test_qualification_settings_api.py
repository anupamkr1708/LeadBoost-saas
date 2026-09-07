"""
P1.2: HTTP-level tests for
  - GET/PUT /api/v2/organizations/{org_id}/qualification-settings
  - Company Profile fields (industry, icp_description) via the existing
    PUT /api/v2/organizations/{org_id}
  - Sender Profile fields (job_title, signature) via the existing
    PUT /api/v2/me

Follows the exact same register/login/client fixture pattern as
tests/application/test_organization_isolation.py.
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
        json={"email": email, "password": "TestPass123!", "first_name": "Qual"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]

    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["organization_id"], me["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Qualification settings: read
# --------------------------------------------------------------------------

def test_get_qualification_settings_requires_auth(client):
    """This codebase's HTTPBearer security scheme returns 403 (not 401)
    for a request with no Authorization header at all -- 401 is reserved
    for a header that's present but invalid/expired (see
    core.infrastructure.auth.security.get_current_user). Same convention
    already used by every other authenticated endpoint (e.g. GET /leads/)."""
    r = client.get("/api/v2/organizations/1/qualification-settings")
    assert r.status_code == 403


def test_get_qualification_settings_returns_default_for_new_organization(client):
    token, org_id, _ = _register_and_login(client, "qual_get_default@example.com")

    r = client.get(f"/api/v2/organizations/{org_id}/qualification-settings", headers=_auth(token))

    assert r.status_code == 200
    body = r.json()
    assert body["qualification_threshold"] == 60.0
    assert body["organization_id"] == org_id


def test_organization_b_cannot_read_organization_as_qualification_settings(client):
    token_a, org_a_id, _ = _register_and_login(client, "qual_iso_a@example.com")
    token_b, _, _ = _register_and_login(client, "qual_iso_b@example.com")

    r = client.get(
        f"/api/v2/organizations/{org_a_id}/qualification-settings", headers=_auth(token_b)
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Qualification settings: update
# --------------------------------------------------------------------------

def test_update_qualification_settings_changes_threshold(client):
    token, org_id, _ = _register_and_login(client, "qual_update@example.com")

    r = client.put(
        f"/api/v2/organizations/{org_id}/qualification-settings",
        headers=_auth(token),
        json={"qualification_threshold": 75.0},
    )
    assert r.status_code == 200
    assert r.json()["qualification_threshold"] == 75.0

    # Persisted, not just echoed back.
    reread = client.get(
        f"/api/v2/organizations/{org_id}/qualification-settings", headers=_auth(token)
    )
    assert reread.json()["qualification_threshold"] == 75.0


@pytest.mark.parametrize("bad_value", [-1.0, 100.5, -0.01, 250.0])
def test_update_qualification_settings_rejects_out_of_range_threshold(client, bad_value):
    token, org_id, _ = _register_and_login(client, f"qual_bad_{bad_value}@example.com")

    r = client.put(
        f"/api/v2/organizations/{org_id}/qualification-settings",
        headers=_auth(token),
        json={"qualification_threshold": bad_value},
    )
    assert r.status_code == 422


def test_organization_b_cannot_update_organization_as_qualification_settings(client):
    token_a, org_a_id, _ = _register_and_login(client, "qual_upd_iso_a@example.com")
    token_b, _, _ = _register_and_login(client, "qual_upd_iso_b@example.com")

    r = client.put(
        f"/api/v2/organizations/{org_a_id}/qualification-settings",
        headers=_auth(token_b),
        json={"qualification_threshold": 10.0},
    )
    assert r.status_code == 403

    # Organization A's setting must be untouched by the rejected attempt.
    reread = client.get(
        f"/api/v2/organizations/{org_a_id}/qualification-settings", headers=_auth(token_a)
    )
    assert reread.json()["qualification_threshold"] == 60.0


def test_update_qualification_settings_requires_auth(client):
    r = client.put(
        "/api/v2/organizations/1/qualification-settings",
        json={"qualification_threshold": 50.0},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Company Profile (industry, icp_description) via existing organization PUT
# --------------------------------------------------------------------------

def test_update_company_profile_fields(client):
    token, org_id, _ = _register_and_login(client, "profile_company@example.com")

    r = client.put(
        f"/api/v2/organizations/{org_id}",
        headers=_auth(token),
        json={"industry": "B2B SaaS", "icp_description": "Series A-C fintech, 50-500 employees"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["industry"] == "B2B SaaS"
    assert body["icp_description"] == "Series A-C fintech, 50-500 employees"

    reread = client.get(f"/api/v2/organizations/{org_id}", headers=_auth(token))
    assert reread.json()["industry"] == "B2B SaaS"


def test_company_profile_update_does_not_touch_qualification_settings(client):
    """Company Profile and Qualification Settings are independent -- see
    core/domain/models/organization.py: updating one must never move the
    other."""
    token, org_id, _ = _register_and_login(client, "profile_iso@example.com")
    client.put(
        f"/api/v2/organizations/{org_id}/qualification-settings",
        headers=_auth(token),
        json={"qualification_threshold": 33.0},
    )

    client.put(
        f"/api/v2/organizations/{org_id}",
        headers=_auth(token),
        json={"industry": "Retail"},
    )

    settings = client.get(
        f"/api/v2/organizations/{org_id}/qualification-settings", headers=_auth(token)
    )
    assert settings.json()["qualification_threshold"] == 33.0


def test_organization_b_cannot_update_organization_as_company_profile(client):
    token_a, org_a_id, _ = _register_and_login(client, "profile_upd_iso_a@example.com")
    token_b, _, _ = _register_and_login(client, "profile_upd_iso_b@example.com")

    r = client.put(
        f"/api/v2/organizations/{org_a_id}",
        headers=_auth(token_b),
        json={"industry": "Should Not Apply"},
    )
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Sender Profile (job_title, signature) via existing PUT /api/v2/me
# --------------------------------------------------------------------------

def test_update_sender_profile_fields(client):
    token, _, _ = _register_and_login(client, "profile_sender@example.com")

    r = client.put(
        "/api/v2/me",
        headers=_auth(token),
        json={"job_title": "Head of Growth", "signature": "Best,\nJane"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["job_title"] == "Head of Growth"
    assert body["signature"] == "Best,\nJane"

    reread = client.get("/api/v2/me", headers=_auth(token))
    assert reread.json()["job_title"] == "Head of Growth"


def test_sender_profile_no_smtp_or_credential_fields_accepted():
    """P1.2 explicitly excludes mailbox credentials -- confirms the schema
    itself has no such field to accidentally accept one (rather than
    relying on the endpoint to silently ignore unknown JSON keys)."""
    from core.domain.schemas.user import UserUpdate

    field_names = set(UserUpdate.model_fields.keys())
    forbidden = {
        "smtp_password", "app_password", "oauth_refresh_token",
        "mailbox_password", "api_secret",
    }
    assert not (field_names & forbidden)
