"""
End-to-end organization (multi-tenant) isolation tests.

The isolation checks themselves already exist throughout
api/endpoints/leads.py (every read/update/delete compares
lead.organization_id against current_user.organization_id, and list
queries are always scoped by organization_id) -- this file adds the
missing test coverage confirming that behavior actually holds at the
HTTP level, per SECTION 12 of the production-polish brief ("Organization
isolation" was explicitly called out as untested).

Leads are created directly via the CRUD layer (not the POST /leads/
endpoint, which kicks off real scraping) so these tests stay fast and
hermetic -- isolation itself is what's under test, not lead creation.
"""

import pytest
from fastapi.testclient import TestClient

import main
from core.domain.schemas.lead import LeadCreate
from core.infrastructure.database import crud


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Iso"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]

    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["organization_id"], me["id"]


def _create_lead_direct(db_session, organization_id, owner_id, website):
    lead = crud.create_lead(
        db_session, LeadCreate(website=website, organization_id=organization_id, owner_id=owner_id)
    )
    return lead.id


def test_organization_a_cannot_read_organization_bs_lead(client, db_session):
    token_a, org_a_id, user_a_id = _register_and_login(client, "org_a_iso@example.com")
    token_b, _, _ = _register_and_login(client, "org_b_iso@example.com")

    lead_id = _create_lead_direct(db_session, org_a_id, user_a_id, "https://org-a-only-lead.example.com")

    r = client.get(f"/api/v2/leads/{lead_id}", headers={"Authorization": f"Bearer {token_b}"})

    assert r.status_code == 403


def test_organization_a_lead_list_never_includes_organization_bs_leads(client, db_session):
    token_a, org_a_id, user_a_id = _register_and_login(client, "org_a_list@example.com")
    token_b, org_b_id, user_b_id = _register_and_login(client, "org_b_list@example.com")

    _create_lead_direct(db_session, org_a_id, user_a_id, "https://org-a-list-lead.example.com")
    _create_lead_direct(db_session, org_b_id, user_b_id, "https://org-b-list-lead.example.com")

    r = client.get("/api/v2/leads/?limit=1000", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200
    urls = [lead["website"] for lead in r.json()]

    assert "https://org-a-list-lead.example.com" in urls
    assert "https://org-b-list-lead.example.com" not in urls


def test_organization_a_cannot_delete_organization_bs_lead(client, db_session):
    token_a, org_a_id, user_a_id = _register_and_login(client, "org_a_del@example.com")
    token_b, _, _ = _register_and_login(client, "org_b_del@example.com")

    lead_id = _create_lead_direct(db_session, org_a_id, user_a_id, "https://org-a-del-lead.example.com")

    r = client.delete(f"/api/v2/leads/{lead_id}", headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403

    # Confirm it's genuinely still there for the owning organization.
    r = client.get(f"/api/v2/leads/{lead_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200


def test_organization_a_cannot_update_organization_bs_lead(client, db_session):
    token_a, org_a_id, user_a_id = _register_and_login(client, "org_a_upd@example.com")
    token_b, _, _ = _register_and_login(client, "org_b_upd@example.com")

    lead_id = _create_lead_direct(db_session, org_a_id, user_a_id, "https://org-a-upd-lead.example.com")

    r = client.put(
        f"/api/v2/leads/{lead_id}",
        json={"company_name": "Hijacked Name"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 403


def test_organization_a_usage_is_independent_of_organization_b(client, db_session):
    """Two tenants' usage/quota state must never leak into each other --
    each organization's daily usage is tracked independently."""
    token_a, org_a_id, user_a_id = _register_and_login(client, "org_a_usage@example.com")
    token_b, _, _ = _register_and_login(client, "org_b_usage@example.com")

    _create_lead_direct(db_session, org_a_id, user_a_id, "https://org-a-usage-lead.example.com")

    usage_a = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token_a}"}).json()
    usage_b = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token_b}"}).json()

    assert usage_a["current_usage"] >= 1
    assert usage_b["current_usage"] == 0


def test_creating_a_second_organization_does_not_reassign_caller(client, db_session):
    """Regression test for a confirmed bug: POST /organizations/ used to do
    `current_user.organization_id = db_org.id`, silently moving the caller
    into the brand-new org on every call. That both orphaned the user from
    their real organization's data and reproduced the previously-reported
    "POST /leads/single -> 403" runtime-validation failure (the request
    supplied the organization_id/owner_id captured at registration, which
    no longer matched current_user.organization_id after the reassignment).
    """
    token, org_id, user_id = _register_and_login(client, "org_owner_stays@example.com")

    r = client.post(
        "/api/v2/organizations/",
        json={"name": "Some Secondary Org", "description": "should not hijack caller"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    new_org_id = r.json()["id"]
    assert new_org_id != org_id

    # The caller must still belong to their original organization.
    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert me["organization_id"] == org_id

    # And must still be able to act within it using the org_id/owner_id
    # captured at registration -- this is exactly what the runtime
    # validation script does and what previously failed with a 403.
    r = client.post(
        "/api/v2/leads/single",
        json={
            "website": "https://org-owner-stays-lead.example.com",
            "organization_id": org_id,
            "owner_id": user_id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["organization_id"] == org_id


def test_creating_a_second_organization_does_not_reset_quota(client, db_session):
    """The freshly created organization must not become an unlimited /
    zero-usage quota-bypass target: it should have no bearing on the
    caller's own (unchanged) organization's usage count."""
    token, org_id, user_id = _register_and_login(client, "org_quota_stays@example.com")

    _create_lead_direct(db_session, org_id, user_id, "https://org-quota-stays-lead.example.com")
    usage_before = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token}"}).json()
    assert usage_before["current_usage"] >= 1

    client.post(
        "/api/v2/organizations/",
        json={"name": "Quota Bypass Attempt Org", "description": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )

    usage_after = client.get("/api/v2/usage", headers={"Authorization": f"Bearer {token}"}).json()
    assert usage_after["current_usage"] == usage_before["current_usage"]
