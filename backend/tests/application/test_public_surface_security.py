"""
Regression tests for the public-surface security audit (Stage 2).

Everything here is synthetic and local: throwaway SQLite (see conftest.py), no real
mailbox, no network, no live sending. The hostnames/usernames used below are made up and
exist only to prove that they are NOT echoed back to a client.

Covers:
  1. The exact set of routes that answer anonymous callers (a guard: a new unauthenticated
     route, or a privileged route that lost its auth dependency, fails this test).
  2. /health and /ready must not return dependency exception text to anonymous callers
     (they are unauthenticated by design), while keeping their 200/503 deployment contract
     and still logging the details for operators.
  3. PUT /organizations/{id} must not let a tenant write its own entitlement attributes
     (plan_tier / max_users / max_leads), while the editable profile fields still work.
  4. Cross-tenant isolation for GET/PUT /organizations/{id} (previously untested).
  5. Token-type separation on a protected route (a refresh token is not an access token).
"""

import logging
import re
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from core.infrastructure.database import get_db


@pytest.fixture()
def client():
    # raise_server_exceptions=False: assert on what a client actually sees, not a re-raised error.
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_dependency_overrides():
    yield
    main.app.dependency_overrides.pop(get_db, None)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(client, label):
    email = f"{label}_{uuid.uuid4().hex[:8]}@example.com"
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Surface"},
    )
    assert r.status_code == 200, r.text
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    me = client.get("/api/v2/me", headers=_auth(body["access_token"])).json()
    return body["access_token"], body["refresh_token"], me["organization_id"]


# ---------------------------------------------------------------------------
# 1. Anonymous route surface
# ---------------------------------------------------------------------------

# The complete, intentional anonymous surface. Docs/OpenAPI are deliberately exposed (README);
# /metrics is unauthenticated by design and meant to be restricted at the network layer
# (docs/MONITORING.md); register/login/refresh are the public auth entry points; the three
# probes are public health checks. Adding to this set is a conscious, reviewable decision.
INTENTIONALLY_PUBLIC = {
    ("GET", "/openapi.json"),
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/redoc"),
    ("POST", "/api/v2/register"),
    ("POST", "/api/v2/login"),
    ("POST", "/api/v2/refresh"),
    ("GET", "/health"),
    ("GET", "/ready"),
    ("GET", "/live"),
    ("GET", "/metrics"),
}


def _all_routes():
    for route in main.app.routes:
        if not hasattr(route, "methods"):
            continue
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            yield method, route.path


def test_only_intentionally_public_routes_answer_anonymous_callers(client):
    answered_anonymously = set()
    for method, path in _all_routes():
        concrete = re.sub(r"\{[^}]+\}", "1", path)
        body = {} if method in ("POST", "PUT", "PATCH") else None
        r = client.request(method, concrete, json=body)
        if r.status_code not in (401, 403):
            answered_anonymously.add((method, path))

    assert answered_anonymously == INTENTIONALLY_PUBLIC


def test_garbage_bearer_token_is_rejected_on_a_protected_route(client):
    r = client.get("/api/v2/leads/", headers=_auth("not.a.real.jwt"))
    assert r.status_code == 401


def test_refresh_token_is_not_accepted_as_an_access_token(client):
    access, refresh, _org = _register_and_login(client, "tokentype")

    assert client.get("/api/v2/me", headers=_auth(access)).status_code == 200
    assert client.get("/api/v2/me", headers=_auth(refresh)).status_code == 401


# ---------------------------------------------------------------------------
# 2. /health and /ready must not leak dependency exception text
# ---------------------------------------------------------------------------

_LEAKY_DB_ERROR = (
    'connection to server at "db-internal.corp.example" (10.0.0.5), port 5432 failed: '
    'FATAL password authentication failed for user "leadboost_prod"'
)
_LEAKY_REDIS_ERROR = "Error 111 connecting to redis-internal.corp.example:6379. Connection refused."
_LEAK_MARKERS = (
    "db-internal.corp.example",
    "10.0.0.5",
    "leadboost_prod",
    "redis-internal.corp.example",
    "password authentication",
)


class _FailingSession:
    def execute(self, *args, **kwargs):
        raise RuntimeError(_LEAKY_DB_ERROR)


def _use_failing_database():
    def _failing_db():
        yield _FailingSession()

    main.app.dependency_overrides[get_db] = _failing_db


class _HealthyRedis:
    def ping(self):
        return True

    def close(self):
        return None


def _patch_redis(monkeypatch, healthy):
    import redis

    def _from_url(*args, **kwargs):
        if healthy:
            return _HealthyRedis()
        raise redis.exceptions.ConnectionError(_LEAKY_REDIS_ERROR)

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(_from_url))


