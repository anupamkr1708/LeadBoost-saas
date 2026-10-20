"""
Tests for the owner/operator-only protection of the global Prometheus route
(core.infrastructure.auth.metrics_token + `GET /metrics` in main.py).

Synthetic and local only. Every token below is generated per test run
(secrets.token_urlsafe) -- nothing here is, or resembles, a real credential.

Proves the required behaviour table:
  token unset/blank/short/not-distinct   -> 403, no body, no DB session
  configured, header missing             -> 401 + WWW-Authenticate: Bearer
  wrong token / Basic / empty / non-ASCII / a customer JWT -> 401 (never 500)
  correct token                          -> 200 + Prometheus content type
and that a rejected request never opens a database session, never logs the token,
and leaves /health, /ready and /live untouched.
"""

import logging
import secrets
import uuid

import pytest
from fastapi.testclient import TestClient

import main
from core.infrastructure.auth.metrics_token import (
    MIN_METRICS_TOKEN_LENGTH,
    configured_metrics_token_problem,
)
from core.infrastructure.database import SessionLocal, get_db
from core.observability import prometheus_metrics

TOKEN = secrets.token_urlsafe(32)  # 43 chars, generated at test time
assert len(TOKEN) >= MIN_METRICS_TOKEN_LENGTH


@pytest.fixture()
def client():
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setenv("METRICS_BEARER_TOKEN", TOKEN)
    return TOKEN


@pytest.fixture()
def unconfigured(monkeypatch):
    monkeypatch.delenv("METRICS_BEARER_TOKEN", raising=False)


@pytest.fixture()
def db_sessions():
    """Counts how many database sessions the /metrics dependency chain opens."""
    opened = []

    def _counting_db():
        opened.append(1)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_db] = _counting_db
    yield opened
    main.app.dependency_overrides.pop(get_db, None)


def _bearer(value):
    return {"Authorization": f"Bearer {value}"}


def _assert_rejected_cleanly(response, status_code):
    assert response.status_code == status_code
    assert "http_requests_total" not in response.text  # no metrics body of any kind
    assert "text/plain" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Not configured -> fail closed (403)
# ---------------------------------------------------------------------------


def test_unset_token_disables_the_route(client, unconfigured, db_sessions):
    r = client.get("/metrics")
    _assert_rejected_cleanly(r, 403)
    assert "www-authenticate" not in r.headers
    assert db_sessions == []


def test_unset_token_never_lets_any_bearer_through(client, unconfigured, db_sessions):
    for candidate in (TOKEN, "x", "", "null", "undefined"):
        _assert_rejected_cleanly(client.get("/metrics", headers=_bearer(candidate)), 403)
    assert db_sessions == []


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_blank_token_is_treated_as_unset(client, monkeypatch, db_sessions, blank):
    monkeypatch.setenv("METRICS_BEARER_TOKEN", blank)
    _assert_rejected_cleanly(client.get("/metrics", headers=_bearer(blank or "x")), 403)
    assert db_sessions == []


def test_too_short_token_is_refused_even_when_presented_correctly(client, monkeypatch, db_sessions):
    short = "s" * (MIN_METRICS_TOKEN_LENGTH - 1)
    monkeypatch.setenv("METRICS_BEARER_TOKEN", short)
    _assert_rejected_cleanly(client.get("/metrics", headers=_bearer(short)), 403)
    assert db_sessions == []


def test_token_with_surrounding_whitespace_is_refused(client, monkeypatch, db_sessions):
    monkeypatch.setenv("METRICS_BEARER_TOKEN", TOKEN + "\n")  # classic pasted-secret mistake
    _assert_rejected_cleanly(client.get("/metrics", headers=_bearer(TOKEN)), 403)
    assert db_sessions == []


@pytest.mark.parametrize(
    "other_env",
    [
        "SECRET_KEY",
        "EMAIL_CREDENTIAL_ENCRYPTION_KEY",
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "GROQ_API_KEY",
        "SERPER_API_KEY",
    ],
)
def test_token_equal_to_another_credential_is_refused(client, monkeypatch, db_sessions, other_env):
    shared = secrets.token_urlsafe(32)
    monkeypatch.setenv(other_env, shared)
    monkeypatch.setenv("METRICS_BEARER_TOKEN", shared)
    _assert_rejected_cleanly(client.get("/metrics", headers=_bearer(shared)), 403)
    assert db_sessions == []


