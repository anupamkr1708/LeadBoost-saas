# Monitoring Guide

## Overview

The backend exposes a Prometheus-format `/metrics` endpoint
(`core/observability/prometheus_metrics.py`).

**`/metrics` is owner/operator-only.** Its payload is process-global
telemetry across *every* organization (all-time organization and lead
totals, 24-hour discovery/pipeline aggregates, request counters), not
tenant data, so it is not served to customers and is not anonymous. It
requires one dedicated bearer credential, `METRICS_BEARER_TOKEN`
(`core/infrastructure/auth/metrics_token.py`):

| Situation | Response |
|---|---|
| `METRICS_BEARER_TOKEN` unset, blank, shorter than 32 characters, has leading/trailing whitespace, or equals another configured secret (`SECRET_KEY`, `EMAIL_CREDENTIAL_ENCRYPTION_KEY`, a Mailer tenant key, `STRIPE_*`, `GROQ_API_KEY`, `SERPER_API_KEY`) | `403` — the endpoint is disabled; no metrics body |
| Token configured, `Authorization` header missing, not `Bearer`, empty, wrong, or a customer JWT | `401` with `WWW-Authenticate: Bearer` |
| Token configured and presented correctly | `200`, Prometheus text exposition |

* The check runs **before** the database session opens, so unauthenticated
  requests cannot cause database work.
* **If nothing scrapes the endpoint, leave `METRICS_BEARER_TOKEN` unset.**
  The route then answers `403` to everyone, which is the correct production
  state for a deployment without a monitoring consumer.
* The token is generated and provisioned by the operator only (never
  auto-generated at startup, never reused from `SECRET_KEY`, a customer JWT,
  a Mailer tenant key or the Mailer operator key). Keep it in the platform's
  secret store; never in source control, a URL, a frontend (`NEXT_PUBLIC_*`)
  variable, an API response or a log. If it is set but unusable, startup logs
  a warning naming the *reason* (never the value).
* `/health`, `/ready` and `/live` are unchanged and stay anonymous.
* Customer analytics (`/api/v2/analytics/*`) are a separate, tenant-scoped,
  JWT-authenticated path and are unaffected.
* Human viewing of the global dashboard is through Grafana's authenticated
  administrator account (below), not through LeadBoost's application login.
* The `location /metrics` allowlist in `deploy/nginx.conf` remains as an
  extra layer for the self-hosted nginx stack **only**. It does not apply on
  Render/Vercel and is not the security boundary.

**No customer content is ever included** — no emails, business names,
URLs, or prompt/LLM content, only counts, durations, and status codes.

## Two kinds of metric

1. **Live, per-request** (Counters/Histograms) — updated in-process as
   requests happen: `http_requests_total`, `http_request_duration_seconds`,
   `auth_attempts_total`.
2. **Refreshed at scrape time** (Gauges) — recomputed on every `/metrics`
   request by querying the *existing* `AnalyticsService`
   (`application/observability/metrics_service.py`) over a rolling 24-hour
   window, plus simple organization/lead counts. This deliberately reuses
   the same aggregation logic already powering the `/analytics/*`
   endpoints, rather than adding a second, parallel metrics-recording
   system that could quietly drift from it.

**Process-level metrics are not exported.** The app uses its own
`CollectorRegistry`, which does not include `prometheus_client`'s default
`process_*`/`python_*` collectors. (Verified against the registry output.)
The "Process memory (RSS)" and "Process CPU usage" panels in the provisioned
Grafana dashboard query `process_resident_memory_bytes` and
`process_cpu_seconds_total` and therefore show no data until a
`ProcessCollector` is registered — a known gap, unrelated to authentication.

