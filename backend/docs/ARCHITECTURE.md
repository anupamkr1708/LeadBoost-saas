# LeadBoost Application Layer — Observability & Production Polish

This document covers the production-readiness layer added on top of the
existing Application Layer (agents, LangGraph workflow, prompt registry,
evaluation, explainability, memory, human review). It does **not** describe
a new architecture — it describes how every existing pipeline execution is
now measured, logged, and made queryable.

No existing agent, prompt, workflow edge, or evaluation formula was changed.
Everything below is additive instrumentation around the existing system.

---

## 1. Pipeline Execution Lifecycle

Every call to `LeadPipeline.execute(lead_id)` (see
`application/workflows/lead_pipeline.py`) now follows this lifecycle:

```
1. Generate pipeline_id = uuid4()
2. Record started_at (UTC) and a monotonic perf-counter start
3. Look up the lead
     -> not found: return PipelineResult(status=FAILED), no DB row written
4. Build initial LeadState, including pipeline_id
5. Run the LangGraph workflow (ainvoke) -- unchanged graph, unchanged nodes
     -> unhandled exception: status=FAILED, PipelineExecutionRecord written
6. Compute duration_ms and final_status from state["errors"]:
     - no errors            -> SUCCESS
     - one or more stage
       errors, run completed -> PARTIAL_SUCCESS
7. Persist a PipelineExecutionRecord (pipeline_id, lead_id, organization_id,
   started_at, completed_at, duration_ms, final_status, stage_count,
   error_count)
8. Return a PipelineResult carrying the same fields plus every stage's
   individual output (decision, evaluation, review, message, etc.)
```

### Status semantics

| Status            | Meaning                                                                 |
|--------------------|--------------------------------------------------------------------------|
| `SUCCESS`          | The graph ran start to finish with zero stage errors.                   |
| `PARTIAL_SUCCESS`  | The graph ran start to finish, but one or more stages degraded gracefully (e.g. the scraper failed, an LLM call failed) and fell back to a deterministic path. |
| `FAILED`           | The lead did not exist, or the graph runtime itself raised an unhandled exception (a safety net — every node already catches its own exceptions). |

A stage that returns a *low-confidence* or *empty* result is **not** an
error by itself (e.g. a 404 scrape is a normal, successful stage
execution with `success=False` business data). Only an actual exception
inside a stage counts toward `error_count` / `PARTIAL_SUCCESS`. This keeps
the status meaningful: it answers "did the platform work correctly today,"
not "did every lead look impressive."

### Where it's written

New table: `pipeline_execution_logs` — see
`application/observability/models.py::PipelineExecutionRecord`.

Metrics-write failures are caught and logged but never propagate — a
database hiccup while recording metrics must not fail a lead's actual
processing.

---

## 2. Evaluation Lifecycle

The existing, unmodified Confidence Evaluation stage
(`application/evaluation/evaluators.py::build_evaluation_report`) still
computes `confidence`, `completeness`, `grounding`, `consistency`, and
`overall` exactly as before.

What's new: immediately after the report is built, the `confidence_evaluation`
node (`application/workflows/graph_nodes.py`) additionally persists it to
`evaluation_report_logs` via
`application/observability/repository.py::create_evaluation_report_record`,
alongside:

- `pipeline_id` — correlates the report back to its full pipeline run and
  structured log trail.
- `lead_id` / `organization_id` — for per-org analytics.
- `prompt_version` — the version of the Decision Agent's prompt whose
  output was evaluated (`None` if the Decision Agent used its rule-based
  fallback, since there was no prompt to version).

This is purely additive persistence; it does not change what is evaluated
or how the score is computed, and does not change the existing
`AIDecisionLog`-based business-memory write the stage already performed.

---

## 3. Prompt Version Tracking

`CompanyIntelligenceOutput`, `DecisionOutput`, and `MessagingOutput`
(`application/dto/models.py`) each gained three optional fields:

```python
prompt_name: Optional[str] = None      # e.g. "decision"
prompt_version: Optional[str] = None   # e.g. "v1" (resolved from the
                                        # Prompt Registry at call time)
retry_count: int = 0                   # LLM retries this call needed
```

These are populated only on the LLM path of each agent (`source == "llm"`).
The rule-based / heuristic / template fallback paths leave them `None` —
there is no prompt to version-track when no prompt was used.

Whenever a node sees `output.prompt_name` set, it writes one row to
`prompt_execution_logs` via
`create_prompt_execution_record(pipeline_id, lead_id, organization_id,
agent_name, prompt_name, prompt_version, retry_count)`.

This gives a complete, queryable history of which prompt version produced
which pipeline's output — the prerequisite for future prompt A/B
comparison — without introducing a PromptOps platform.

### How retry_count is captured

`application/services/llm_provider.py::safe_invoke_json` now returns
`(payload, retry_count)`. Internally it uses a fresh `tenacity.Retrying`
instance per call (not the `@retry` decorator), so retry statistics are
isolated per invocation — safe under concurrent pipeline runs. The agent
attaches `retry_count` to its output DTO; the node reads it back and both
(a) writes it to `PromptExecutionRecord.retry_count` and (b) sets
`StageTimer.retry_count` so the stage's own log line reports it too.
(A `contextvars`-based approach was considered and rejected: values set
inside `asyncio.to_thread`-executed code run in a *copied* context and do
not propagate back to the caller, so explicit return values are used
instead.)

---

## 4. Workflow (Stage) Logging

`application/utils/stage_logger.py::stage_span` wraps every LangGraph node
(scrape, enrich, analyze_company, qualification, decide,
confidence_evaluation, review_decision, message_generation, persistence,
analytics). Every stage now automatically logs two structured JSON lines
(start and completion/failure) via the existing
`core.infrastructure.logging` JSON logger — no new logging system.