def test_token_equal_to_a_mailer_tenant_key_is_refused(client, monkeypatch, db_sessions):
    import json

    tenant_key = secrets.token_urlsafe(32)
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"7": tenant_key}))
    monkeypatch.setenv("METRICS_BEARER_TOKEN", tenant_key)
    _assert_rejected_cleanly(client.get("/metrics", headers=_bearer(tenant_key)), 403)
    assert db_sessions == []


def test_a_distinct_token_is_accepted_alongside_other_configured_secrets(client, monkeypatch):
    import json

    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"7": secrets.token_urlsafe(32)}))
    monkeypatch.setenv("METRICS_BEARER_TOKEN", TOKEN)
    assert client.get("/metrics", headers=_bearer(TOKEN)).status_code == 200


# ---------------------------------------------------------------------------
# Configured -> authenticate (401)
# ---------------------------------------------------------------------------


def test_missing_authorization_header_is_401_with_challenge(client, configured, db_sessions):
    r = client.get("/metrics")
    _assert_rejected_cleanly(r, 401)
    assert r.headers["www-authenticate"] == "Bearer"
    assert db_sessions == []


def test_wrong_token_is_401(client, configured, db_sessions):
    r = client.get("/metrics", headers=_bearer(secrets.token_urlsafe(32)))
    _assert_rejected_cleanly(r, 401)
    assert r.headers["www-authenticate"] == "Bearer"
    assert db_sessions == []


def test_token_that_is_a_prefix_or_extension_of_the_real_one_is_401(client, configured):
    assert client.get("/metrics", headers=_bearer(TOKEN[:-1])).status_code == 401
    assert client.get("/metrics", headers=_bearer(TOKEN + "x")).status_code == 401


def test_basic_scheme_with_the_correct_secret_is_401(client, configured, db_sessions):
    r = client.get("/metrics", headers={"Authorization": f"Basic {TOKEN}"})
    _assert_rejected_cleanly(r, 401)
    assert db_sessions == []


def test_raw_token_without_a_scheme_is_401(client, configured):
    assert client.get("/metrics", headers={"Authorization": TOKEN}).status_code == 401


@pytest.mark.parametrize("header", ["Bearer", "Bearer ", "Bearer    ", "bearer"])
def test_empty_bearer_credential_is_401(client, configured, db_sessions, header):
    r = client.get("/metrics", headers={"Authorization": header})
    _assert_rejected_cleanly(r, 401)
    assert db_sessions == []


@pytest.mark.parametrize(
    "raw",
    [
        "pässwörd-ünïcode-❌".encode("utf-8"),
        b"\xff\xfe\xfd",  # not valid UTF-8 at all
        b"\xe9clair",  # a lone latin-1 byte (e-acute), invalid as UTF-8
    ],
)
def test_non_ascii_credential_is_401_never_500(client, configured, db_sessions, raw):
    r = client.get("/metrics", headers={"Authorization": b"Bearer " + raw})
    _assert_rejected_cleanly(r, 401)
    assert db_sessions == []


def test_customer_jwt_is_not_accepted_as_the_metrics_token(client, configured, db_sessions):
    email = f"metrics_jwt_{uuid.uuid4().hex[:8]}@example.com"
    assert client.post(
        "/api/v2/register", json={"email": email, "password": "TestPass123!", "first_name": "J"}
    ).status_code == 200
    jwt_token = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"}).json()[
        "access_token"
    ]
    assert client.get("/api/v2/me", headers=_bearer(jwt_token)).status_code == 200  # it IS a valid JWT
    db_sessions.clear()  # count only what the /metrics request itself opens

    r = client.get("/metrics", headers=_bearer(jwt_token))

    _assert_rejected_cleanly(r, 401)
    assert db_sessions == []


