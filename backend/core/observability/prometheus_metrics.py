"""
Prometheus metrics (SECTION 7 of the production-polish brief).

Two kinds of metric here, deliberately kept separate:

1. Live counters/histograms (HTTP requests, auth attempts) -- updated
   in-process as requests happen, via a couple of lines added to the
   existing `add_request_id_and_timing` middleware and the existing
   login endpoint. These need per-request granularity a periodic DB
   query can't give you.

2. Gauges refreshed at scrape time (discovery/pipeline success rate,
   durations, organization/lead counts) -- computed by calling the
   existing, already-tested `application.observability.metrics_service
   .AnalyticsService` and simple counts, NOT by re-instrumenting
   discovery_service.py or the LangGraph pipeline with a second,
   parallel metrics-recording system. That service already aggregates
   exactly these numbers from the `application.observability` tables for
   the existing /analytics/* endpoints; reusing it here means there is
   only ever one code path computing "discovery success rate", not two
   that could quietly drift apart.

No customer content is ever recorded as a metric or label (no emails,
no prompts, no business names/URLs) -- only counts, durations, and status
codes, per the brief's explicit privacy requirement. Route labels use the
matched route *template* (e.g. "/leads/{lead_id}"), never the raw path,
to avoid unbounded label cardinality from path parameters.
"""

from datetime import datetime, timedelta
from typing import Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy.orm import Session

REGISTRY = CollectorRegistry(auto_describe=True)

# -- Live, request-time metrics ----------------------------------------------

http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    registry=REGISTRY,
)

auth_attempts_total = Counter(
    "auth_attempts_total",
    "Authentication attempts",
    ["result"],  # "success" | "failure"
    registry=REGISTRY,
)

# -- Scrape-time gauges (refreshed from AnalyticsService on each /metrics ----
# -- request -- see refresh_periodic_gauges below) ---------------------------

discovery_runs_total = Gauge(
    "discovery_runs_total", "Discovery runs in the lookback window", registry=REGISTRY
)
discovery_success_rate_pct = Gauge(
    "discovery_success_rate_pct", "Discovery success rate (%) in the lookback window", registry=REGISTRY
)
discovery_duration_seconds_avg = Gauge(
    "discovery_duration_seconds_avg", "Average discovery duration in seconds", registry=REGISTRY
)
website_resolution_rate_pct = Gauge(
    "website_resolution_rate_pct", "Website fallback resolution success rate (%)", registry=REGISTRY
)

pipeline_runs_total = Gauge(
    "pipeline_runs_total", "Lead pipeline runs in the lookback window", registry=REGISTRY
)
pipeline_success_rate_pct = Gauge(
    "pipeline_success_rate_pct", "Lead pipeline success rate (%) in the lookback window", registry=REGISTRY
)
pipeline_duration_seconds_avg = Gauge(
    "pipeline_duration_seconds_avg", "Average lead pipeline duration in seconds", registry=REGISTRY
)

organizations_total = Gauge("organizations_total", "Total organizations", registry=REGISTRY)
leads_total = Gauge("leads_total", "Total leads across all organizations", registry=REGISTRY)


def route_template(request) -> str:
    """The matched route's path *template* (e.g. "/leads/{lead_id}"),
    falling back to the raw path only if routing hasn't resolved (e.g. a
    404 for a path with no matching route at all) -- keeps the `path`
    label's cardinality bounded to the number of routes, not the number
    of distinct IDs ever requested."""
    route = request.scope.get("route")
    if route is not None and hasattr(route, "path"):
        return route.path
    return request.url.path


def refresh_periodic_gauges(db: Session, lookback_hours: int = 24) -> None:
    """Recomputes the scrape-time gauges. Called once per /metrics
    request -- cheap enough (a handful of bounded SQL queries over a
    24-hour window) to run on every scrape rather than needing its own
    background scheduler."""
    from application.observability.metrics_service import AnalyticsService
    from core.domain.models.lead import Lead
    from core.domain.models.organization import Organization

    since = datetime.utcnow() - timedelta(hours=lookback_hours)
    analytics = AnalyticsService(db)

    discovery = analytics.get_discovery_metrics(since=since)
    discovery_runs_total.set(discovery.total_discovery_runs)
    discovery_success_rate_pct.set(discovery.discovery_success_rate_pct)
    discovery_duration_seconds_avg.set((discovery.avg_discovery_time_ms or 0) / 1000)
    website_resolution_rate_pct.set(discovery.website_resolution_rate_pct)

    pipeline = analytics.get_pipeline_metrics(since=since)
    pipeline_runs_total.set(pipeline.total_runs)
    pipeline_success_rate_pct.set(pipeline.success_rate_pct)
    pipeline_duration_seconds_avg.set((pipeline.avg_processing_time_ms or 0) / 1000)

    organizations_total.set(db.query(Organization).count())
    leads_total.set(db.query(Lead).count())


def render_latest(db: Optional[Session] = None) -> tuple:
    """Returns (body_bytes, content_type) for the /metrics endpoint. Pass
    a db session to also refresh the scrape-time gauges; omit it (e.g. in
    a unit test) to render only the live counters/histograms."""
    if db is not None:
        try:
            refresh_periodic_gauges(db)
        except Exception:
            # A gauge-refresh failure must never take down the whole
            # /metrics endpoint -- live HTTP metrics are still valuable
            # on their own, and a scrape target flapping unhealthy would
            # be worse than one stale panel.
            pass
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
