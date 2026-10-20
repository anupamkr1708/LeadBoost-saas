"""
Tests for the Prometheus /metrics endpoint and core.observability.prometheus_metrics
(SECTION 7 of the production-polish brief).
"""

import re
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from core.observability import prometheus_metrics

# /metrics is owner/operator-only (see test_metrics_auth.py). These tests exercise the
# *content* of the exposition, so the client carries a per-run, test-only bearer token.
_METRICS_TOKEN = secrets.token_urlsafe(32)


class _AuthenticatedMetricsClient:
    """TestClient wrapper: every GET /metrics carries the test bearer token."""

    def __init__(self, inner):
        self._inner = inner

    def get(self, url, **kwargs):
        if url == "/metrics":
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("Authorization", f"Bearer {_METRICS_TOKEN}")
            kwargs["headers"] = headers
        return self._inner.get(url, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("METRICS_BEARER_TOKEN", _METRICS_TOKEN)
    with TestClient(main.app) as c:
        yield _AuthenticatedMetricsClient(c)


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
    assert '"/api/v2/leads/{lead_id}"' in r.text

# ---------------------------------------------------------------------------
# Bounded label cardinality (unknown routes and non-standard HTTP methods)
# ---------------------------------------------------------------------------

_SERIES = re.compile(r'^http_requests_total\{method="([^"]*)",path="([^"]*)",status_code="([^"]*)"\}', re.M)
_DURATION_SERIES = re.compile(r'^http_request_duration_seconds_count\{method="([^"]*)",path="([^"]*)"\}', re.M)


def _request_series(text):
    return set(_SERIES.findall(text))


def test_unmatched_paths_share_one_bounded_label(client):
    before = _request_series(client.get("/metrics").text)

    random_paths = [f"/no-such-route-{uuid.uuid4().hex}/{i}" for i in range(25)]
    for path in random_paths:
        assert client.get(path).status_code == 404

    body = client.get("/metrics").text
    after = _request_series(body)

    # None of the 25 distinct URLs became a label value ...
    for path in random_paths:
        assert path not in body
        assert path.split("/")[1] not in body
    # ... they all collapsed into the single constant label (one new series: GET/404).
    assert ("GET", "__unmatched__", "404") in after
    assert len(after - before) <= 1
    assert prometheus_metrics.UNMATCHED_ROUTE_LABEL == "__unmatched__"


def test_unmatched_paths_with_query_strings_and_ids_never_leak_into_labels(client):
    client.get("/leads/12345/secret?email=victim@example.com&token=abc")
    client.get("/%2e%2e/etc/passwd")
    client.get("/api/v2/leads/not-a-route/1/2/3")

    body = client.get("/metrics").text
    for fragment in ("victim", "12345", "etc/passwd", "token=abc", "not-a-route"):
        assert fragment not in body


def test_duration_histogram_uses_the_same_bounded_path_label(client):
    for _ in range(5):
        client.get(f"/missing-{uuid.uuid4().hex}")

    body = client.get("/metrics").text
    assert ("GET", "__unmatched__") in set(_DURATION_SERIES.findall(body))
    assert "missing-" not in body


def test_nonstandard_http_methods_share_one_bounded_label(client):
    tokens = [f"X{uuid.uuid4().hex[:8].upper()}" for _ in range(10)]
    for token in tokens:
        client.request(token, f"/no-such-route-{token.lower()}")

    body = client.get("/metrics").text
    for token in tokens:
        assert token not in body
    assert any(m == "OTHER" for m, _p, _s in _request_series(body))


def test_known_methods_and_route_templates_are_preserved(client):
    client.get("/live")
    client.post("/api/v2/login", data={"username": "nobody@example.com", "password": "x"})

    series = _request_series(client.get("/metrics").text)
    assert ("GET", "/live", "200") in series
    assert any(m == "POST" and p == "/api/v2/login" for m, p, _s in series)


def test_route_template_and_method_label_units():
    class _Route:
        path = "/leads/{lead_id}"

    class _Req:
        def __init__(self, scope, method="GET"):
            self.scope = scope
            self.method = method

    assert prometheus_metrics.route_template(_Req({"route": _Route()})) == "/leads/{lead_id}"
    assert prometheus_metrics.route_template(_Req({})) == "__unmatched__"
    assert prometheus_metrics.route_template(_Req({"route": object()})) == "__unmatched__"
    assert prometheus_metrics.route_template(_Req({"route": None})) == "__unmatched__"

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get"):
        assert prometheus_metrics.method_label(_Req({}, method)) == method.upper()
    for method in ("PROPFIND", "TRACE", "FOO", "", "G\u00c9T"):
        assert prometheus_metrics.method_label(_Req({}, method)) == "OTHER"