# ---------------------------------------------------------------------------
# Correct token -> 200
# ---------------------------------------------------------------------------


def test_correct_token_returns_prometheus_exposition(client, configured, db_sessions):
    r = client.get("/metrics", headers=_bearer(TOKEN))

    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert r.headers["content-type"] == prometheus_metrics.CONTENT_TYPE_LATEST
    assert "# TYPE http_requests_total counter" in r.text
    assert TOKEN not in r.text
    assert TOKEN not in str(dict(r.headers))
    assert len(db_sessions) == 1  # exactly the one the handler needs, opened only after auth


def test_scheme_is_case_insensitive(client, configured):
    assert client.get("/metrics", headers={"Authorization": f"bearer {TOKEN}"}).status_code == 200
    assert client.get("/metrics", headers={"Authorization": f"BEARER {TOKEN}"}).status_code == 200


def test_global_gauges_the_dashboard_depends_on_are_still_exported(client, configured):
    body = client.get("/metrics", headers=_bearer(TOKEN)).text
    for gauge in (
        "organizations_total",
        "leads_total",
        "discovery_runs_total",
        "discovery_success_rate_pct",
        "discovery_duration_seconds_avg",
        "website_resolution_rate_pct",
        "pipeline_runs_total",
        "pipeline_success_rate_pct",
        "pipeline_duration_seconds_avg",
    ):
        assert f"# TYPE {gauge} gauge" in body, gauge


def test_token_in_the_query_string_is_not_accepted(client, configured):
    assert client.get("/metrics", params={"token": TOKEN}).status_code == 401
    assert client.get("/metrics", params={"access_token": TOKEN, "bearer_token": TOKEN}).status_code == 401


# ---------------------------------------------------------------------------
# Never logged; probes unchanged; startup visibility
# ---------------------------------------------------------------------------


def test_token_is_never_written_to_logs(client, configured, caplog):
    wrong = secrets.token_urlsafe(32)
    with caplog.at_level(logging.DEBUG):
        client.get("/metrics")
        client.get("/metrics", headers=_bearer(wrong))
        client.get("/metrics", headers=_bearer(TOKEN))
    assert TOKEN not in caplog.text
    assert wrong not in caplog.text


class _HealthyRedis:
    def ping(self):
        return True

    def close(self):
        return None


def test_health_ready_live_contracts_are_unchanged(client, configured, monkeypatch):
    import redis

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: _HealthyRedis()))

    assert client.get("/live").json() == {"status": "alive"}
    assert client.get("/ready").json() == {"status": "ready"}
    health = client.get("/health")
    assert health.status_code == 200 and health.json()["status"] == "healthy"


def test_health_probes_stay_anonymous_when_metrics_is_disabled(client, unconfigured):
    assert client.get("/live").status_code == 200
    assert client.get("/ready").status_code == 200


def test_startup_warns_about_an_unusable_token_without_revealing_it(monkeypatch, caplog):
    from core.config import validate_startup_environment

    short = "short-secret-" + secrets.token_hex(4)
    monkeypatch.setenv("METRICS_BEARER_TOKEN", short)
    assert configured_metrics_token_problem() == f"is shorter than {MIN_METRICS_TOKEN_LENGTH} characters"

    with caplog.at_level(logging.WARNING):
        validate_startup_environment()  # must warn, never raise

    assert "METRICS_BEARER_TOKEN is set but unusable" in caplog.text
    assert short not in caplog.text


def test_startup_is_silent_when_the_token_is_unset_or_valid(monkeypatch, caplog):
    from core.config import validate_startup_environment

    monkeypatch.delenv("METRICS_BEARER_TOKEN", raising=False)
    with caplog.at_level(logging.WARNING):
        validate_startup_environment()
    assert "METRICS_BEARER_TOKEN" not in caplog.text

    monkeypatch.setenv("METRICS_BEARER_TOKEN", TOKEN)
    assert configured_metrics_token_problem() is None
    with caplog.at_level(logging.WARNING):
        validate_startup_environment()
    assert "METRICS_BEARER_TOKEN" not in caplog.text