Start line:
```json
{
  "event": "stage_start",
  "stage": "scrape",
  "lead_id": 42,
  "pipeline_id": "2339b4c1-3f5f-44e8-af70-92363e479d79",
  "started_at": "2026-07-20T03:03:18.214254+00:00"
}
```

Completion line:
```json
{
  "event": "stage_complete",
  "stage": "scrape",
  "lead_id": 42,
  "pipeline_id": "2339b4c1-3f5f-44e8-af70-92363e479d79",
  "started_at": "2026-07-20T03:03:18.214254+00:00",
  "completed_at": "2026-07-20T03:03:18.660623+00:00",
  "duration_ms": 446,
  "success": true,
  "retry_count": 0
}
```

Failure line (`event: "stage_failed"`) carries the same fields plus
`"success": false` and the exception is logged with a full traceback
(`exc_info=True`), then re-raised to `graph_nodes._run_stage`, which
catches it, appends a structured entry to `state["errors"]`, and lets the
pipeline continue to the next stage.

No external observability platform (Datadog, OpenTelemetry, etc.) was
introduced — this is the same `python-json-logger`-based structured
logging the codebase already used, just applied consistently with a
richer, uniform field set.

---

## 5. Metrics Collected & How They're Calculated

`application/observability/metrics_service.py::AnalyticsService` computes
everything in plain Python (`statistics` from the stdlib) over rows
queried from the three new tables — no caching layer, no database-specific
SQL (works identically on SQLite and Postgres), no new frameworks.

### Pipeline Success Rate

```
success_rate_pct = (count of runs with final_status == SUCCESS)
                    / (total runs) * 100
```

`PARTIAL_SUCCESS` and `FAILED` are **not** folded into the numerator. They
are reported separately (`partial_success_count`, `failed_count`) so the
success rate specifically answers "what fraction of runs had zero stage
errors," rather than being diluted by a looser definition of "success."

### Average / Median / P95 Processing Time

Computed over `duration_ms` from every matching `PipelineExecutionRecord`:

- **Average** — `statistics.mean(durations)`
- **Median** — `statistics.median(durations)`
- **P95** — `statistics.quantiles(durations, n=100, method="inclusive")[94]`
  (nearest-rank 95th percentile; falls back to the single value when there
  is only one data point, and to `0.0` when there are none)

### Evaluation metrics

`average_overall_score` and the four component averages
(`average_confidence`, `average_completeness`, `average_grounding`,
`average_consistency`) are simple means over `EvaluationReportRecord` rows.

### Scoping and windowing

Both `AnalyticsService` methods accept `organization_id` (always applied
by the API layer from the authenticated user) and an optional `since`
timestamp. The Analytics API's `?hours=N` query parameter is a thin
convenience over `since`.

---

## 6. Analytics API

`api/endpoints/analytics.py`, registered exactly like every other router
in `main.py` (`/api/v2` prefix, same `get_current_user` / `get_db`
dependencies as `organizations.py` / `billing.py`). Both endpoints are
read-only (`GET`) and scoped to the caller's organization.

| Endpoint                             | Returns                                                        |
|----------------------------------------|------------------------------------------------------------------|
| `GET /api/v2/analytics/pipeline-metrics?hours=` | `PipelineMetricsSummary`: total runs, success/partial/failed counts, success rate, avg/median/P95 processing time |
| `GET /api/v2/analytics/evaluation-metrics?hours=` | `EvaluationMetricsSummary`: total evaluations, average overall/confidence/completeness/grounding/consistency |

---

## 7. Updated Workflow Diagram

```
                              Lead Created
                                    |
                                    v
   [pipeline_id assigned, started_at recorded]
                                    |
                                    v
   Scraper --> Enrichment --> Company Intelligence --> Lead Qualification
     |             |                    |                       |
     |             |                    +-- PromptExecutionRecord
     |             |                        (if LLM path used)
     v             v
  [stage_span logs: start/complete, duration_ms, retry_count]  (every stage)

   Decision Engine --> Confidence Evaluation --> Review Decision
        |                       |                       |
        +-- PromptExecutionRecord   +-- EvaluationReportRecord
            (if LLM path used)          (always, when evaluation ran)
                                                        |
                                     +------------------+------------------+
                                     |                                     |
                              (not human_review)                   (human_review)
                                     v                                     |
                          Message Generation                              |
                                     |                                     |
                                     +-- PromptExecutionRecord             |
                                         (if LLM path used)                |
                                     +------------------+------------------+
                                                        v
                                                  Persistence --> Analytics
                                                        |
                                                        v
                                    [final_status computed: SUCCESS /
                                     PARTIAL_SUCCESS / FAILED]
                                                        |
                                                        v
                                    PipelineExecutionRecord persisted
                                    (pipeline_id, started_at, completed_at,
                                     duration_ms, final_status)
```

---

## 8. What Was Deliberately Not Done

Per the engineering constraints for this change:

- No new AI agents were introduced. Metrics/logging are pure instrumentation.
- No new frameworks. `statistics` (stdlib) and `tenacity` (already a
  dependency) are the only tools used for aggregation/retries.
- No caching layer — `AnalyticsService` computes on demand.
- No distributed tracing / external observability platform.
- No repository-pattern abstraction beyond the flat functions already used
  by `core/infrastructure/database/crud.py` — `application/observability/repository.py`
  follows the exact same style.
- Core (`core/domain`, `core/infrastructure`) was not modified at all in
  this change. All new tables live in `application/observability/models.py`
  and share Core's existing `Base`/engine — they are additive rows in the
  same database, not a new datastore.
