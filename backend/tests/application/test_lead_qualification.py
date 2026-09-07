"""
P1.2: qualification-derivation tests for GET /api/v2/leads/ and
GET /api/v2/leads/{id} -- `is_qualified`, `?qualified=` filtering, and the
regression test proving the root cause identified in the P1.2 audit
(dashboard/frontend comparing lowercased `qualification_label` strings
against a hardcoded ["qualified", "hot", "warm"] list, which never matches
this backend's actual labels of "Hot Lead"/"Warm Lead"/"Cold Lead"/
"Disqualified").

Leads are created directly via the ORM (like tests/application/conftest.py's
`sample_lead` fixture) with `score`/`qualification_label` set explicitly,
rather than run through the real AI pipeline -- qualification derivation
is what's under test here, not the pipeline that produces the score.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

import main
from core.domain.models.lead import Lead


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Lead"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]

    me = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"}).json()
    return token, me["organization_id"], me["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _make_lead(db_session, organization_id, owner_id, score, qualification_label, website=None):
    lead = Lead(
        organization_id=organization_id,
        owner_id=owner_id,
        website=website or f"https://{uuid.uuid4().hex}.example.com",
        score=score,
        qualification_label=qualification_label,
        is_active=True,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead


def _set_threshold(client, token, org_id, threshold):
    r = client.put(
        f"/api/v2/organizations/{org_id}/qualification-settings",
        headers=_auth(token),
        json={"qualification_threshold": threshold},
    )
    assert r.status_code == 200
    return r


# --------------------------------------------------------------------------
# Core predicate: score vs. threshold
# --------------------------------------------------------------------------

def test_score_below_threshold_is_not_qualified(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qderiv_below@example.com")
    _set_threshold(client, token, org_id, 60.0)
    lead = _make_lead(db_session, org_id, user_id, score=59.0, qualification_label="Cold Lead")

    r = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["is_qualified"] is False


def test_score_equal_to_threshold_is_qualified(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qderiv_equal@example.com")
    _set_threshold(client, token, org_id, 60.0)
    lead = _make_lead(db_session, org_id, user_id, score=60.0, qualification_label="Warm Lead")

    r = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["is_qualified"] is True


def test_score_above_threshold_is_qualified(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qderiv_above@example.com")
    _set_threshold(client, token, org_id, 60.0)
    lead = _make_lead(db_session, org_id, user_id, score=85.0, qualification_label="Hot Lead")

    r = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["is_qualified"] is True


# --------------------------------------------------------------------------
# Threshold changes are configuration, not data mutation
# --------------------------------------------------------------------------

def test_threshold_change_flips_derived_result_without_mutating_stored_fields(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qderiv_flip@example.com")
    _set_threshold(client, token, org_id, 60.0)
    lead = _make_lead(db_session, org_id, user_id, score=70.0, qualification_label="Warm Lead")

    before = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token)).json()
    assert before["is_qualified"] is True
    assert before["score"] == 70.0
    assert before["qualification_label"] == "Warm Lead"

    _set_threshold(client, token, org_id, 75.0)

    after = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token)).json()
    assert after["is_qualified"] is False
    # The stored fields this whole derivation is built on top of must be
    # completely untouched by the threshold change.
    assert after["score"] == 70.0
    assert after["qualification_label"] == "Warm Lead"


def test_legacy_qualification_label_and_is_qualified_are_allowed_to_disagree(client, db_session):
    """Documents the P1.2 design decision explicitly: a lenient
    organization can consider a "Cold Lead" (by the legacy fixed 80/60/40
    banding) qualified. The two concepts are independent by design -- see
    core/domain/schemas/lead.py::LeadWithQualification."""
    token, org_id, user_id = _register_and_login(client, "qderiv_disagree@example.com")
    _set_threshold(client, token, org_id, 50.0)
    lead = _make_lead(db_session, org_id, user_id, score=55.0, qualification_label="Cold Lead")

    body = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token)).json()
    assert body["qualification_label"] == "Cold Lead"
    assert body["is_qualified"] is True


# --------------------------------------------------------------------------
# Null / missing data semantics
# --------------------------------------------------------------------------

def test_null_score_lead_is_not_qualified_and_does_not_error(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qderiv_null@example.com")
    lead = Lead(
        organization_id=org_id,
        owner_id=user_id,
        website=f"https://{uuid.uuid4().hex}.example.com",
        score=None,
        qualification_label=None,
        is_active=True,
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)

    r = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["is_qualified"] is False


def test_missing_qualification_settings_falls_back_to_default_threshold(client, db_session):
    """An organization that has never called the qualification-settings
    endpoints (no row exists yet) must still get a well-defined result --
    the backward-compatible default (60.0) -- not a 500 or a NULL
    threshold. See crud.get_or_create_qualification_settings."""
    token, org_id, user_id = _register_and_login(client, "qderiv_no_settings@example.com")
    lead = _make_lead(db_session, org_id, user_id, score=65.0, qualification_label="Warm Lead")

    r = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["is_qualified"] is True  # 65 >= default 60.0


# --------------------------------------------------------------------------
# Server-side ?qualified= filtering, applied before pagination
# --------------------------------------------------------------------------

def test_qualified_true_filter_returns_only_qualifying_leads(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qfilter_true@example.com")
    _set_threshold(client, token, org_id, 60.0)
    _make_lead(db_session, org_id, user_id, score=80.0, qualification_label="Hot Lead")
    _make_lead(db_session, org_id, user_id, score=30.0, qualification_label="Disqualified")

    r = client.get("/api/v2/leads/?qualified=true&limit=1000", headers=_auth(token))
    assert r.status_code == 200
    scores = [lead["score"] for lead in r.json()]
    assert 80.0 in scores
    assert 30.0 not in scores
    assert all(lead["is_qualified"] for lead in r.json())


def test_qualified_false_filter_returns_only_non_qualifying_leads(client, db_session):
    token, org_id, user_id = _register_and_login(client, "qfilter_false@example.com")
    _set_threshold(client, token, org_id, 60.0)
    _make_lead(db_session, org_id, user_id, score=80.0, qualification_label="Hot Lead")
    _make_lead(db_session, org_id, user_id, score=30.0, qualification_label="Disqualified")

    r = client.get("/api/v2/leads/?qualified=false&limit=1000", headers=_auth(token))
    assert r.status_code == 200
    scores = [lead["score"] for lead in r.json()]
    assert 30.0 in scores
    assert 80.0 not in scores
    assert all(not lead["is_qualified"] for lead in r.json())


def test_qualified_filter_is_applied_before_pagination(client, db_session):
    """With 3 qualifying and 2 non-qualifying leads, `?qualified=true&limit=2`
    must return exactly 2 (still-qualifying) leads out of the 3 that
    exist -- not "the first 2 of all 5, then filtered", which could
    return fewer than `limit` even though more qualifying rows exist."""
    token, org_id, user_id = _register_and_login(client, "qfilter_page@example.com")
    _set_threshold(client, token, org_id, 60.0)
    for score in (61.0, 70.0, 90.0):
        _make_lead(db_session, org_id, user_id, score=score, qualification_label="Warm Lead")
    for score in (10.0, 20.0):
        _make_lead(db_session, org_id, user_id, score=score, qualification_label="Disqualified")

    r = client.get("/api/v2/leads/?qualified=true&limit=2", headers=_auth(token))
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    assert all(lead["is_qualified"] for lead in body)


def test_qualified_filter_never_crosses_organization_boundary(client, db_session):
    token_a, org_a_id, user_a_id = _register_and_login(client, "qfilter_iso_a@example.com")
    token_b, org_b_id, user_b_id = _register_and_login(client, "qfilter_iso_b@example.com")
    _set_threshold(client, token_a, org_a_id, 60.0)
    _set_threshold(client, token_b, org_b_id, 60.0)

    _make_lead(db_session, org_a_id, user_a_id, score=90.0, qualification_label="Hot Lead",
               website="https://org-a-qualified.example.com")
    _make_lead(db_session, org_b_id, user_b_id, score=90.0, qualification_label="Hot Lead",
               website="https://org-b-qualified.example.com")

    r = client.get("/api/v2/leads/?qualified=true&limit=1000", headers=_auth(token_a))
    urls = [lead["website"] for lead in r.json()]
    assert "https://org-a-qualified.example.com" in urls
    assert "https://org-b-qualified.example.com" not in urls


# --------------------------------------------------------------------------
# Regression test: the actual "no qualified leads" bug
# --------------------------------------------------------------------------

def test_regression_hot_lead_label_no_longer_relies_on_string_matching(client, db_session):
    """Reproduces the exact root cause from the P1.2 audit: the dashboard
    computed its qualified-leads KPI with

        leads.filter(l => ["qualified","hot","warm"].includes(
            (l.qualification_label || "").toLowerCase()
        ))

    "Hot Lead".toLowerCase() is "hot lead", which is never in
    ["qualified","hot","warm"] -- so that check always evaluated to false,
    regardless of real data. This test proves two things: (1) that naive
    string check genuinely reproduces as false for this backend's real
    label, and (2) the new authoritative `is_qualified` field the frontend
    should use instead gets it right regardless of label wording.
    """
    token, org_id, user_id = _register_and_login(client, "qregression@example.com")
    _set_threshold(client, token, org_id, 60.0)
    lead = _make_lead(db_session, org_id, user_id, score=85.0, qualification_label="Hot Lead")

    # (1) The old, buggy frontend logic, run verbatim against this
    # backend's actual label value:
    old_buggy_check = lead.qualification_label.lower() in ["qualified", "hot", "warm"]
    assert old_buggy_check is False, (
        "sanity check: this must reproduce the bug (a real 'Hot Lead' "
        "lead failing the old hardcoded string match)"
    )

    # (2) The new authoritative field is correct regardless:
    body = client.get(f"/api/v2/leads/{lead.id}", headers=_auth(token)).json()
    assert body["qualification_label"] == "Hot Lead"
    assert body["is_qualified"] is True


def test_regression_dashboard_qualified_count_via_api_is_correct(client, db_session):
    """End-to-end version of the same regression, at the list-endpoint
    level a dashboard KPI would actually call: three real "Hot Lead"/
    "Warm Lead" leads exist, and the authoritative `?qualified=true`
    count reflects that -- not zero, which is what the old label-string
    matching bug would have effectively produced."""
    token, org_id, user_id = _register_and_login(client, "qregression_dash@example.com")
    _set_threshold(client, token, org_id, 60.0)
    _make_lead(db_session, org_id, user_id, score=85.0, qualification_label="Hot Lead")
    _make_lead(db_session, org_id, user_id, score=65.0, qualification_label="Warm Lead")
    _make_lead(db_session, org_id, user_id, score=30.0, qualification_label="Disqualified")

    r = client.get("/api/v2/leads/?qualified=true&limit=1000", headers=_auth(token))
    assert r.status_code == 200
    assert len(r.json()) == 2