def test_health_failure_returns_503_without_exception_details(client, monkeypatch):
    _use_failing_database()
    _patch_redis(monkeypatch, healthy=False)

    r = client.get("/health")

    assert r.status_code == 503  # the deployment contract is unchanged
    body = r.json()
    assert body["status"] == "unhealthy"
    assert body["checks"] == {"database": "unhealthy", "redis": "unhealthy"}
    for marker in _LEAK_MARKERS:
        assert marker not in r.text


def test_health_still_reports_healthy_when_dependencies_are_up(client, monkeypatch):
    _patch_redis(monkeypatch, healthy=True)

    r = client.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["checks"] == {"database": "healthy", "redis": "healthy"}


def test_ready_failure_returns_503_without_exception_details(client):
    _use_failing_database()

    r = client.get("/ready")

    assert r.status_code == 503
    assert r.json() == {"status": "not ready", "error": "database unavailable"}
    for marker in _LEAK_MARKERS:
        assert marker not in r.text


def test_ready_still_reports_ready_when_database_is_up(client):
    r = client.get("/ready")

    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_health_failure_details_are_still_logged_for_operators(client, monkeypatch, caplog):
    """Removing the details from the response must not remove them from the operator log."""
    _use_failing_database()
    _patch_redis(monkeypatch, healthy=False)

    with caplog.at_level(logging.ERROR):
        client.get("/health")

    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "Database health check failed" in logged
    assert "Redis health check failed" in logged


# ---------------------------------------------------------------------------
# 3. Entitlement attributes are not tenant-writable
# ---------------------------------------------------------------------------


def test_member_cannot_change_organization_entitlement_fields(client):
    token, _refresh, org_id = _register_and_login(client, "entitle")
    url = f"/api/v2/organizations/{org_id}"
    before = client.get(url, headers=_auth(token)).json()
    usage_before = client.get("/api/v2/usage", headers=_auth(token)).json()

    r = client.put(
        url,
        headers=_auth(token),
        json={"plan_tier": "enterprise", "max_users": 9999, "max_leads": 1_000_000_000},
    )

    # Compatible with clients that echo the whole organization back: accepted, but ignored.
    assert r.status_code == 200
    for field in ("plan_tier", "max_users", "max_leads"):
        assert r.json()[field] == before[field]

    after = client.get(url, headers=_auth(token)).json()  # not just the response: persisted state
    for field in ("plan_tier", "max_users", "max_leads"):
        assert after[field] == before[field]
    assert client.get("/api/v2/usage", headers=_auth(token)).json() == usage_before


def test_member_can_still_update_editable_organization_fields(client):
    token, _refresh, org_id = _register_and_login(client, "editable")
    url = f"/api/v2/organizations/{org_id}"

    r = client.put(
        url,
        headers=_auth(token),
        json={
            "description": "Updated description",
            "industry": "Logistics",
            "icp_description": "Mid-size freight forwarders",
            # Mixed request: the entitlement field is dropped, the legitimate ones still apply.
            "plan_tier": "enterprise",
        },
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "Updated description"
    assert body["industry"] == "Logistics"
    assert body["icp_description"] == "Mid-size freight forwarders"
    assert body["plan_tier"] == "free"


# ---------------------------------------------------------------------------
# 4. Cross-tenant isolation for /organizations/{org_id}
# ---------------------------------------------------------------------------


def test_organization_endpoints_reject_other_tenants_and_leave_data_unchanged(client):
    token_a, _ra, org_a = _register_and_login(client, "tenant_a")
    token_b, _rb, org_b = _register_and_login(client, "tenant_b")
    assert org_a != org_b
    url_a = f"/api/v2/organizations/{org_a}"
    original = client.get(url_a, headers=_auth(token_a)).json()

    # Reads are rejected ...
    r_get = client.get(url_a, headers=_auth(token_b))
    assert r_get.status_code == 403
    assert original["name"] not in r_get.text

    # ... and so are writes, including the entitlement fields from finding 3.
    r_put = client.put(
        url_a,
        headers=_auth(token_b),
        json={"description": "hijacked", "plan_tier": "enterprise", "max_leads": 1_000_000_000},
    )
    assert r_put.status_code == 403

    assert client.get(url_a, headers=_auth(token_a)).json() == original  # tenant A untouched

    # Positive control: tenant B still reaches its own organization.
    own = client.get(f"/api/v2/organizations/{org_b}", headers=_auth(token_b))
    assert own.status_code == 200
    assert own.json()["id"] == org_b
