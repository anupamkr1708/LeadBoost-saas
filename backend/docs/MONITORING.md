# Monitoring Guide

## Overview

The backend exposes a Prometheus-format `/metrics` endpoint
(`core/observability/prometheus_metrics.py`). No authentication is
required on it, matching how `/health`/`/ready`/`/live` are already
exposed — protect it at the network level (private network, VPC, or an
IP allowlist at a reverse proxy — see the `location /metrics` block in
`deploy/nginx.conf`) rather than with application credentials, so
Prometheus itself doesn't need to authenticate.

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

Process-level metrics (`process_resident_memory_bytes`,
`process_cpu_seconds_total`, etc.) come from `prometheus_client`'s
default collectors — no extra code needed for those.

## Metric reference

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_requests_total` | Counter | `method`, `path`, `status_code` | Every HTTP request. `path` is the matched route *template* (e.g. `/leads/{lead_id}`), never the raw path — see `route_template()`'s docstring for why that matters for cardinality. |
| `http_request_duration_seconds` | Histogram | `method`, `path` | Request latency. |
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
| `process_resident_memory_bytes`, `process_cpu_seconds_total`, etc. | Gauge/Counter | — | Standard `prometheus_client` process collectors. |

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

- Prometheus: `http://localhost:9090`, config at `monitoring/prometheus.yml`
  (scrapes `backend:8000/metrics` every 15s).
- Grafana: `http://localhost:3001` (default login `admin`/`admin`, or
  whatever `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD` you set) — the
  Prometheus datasource and the "LeadBoost - Overview" dashboard
  (`monitoring/grafana/dashboards/leadboost-overview.json`) are both
  auto-provisioned on first start via
  `monitoring/grafana/provisioning/`; no manual setup needed.

The dashboard covers: request rate and error rate by route, p95 latency,
auth attempts, discovery/pipeline run counts + success rates + average
durations, organization/lead totals, and process memory/CPU.
