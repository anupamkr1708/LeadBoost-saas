"""
Boundary regression tests for the customer analytics routes
(/api/v2/analytics/{pipeline,evaluation,discovery}-metrics).

Synthetic and local only: throwaway SQLite (see conftest.py), no network.

Covers:
  1. A user whose organization_id is NULL (legacy / detached row) must NOT receive global
     analytics. AnalyticsService treats organization_id=None as "no filter", so before the
     guard such a user was served every tenant's aggregates.
  2. `hours` is a positive integer bounded by MAX_ANALYTICS_HOURS; anything else is an
     ordinary 422, never an uncaught 500 (hours=10**9 used to raise OverflowError).
  3. Tenant isolation and the existing response contract for valid users are unchanged.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import main
from api.endpoints.analytics import MAX_ANALYTICS_HOURS
from application.observability import repository as obs_repo
from core.domain.models.user import User
from core.domain.schemas.lead import LeadCreate
from core.infrastructure.database import crud

ROUTES = (
    "/api/v2/analytics/pipeline-metrics",
    "/api/v2/analytics/evaluation-metrics",
    "/api/v2/analytics/discovery-metrics",
)


@pytest.fixture()
def client():
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register(client, label):
    email = f"{label}_{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Analytics"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]
    me = client.get("/api/v2/me", headers=_auth(token)).json()
    return token, me


def _seed_org_analytics(db, me, *, pipelines=2, evaluations=1, discoveries=1):
    """Give one organization recognizable analytics rows (pipeline/evaluation/discovery)."""
    lead = crud.create_lead(
        db,
        LeadCreate(
            website=f"https://{uuid.uuid4().hex[:8]}.analytics-seed.example.com",
            organization_id=me["organization_id"],
            owner_id=me["id"],
        ),
    )
    now = datetime.now(timezone.utc)
    for _ in range(pipelines):
        obs_repo.create_pipeline_execution_record(
            db,
            pipeline_id=uuid.uuid4().hex,
            lead_id=lead.id,
            organization_id=me["organization_id"],
            started_at=now - timedelta(minutes=30),
            completed_at=now - timedelta(minutes=29),
            duration_ms=1000,
            final_status="SUCCESS",
        )
    for _ in range(evaluations):
        obs_repo.create_evaluation_report_record(
            db,
            pipeline_id=uuid.uuid4().hex,
            lead_id=lead.id,
            organization_id=me["organization_id"],
            confidence=0.5,
            completeness=0.5,
            grounding=0.5,
            consistency=0.5,
            overall=0.5,
        )
    for _ in range(discoveries):
        obs_repo.create_discovery_run_record(
            db,
            organization_id=me["organization_id"],
            query="seed",
            category=None,
            location=None,
            requested_limit=10,
            businesses_returned=10,
            businesses_missing_website=0,
            websites_resolved_via_fallback=0,
            duplicates_removed=0,
            validated_leads=10,
            duration_ms=500,
        )


def _detach_user_from_organization(db, user_id):
    user = db.query(User).filter(User.id == user_id).one()
    user.organization_id = None
    db.commit()


# ---------------------------------------------------------------------------
# 1. NULL organization -> fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_user_with_null_organization_never_receives_global_analytics(client, db_session, route):
    owner_token, owner = _register(client, "org_owner")
    _seed_org_analytics(db_session, owner)

    # Positive control: the owning tenant really has data, so a leak would be visible.
    own = client.get(ROUTES[0], headers=_auth(owner_token))
    assert own.status_code == 200 and own.json()["total_runs"] >= 2

    detached_token, detached = _register(client, "detached")
    _detach_user_from_organization(db_session, detached["id"])

    r = client.get(route, headers=_auth(detached_token))

    assert r.status_code == 403
    assert r.json() == {"detail": "Organization context required"}
    # No aggregate field of any summary model may be present in the refusal.
    for field in ("total_runs", "total_evaluations", "total_discovery_runs", "success_rate_pct"):
        assert field not in r.text


@pytest.mark.parametrize("route", ROUTES)
def test_null_organization_is_refused_even_with_a_valid_hours_window(client, db_session, route):
    token, me = _register(client, "detached_hours")
    _detach_user_from_organization(db_session, me["id"])

    assert client.get(route, params={"hours": 24}, headers=_auth(token)).status_code == 403


@pytest.mark.parametrize("route", ROUTES)
def test_analytics_still_requires_authentication(client, route):
    assert client.get(route).status_code == 403  # HTTPBearer: missing header
    assert client.get(route, headers=_auth("not.a.real.jwt")).status_code == 401


# ---------------------------------------------------------------------------
# 2. `hours` bounds
# ---------------------------------------------------------------------------


@pytest.fixture()
def tenant(client):
    token, me = _register(client, "hours_tenant")
    return token, me


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "hours",
    [1, 24, 24 * 7, 24 * 30, MAX_ANALYTICS_HOURS],  # minimum, frontend windows, maximum
)
def test_hours_inside_the_bounds_is_accepted(client, tenant, route, hours):
    token, _me = tenant
    assert client.get(route, params={"hours": hours}, headers=_auth(token)).status_code == 200


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize(
    "hours",
    [
        0,
        -1,
        -(10**9),
        MAX_ANALYTICS_HOURS + 1,
        10**9,  # previously OverflowError -> HTTP 500
        10**30,  # far beyond any machine integer
        "abc",
        "1.5",
        "",
    ],
)
def test_hours_outside_the_bounds_is_a_validation_error_never_a_server_error(client, tenant, route, hours):
    token, _me = tenant
    r = client.get(route, params={"hours": hours}, headers=_auth(token))
    assert r.status_code == 422, (hours, r.status_code, r.text)


def test_maximum_is_one_year_and_the_frontend_windows_fit_inside_it():
    assert MAX_ANALYTICS_HOURS == 24 * 365
    assert 24 * 30 <= MAX_ANALYTICS_HOURS  # widest explicit frontend window (30d)


@pytest.mark.parametrize("route", ROUTES)
def test_omitting_hours_still_means_all_time(client, db_session, route):
    token, me = _register(client, "alltime")
    _seed_org_analytics(db_session, me, pipelines=3, evaluations=2, discoveries=2)

    r = client.get(route, headers=_auth(token))

    assert r.status_code == 200
    body = r.json()
    expected = {"pipeline": ("total_runs", 3), "evaluation": ("total_evaluations", 2), "discovery": ("total_discovery_runs", 2)}
    key, count = next(v for k, v in expected.items() if k in route)
    assert body[key] == count


# ---------------------------------------------------------------------------
# 3. Tenant isolation and unchanged behaviour for valid users
# ---------------------------------------------------------------------------


def test_tenant_a_never_sees_tenant_b_analytics(client, db_session):
    token_a, a = _register(client, "iso_a")
    token_b, b = _register(client, "iso_b")
    assert a["organization_id"] != b["organization_id"]
    _seed_org_analytics(db_session, b, pipelines=4, evaluations=3, discoveries=2)

    ra = client.get(ROUTES[0], headers=_auth(token_a)).json()
    assert ra["total_runs"] == 0

    rb = client.get(ROUTES[0], headers=_auth(token_b)).json()
    assert rb["total_runs"] == 4

    assert client.get(ROUTES[1], headers=_auth(token_a)).json()["total_evaluations"] == 0
    assert client.get(ROUTES[2], headers=_auth(token_a)).json()["total_discovery_runs"] == 0


def test_a_recent_window_includes_recent_rows_for_a_valid_user(client, db_session):
    token, me = _register(client, "window")
    _seed_org_analytics(db_session, me, pipelines=2)

    r = client.get(ROUTES[0], params={"hours": 24}, headers=_auth(token))

    assert r.status_code == 200
    assert r.json()["total_runs"] == 2