## Metric reference

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `path`, `status_code` | Every HTTP request. `path` is the matched route *template* (e.g. `/leads/{lead_id}`); a request that matches no route is labelled `__unmatched__`, never its raw path. `method` is a standard HTTP method, or `OTHER` for any non-standard token. Label cardinality is therefore bounded by the number of routes, regardless of what anonymous clients request. |
| `http_request_duration_seconds` | Histogram | `method`, `path` | Request latency (same bounded labels). |
| `auth_attempts_total` | Counter | `result` (`success`/`failure`) | Login attempts. |
| `discovery_runs_total` | Gauge | — | Discovery searches in the last 24h. |
| `discovery_success_rate_pct` | Gauge | — | % of discovery runs that found at least one validated business. |
| `discovery_duration_seconds_avg` | Gauge | — | Average end-to-end discovery request duration. |
| `website_resolution_rate_pct` | Gauge | — | % of the fallback website-resolution attempts that succeeded. |
| `pipeline_runs_total` | Gauge | — | `LeadPipeline.execute()` runs in the last 24h. |
| `pipeline_success_rate_pct` | Gauge | — | % of pipeline runs with no stage errors. |
| `pipeline_duration_seconds_avg` | Gauge | — | Average pipeline duration. |
| `organizations_total` | Gauge | — | Total organizations (all-time). |
| `leads_total` | Gauge | — | Total leads (all-time), across every organization. |

### Not currently applicable

The brief's metric wishlist also mentions Redis cache hit/miss rate and
database connection-pool usage. Redis in this codebase is only used as a
legacy Celery-broker health-check target (see `docs/ENVIRONMENT.md`) —
there is no caching layer to instrument yet. If one is added later,
`prometheus_client` Counters for hits/misses drop in easily next to the
ones already here. DB pool metrics would need SQLAlchemy engine-level
pool-event hooks, which weren't added in this pass to avoid touching
`core/infrastructure/database`'s working connection-handling code
without a concrete need — both are called out as optional future
improvements.

## Running Prometheus + Grafana

Already wired into `docker-compose.prod.yml` (see `docs/DOCKER.md`):

```bash
docker compose -f docker-compose.prod.yml up -d prometheus grafana
```

Before the first start, the operator creates the scrape credential (the same
value as `METRICS_BEARER_TOKEN` in `.env`) as a git-ignored file and sets the
Grafana administrator password in `.env`:

```bash
# 1. METRICS_BEARER_TOKEN and GRAFANA_ADMIN_PASSWORD in .env (generate each separately):
#      python -c "import secrets; print(secrets.token_urlsafe(48))"
# 2. The Prometheus copy of the token:
install -d -m 700 monitoring/secrets
printf '%s' "<the METRICS_BEARER_TOKEN value>" > monitoring/secrets/metrics_bearer_token
chmod 644 monitoring/secrets/metrics_bearer_token   # container user must read it; the 700 directory excludes other host users
```

`docker compose` refuses to start Grafana without `GRAFANA_ADMIN_PASSWORD`
(there is no default password). `monitoring/secrets/` is git-ignored. If the
two token copies differ, the Prometheus target shows `DOWN` (HTTP 401); if
`METRICS_BEARER_TOKEN` is unset on the backend it shows `DOWN` (HTTP 403).
Rotate by changing both and restarting `backend` and `prometheus`.

- Prometheus: `http://localhost:9090` (**bound to the host's loopback only** —
  its UI has no authentication; use `ssh -L 9090:localhost:9090 <host>`),
  config at `monitoring/prometheus.yml` (scrapes `backend:8000/metrics` every
  15s with the bearer token read from `/run/secrets/metrics_bearer_token`).
- Grafana: `http://localhost:3001` (log in as `GRAFANA_ADMIN_USER` with the
  `GRAFANA_ADMIN_PASSWORD` you set; this authenticated administrator account is
  the owner's view of global telemetry) — the
  Prometheus datasource and the "LeadBoost - Overview" dashboard
  (`monitoring/grafana/dashboards/leadboost-overview.json`) are both
  auto-provisioned on first start via
  `monitoring/grafana/provisioning/`; no manual setup needed.

The dashboard covers: request rate and error rate by route, p95 latency,
auth attempts, discovery/pipeline run counts + success rates + average
durations, organization/lead totals, and (once a process collector is
registered — see above) process memory/CPU.
