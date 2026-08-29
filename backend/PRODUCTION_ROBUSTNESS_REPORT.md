# LeadBoost SaaS Backend — Production Robustness Hardening
## Final Report: Phase A (Retry/Idempotency Safety) + Phase B (Resource/Concurrency Safety)

---

## 1. Executive Summary

The backend was audited end-to-end for the two scoped concerns — correctness
under retries/duplicate submissions (Phase A) and resource/concurrency
protection (Phase B) — by reading the actual implementation, not by
assuming gaps existed. Four **confirmed gaps** were found and fixed with
the smallest compatible mechanism in each case; everything else audited
was found already adequate and was left untouched.

| # | Gap | Area | Fix |
|---|-----|------|-----|
| 1 | `POST /leads/{id}/process` has no protection against concurrent/duplicate runs | Phase A3/A4 | New DB-backed per-lead pipeline lock |
| 2 | No DB-level uniqueness for `(organization_id, website)` — concurrent identical submissions can create two Lead rows | Phase A5 | `UniqueConstraint` + graceful `IntegrityError` handling at every creation call site |
| 3 | Daily lead quota computed via `COUNT(*)`, a classic check-then-act race | Phase A5 | Atomic per-org reservation counter (single conditional `UPDATE`) |
| 4 | Three independent, uncoordinated Groq call sites share one TPM budget with no concurrency bound | Phase B4 | One shared, process-wide semaphore applied at all three sites |

**Result:** 687 pre-existing tests still pass unchanged, plus 21 new
regression tests (including real multi-threaded concurrency tests against
the actual database) — **708/708 passing**. No API contract, response
shape, database semantics (beyond one additive constraint), or AI/
scraping/messaging behavior was altered outside the four fixes above.

---

## 2. Existing Protection Discovered (left unchanged)

| Area | What's already there | Verdict |
|---|---|---|
| Lead-creation dedup | `get_lead_by_url(org_id, url)` checked before creating in all 3 creation call sites | ALREADY IMPLEMENTED |
| Browser/Playwright concurrency (B5) | Genuinely global `_BrowserPool` singleton + `asyncio.Semaphore(SCRAPER_MAX_CONCURRENT_PAGES)` | ALREADY IMPLEMENTED — no gap |
| Discovery/scraper internal concurrency | `DISCOVERY_MAX_CONCURRENT_PIPELINES`, `DISCOVERY_MAX_CONCURRENT_RESOLUTIONS`, `SCRAPER_ENRICHMENT_CONCURRENCY` | ALREADY IMPLEMENTED |
| LLM retry/backoff | `tenacity`-based retry (2 attempts, exponential backoff+jitter) around every LLM call | ALREADY IMPLEMENTED — sufficient; not modified |
| Discovery provider fallback | Overpass → Serper fallback; provider-level try/except throughout `application/discovery/providers/` | ALREADY ADEQUATE |
| Pagination (B8) | `GET /leads` already has `skip`/`limit`; other list endpoints (`analytics/*`, `organizations/{id}`) return bounded/single-object payloads, not open-ended collections | ALREADY ADEQUATE |
| Discovery-request retry safety | `_create_leads_and_run_pipelines` already checks `get_lead_by_url` per business before creating/re-running a pipeline, so a retried `/discovery/search` naturally skips already-created leads | ALREADY IMPLEMENTED (see §13 for the one related idea NOT implemented) |

---

## 3. Phase A — Idempotency / Retry Safety

### A3/A4 — Lead processing lock (CONFIRMED GAP → FIXED)

- **Problem:** `POST /leads/{id}/process` re-ran the entire pipeline
  (scrape → enrich → company intelligence → decision → messaging) on
  every call with zero protection. A retried request, a double-click, or
  a genuinely concurrent second request for the same lead each launched
  a full, independent, expensive run — duplicate scrapes, duplicate LLM
  calls, duplicate `ScrapingLog`/`LeadEnrichmentLog`/`AIDecisionLog` rows.
- **Evidence:** The `Lead` model has no processing-status column at all;
  `PipelineExecutionRecord` (`application/observability/models.py`) is
  only ever written *after* a run completes, so there was no existing
  signal of "a pipeline is running right now" to check against.
- **Root cause:** `application/workflows/lead_pipeline.py::LeadPipeline.execute()`
  had no mutual-exclusion of any kind.
- **File / function:** `core/domain/models/pipeline_lock.py` (new model),
  `application/concurrency/pipeline_lock.py` (new acquire/release
  helpers), `application/workflows/lead_pipeline.py::LeadPipeline.execute()`
  (integration).
- **Minimal fix:** A new `active_pipeline_locks` table with a real
  database `UNIQUE(lead_id)` constraint — durable across worker
  processes, unlike an in-memory dict, and portable across SQLite and
  PostgreSQL. A row is inserted immediately before the graph runs and
  deleted in a `finally` block regardless of outcome. A second concurrent
  call gets `IntegrityError` → returns immediately with a new
  `PipelineStatus.SKIPPED_IN_PROGRESS` result (same response shape,
  `message`/`lead_id`/`result` keys unchanged) referencing the in-flight
  `pipeline_id`, instead of running the graph again. A stale lock (owning
  process crashed) is auto-reclaimed after `PIPELINE_LOCK_STALE_SECONDS`
  (default 1800s) so a hard crash can never permanently strand a lead.
  Crucially, this **never blocks a later, non-overlapping** reprocess
  request — the existing "manually trigger processing" feature is fully
  preserved.
- **Tests:** `tests/application/test_production_robustness.py::TestPipelineLock`
  (7 tests) + `TestPipelineLockIntegration` (4 tests, including a true
  `asyncio`-concurrent test against two independent DB sessions proving
  exactly one of two overlapping runs actually executes).
- **Result:** No duplicate pipeline execution possible under concurrency;
  zero change to the single-request happy path.

### A5 — Lead-creation race (CONFIRMED GAP → FIXED)

- **Problem:** All three lead-creation call sites did check-then-insert
  (`get_lead_by_url` then `create_lead`) with no atomicity between the
  two steps. Two concurrent identical submissions (duplicate click,
  client retry racing the original request) could both pass the check
  and both insert, producing two Lead rows for the same site and two
  redundant full pipeline runs.
- **File / function:** `core/domain/models/lead.py::Lead`;
  `api/endpoints/leads.py::create_leads_from_urls`,
  `create_lead_endpoint`; `application/discovery/discovery_service.py::_create_leads_and_run_pipelines`.
- **Minimal fix:** Added `UniqueConstraint("organization_id", "website")`
  to `Lead`. Every creation call site now catches `IntegrityError` and
  falls back to `get_lead_by_url` to return the winning concurrent
  request's lead — exactly the same "already exists" response shape
  used for the ordinary (non-racing) dedup path.
- **Tests:** `TestLeadCreationRace` (3 tests, including a real 2-thread/
  2-DB-session race proving exactly one lead is created and the other
  observes `IntegrityError`).

### A5 — Atomic daily quota (CONFIRMED GAP → FIXED, exact scenario from spec)

- **Problem:** `SubscriptionService._get_daily_usage()` computes remaining
  quota via a plain `COUNT(*)` over `Lead` rows. Two concurrent requests
  for the same organization can both read the same "remaining = N"
  snapshot before either commits, and both proceed — the precise race
  described in the spec (`remaining=10`, both requests read 10, both
  accept).
- **File / function:** `core/infrastructure/billing/subscription_service.py::_get_daily_usage`
  (left **unchanged** — still the accurate source of truth for display);
  new gate at `core/infrastructure/billing/quota_guard.py::reserve_daily_lead_quota`,
  called from all three lead-creation call sites immediately before each
  `create_lead()`.
- **Minimal fix:** A new additive table `daily_lead_quota_usage`
  (`organization_id`, `usage_date`, `leads_reserved`,
  `UNIQUE(organization_id, usage_date)`). Reservation is one conditional
  `UPDATE ... SET leads_reserved = leads_reserved + :n WHERE
  leads_reserved + :n <= :max_per_day`, checked via `rowcount`. A single
  UPDATE's WHERE-evaluation-and-SET is atomic in both SQLite and
  PostgreSQL — this sidesteps `SELECT ... FOR UPDATE` dialect
  differences entirely rather than relying on them.
- **Known, documented trade-off:** the reservation commits separately
  from the subsequent `create_lead()` call (every existing write helper
  in `crud.py` already commits internally — matching that convention was
  judged smaller/safer than changing `create_lead()`'s transaction
  behavior). In the rare case reservation succeeds but `create_lead()`
  itself then raises, that one quota unit isn't refunded for the rest of
  the day — a conservative failure direction (documented in
  `quota_guard.py`'s docstring), not a correctness risk.
- **Tests:** `TestAtomicDailyQuota` (5 tests), including **20 real
  concurrent threads racing for a limit of 10**, asserting exactly 10
  succeed and the persisted counter matches reality exactly — this is
  the spec's own example scenario, reproduced and closed.

### A1/A2 — General idempotency-key audit

- **Finding:** No formal `Idempotency-Key` header mechanism exists
  anywhere. Deliberately **not added** as a new, separate system: the
  existing per-URL dedup (above) plus the fixes above already give
  `POST /discovery/search`, `POST /leads`, and `POST /leads/single` the
  property the spec asks for — a retried/duplicated request cannot cause
  duplicate expensive scraping/LLM work for a business/lead that already
  exists or is already being processed. See §13 for why a generic
  Idempotency-Key layer was considered and declined.

### A6 — Tenant safety

Explicitly verified for both new mechanisms:
- Pipeline lock is keyed by `lead_id`, which is intrinsically
  single-organization (`test_lock_scoping_is_per_lead_not_global`).
- Quota reservation is keyed by `(organization_id, usage_date)`
  (`test_reservation_is_scoped_per_organization` — organization A
  exhausting its quota has zero effect on organization B).
- Lead-creation uniqueness is `(organization_id, website)`, not `website`
  alone (`test_same_url_different_organization_is_independent` — the
  identical URL for two different organizations succeeds for both).

No existing authentication, authorization, or organization-scoping logic
was touched.

---

## 4. Phase B — Resource / Concurrency Safety

### B4 — LLM concurrency (CONFIRMED GAP → FIXED)

- **Problem:** Zero concurrency guard on LLM calls anywhere. Worse: **three
  independent Groq call sites** each construct their own `ChatGroq`
  client and call `.invoke()` directly —
  `application/services/llm_provider.py` (used by company-intelligence,
  decision, and messaging agents), `core/infrastructure/enrichment/enricher.py`,
  and `core/infrastructure/messaging/messenger.py` (reachable as the
  "template fallback" path, which itself tries an LLM call first). All
  three draw on the same account/TPM budget with no coordination — this
  matches the spec's note about hitting Groq TPM limits in real testing.
- **File / function:** `application/services/llm_provider.py` (new
  `llm_call_slot()` + `_llm_call_semaphore`, applied inside
  `_invoke_chain_with_retry`); `core/infrastructure/enrichment/enricher.py::EnrichmentService._llm_enrich`
  (wrapped); `core/infrastructure/messaging/messenger.py::Messenger._generate_llm_message`
  (wrapped).
- **Minimal fix:** One process-wide `threading.Semaphore`
  (`LLM_MAX_CONCURRENT_CALLS`, default 4 — env-configurable, no
  arbitrary hardcoded production value), safe to acquire from worker
  threads since all three call sites are already reached via
  `asyncio.to_thread` (confirmed by tracing every call site in
  `application/workflows/graph_nodes.py`). Wraps only the actual
  `.invoke()` call in each of the three sites — retry/backoff, JSON
  parsing, and fallback logic in all three are untouched.
- **429/backoff:** Existing `tenacity` retry (broad
  `retry_if_exception_type(Exception)`, 2 attempts, exponential
  backoff+jitter) already covers rate-limit exceptions generically.
  Judged ALREADY ADEQUATE — adding bespoke 429 detection on top would be
  robustness-for-its-own-sake with no demonstrated gap.
- **Tests:** `TestLLMConcurrencyGuard` (2 tests): 12 real threads racing
  a semaphore of capacity 3 (never exceeded, all complete — no
  deadlock); and a leak-safety test proving an exception inside the
  guarded section doesn't strand permits.

### B1/B2/B3/B5/B6/B7/B8 — Audited, no gap requiring action

| Item | Finding | Verdict |
|---|---|---|
| B1 Concurrency audit | `DISCOVERY_MAX_CONCURRENT_PIPELINES`, `DISCOVERY_MAX_CONCURRENT_RESOLUTIONS`, `SCRAPER_ENRICHMENT_CONCURRENCY`, browser-pool semaphore all found and documented | Done |
| B2 Per-tenant pipeline concurrency | A single org can in principle submit up to 100 leads/request, each becoming a background task — but with B4 now closing the LLM gap and the existing browser-pool semaphore already global, the *actually expensive shared resources* are bounded process-wide. The only remaining effect is a fairness/latency one (one org's burst makes others queue longer on those same global semaphores), not a correctness or resource-exhaustion risk. The spec explicitly cautions against inventing a `MAX_PIPELINES_PER_ORG` value "unless actual code structure supports it" — current plan tiers don't differentiate concurrency, only daily volume, so no grounded value exists to configure. **Deferred** (see §13) rather than invented. | OPTIONAL IMPROVEMENT, not implemented |
| B3 Global resource budget | Covered by B4 (LLM) + pre-existing browser pool (B5) | ALREADY ADEQUATE (post-fix) |
| B5 Browser/Playwright concurrency | Genuine global singleton pool + semaphore, contexts/pages properly released (`finally` blocks) | ALREADY ADEQUATE — untouched |
| B6 Discovery provider concurrency | Existing try/except coverage across all 4 provider files (Overpass, Serper, Brave, http_utils); provider fallback already in place; a failing provider doesn't kill the pipeline | ALREADY ADEQUATE |
| B7 Backpressure | No uncontrolled task creation found: background tasks are cheap Python coroutines that immediately queue on the (now fully bounded) LLM/browser semaphores rather than doing unbounded work outside them | ALREADY ADEQUATE |
| B8 Pagination | `GET /leads` has `skip`/`limit`; every other list/history-shaped endpoint returns a single aggregate object, not a growing collection | ALREADY ADEQUATE |

---

## 5. Quota / Tenant Safety (explicit verification)

- **Organization isolation:** verified by test (§3, A6) — a second
  organization is completely unaffected by the first exhausting its
  quota or lock.
- **Idempotency isolation:** the pipeline lock is keyed by `lead_id`,
  which cannot span organizations (a `Lead` row belongs to exactly one
  `organization_id`).
- **Quota correctness:** verified with 20 real concurrent threads against
  a limit of 10 — exactly 10 succeed, persisted counter matches exactly.
- **Concurrent requests:** both the lock and the quota reservation were
  tested with genuine OS threads / `asyncio.gather` against independent
  DB sessions, not sequential calls on a shared session (which would
  trivially pass a broken implementation).

---

## 6. AI Resource Safety

- **LLM concurrency:** bounded process-wide (B4, above) across all three
  independent call sites.
- **429 behavior / retry:** unchanged, existing `tenacity` retry with
  exponential backoff+jitter; judged adequate.
- **Fallback:** unchanged in all three call sites — `safe_invoke_json`
  still returns `None` on any failure (never raises), every agent's
  deterministic/heuristic fallback path is exactly as before.
- **JSON correctness:** the JSON-parsing code path in
  `llm_provider.py::safe_invoke_json` was not touched at all — only the
  `.invoke()` call itself is now wrapped in the concurrency guard.
- **AI features remain enabled:** no gating logic
  (`can_use_ai_features`, `ai_features_enabled` state, human-review
  routing) was modified.

---

## 7. Scraper Resource Safety

- **Playwright/browser concurrency:** audited (B5) — already a proper
  global singleton pool with a semaphore; pages are always released via
  `finally`. No changes made; no gap found.
- **Session cleanup:** unchanged; existing `async with TieredScraper()`
  context-manager pattern untouched.
- **Retries:** unchanged.

---

## 8. API Compatibility

- **No endpoint renamed, removed, or restructured.**
- **No response field removed.** `PipelineResult`/the `/process` endpoint
  response gained one new possible `status` value
  (`SKIPPED_IN_PROGRESS`) — purely additive; existing consumers reading
  `lead_id`/`message`/`result` are unaffected, and no existing test
  asserts on an exhaustive status set for that field (confirmed by
  search).
- **No request schema changed.** No new required header, field, or query
  parameter was introduced anywhere (a formal `Idempotency-Key` header
  was deliberately not added — see §3 and §13).
- **HTTP status codes:** unchanged in every case except that the
  quota-exceeded 429 path is now reachable in a race scenario where it
  previously wasn't (previously: silent over-quota; now: correctly
  enforced) — this is a bug fix within the *existing* 429 contract, not
  a new one.

---

## 9. Database Impact

**Schema changes — three, all additive:**

1. `leads` table: added `UniqueConstraint(organization_id, website)`.
2. New table `active_pipeline_locks` (`lead_id` UNIQUE, `organization_id`,
   `pipeline_id`, `acquired_at`).
3. New table `daily_lead_quota_usage` (`UNIQUE(organization_id, usage_date)`,
   `leads_reserved`).

**No existing table's existing columns were modified or removed.**
**No existing rows are touched, migrated, or reinterpreted.**

**Migrations:** this project has no Alembic tooling wired up (schema is
created via `Base.metadata.create_all()` at startup, the same convention
already used for every other additive table in this codebase, e.g.
`PipelineExecutionRecord`). This is transparent for the two brand-new
tables (`create_all()` creates any missing table automatically) but has
one real caveat for the `leads` unique constraint:

> `create_all()` only creates **missing** tables — it does not `ALTER` a
> table that already exists in a database from before this change. A
> fresh database gets the constraint automatically and immediately. An
> **already-running deployment** needs a one-time manual step:
>
> ```sql
> -- 1. Find and resolve any existing duplicates first (keep the oldest row per pair):
> SELECT organization_id, website, COUNT(*) FROM leads
> GROUP BY organization_id, website HAVING COUNT(*) > 1;
>
> -- 2. Then add the constraint (PostgreSQL syntax):
> ALTER TABLE leads
>   ADD CONSTRAINT uq_leads_org_website UNIQUE (organization_id, website);
> ```
>
> Until that manual step runs on an existing production database, the
> `IntegrityError`-handling code added to every creation call site is
> simply dead code on that specific deployment (harmless — it just never
> triggers) rather than incorrect; the race window it closes remains open
> only until the operator runs the migration above.

---

## 10. Test Results

| Suite | Before | After |
|---|---|---|
| Full `pytest` (excl. `tests/scraper`, per this repo's own documented convention) | 687 passed, 0 failed | **708 passed, 0 failed** |
| New targeted regression tests | — | 21 passed (`tests/application/test_production_robustness.py`) |
| Concurrency-test stability | — | 5 consecutive full runs, 0 flakes |
| App boot / model registry | N/A | Verified: FastAPI app imports cleanly; all 3 new/modified constraints physically present in generated SQLite DDL (inspected directly, not inferred) |

`test_api.sh` / `runtime_validation.sh` / the real-pipeline benchmark
script referenced in the spec's Test Strategy were not present in this
repository (no such files exist at the repo root or under `scripts/`)
and no live Groq API key or external network access is available in this
environment (network egress is restricted to package registries and
GitHub) — so the "one real full-pipeline execution against a live LLM"
step could not be performed. Everything achievable without live external
services (full suite, targeted regressions, real multi-threaded
concurrency against the actual database, app-boot/DDL verification) was
completed.

---

## 11. Before / After

| Metric | Before | After |
|---|---|---|
| Test count | 687 | 708 |
| Confirmed correctness bugs (quota race, lead-creation race) | 2 open | 0 open |
| Duplicate pipeline execution possible under concurrency | Yes | No |
| Groq call sites with any concurrency bound | 0 of 3 | 3 of 3 (shared) |
| Schema changes | — | 3, all additive, all covered by regression tests |

---

## 12. Remaining Risks (evidence-backed only)

1. **`leads` unique constraint on an already-running deployment** requires
   the manual migration in §9 before it takes effect — documented, not
   silently glossed over.
2. **Quota reservation / lead-creation are two separate commits** (see §3
   trade-off) — an extremely rare true DB failure between them could
   waste one unit of quota. Fails conservative, not unsafe.
3. **No live-LLM smoke test was run** (no API key / network access in
   this environment) — the concurrency guard's correctness was proven
   with a synthetic semaphore test standing in for the real `.invoke()`
   call; the wrapping itself was verified by direct code inspection at
   all three call sites.

---

## 13. Deferred / Not Needed (considered, intentionally not implemented)

- **Kafka / RabbitMQ / Celery / Redis / a full queue redesign** — no
  evidence of unbounded task creation or a need for cross-process
  coordination beyond what a database-level constraint already provides;
  introducing any of these would be exactly the "future scalability
  architecture" the spec prohibits.
- **Distributed locks (Redis-based)** — the DB-UNIQUE-constraint pattern
  used for both the pipeline lock and the quota reservation already
  gives a durable, multi-process-safe guarantee without adding a new
  piece of infrastructure or a new failure mode (a Redis outage taking
  down locking that a database outage would already have taken down
  anyway).
- **A formal `Idempotency-Key` HTTP header mechanism** for
  `/discovery/search`/`/leads`/`/leads/single` — the existing
  per-URL/per-organization dedup, now made race-free, already delivers
  the property the spec cares about (no duplicate expensive
  scraping/LLM work on retry) without adding a new client-facing
  contract, a new header the client must generate/send, or a second
  "idempotency system" alongside the URL-based one that already exists.
  If product requirements later need idempotency for requests that
  *don't* share a stable natural key (e.g., a bulk import job with no
  single URL), that would be a new, narrower, evidence-backed
  requirement at that time — not one this audit found justified today.
- **`MAX_PIPELINES_PER_ORG` (per-tenant pipeline concurrency limit, B2)**
  — no grounded value exists in current plan/product configuration to
  set it to, and the actual expensive shared resources (LLM, browser)
  are now both globally bounded; the residual effect of one org's burst
  is queuing latency for others, not a correctness or resource-safety
  issue. Revisit if fairness complaints or resource-exhaustion evidence
  actually materialize.
- **Circuit breakers** — no evidence of cascading-failure risk; existing
  per-stage graceful degradation (`graph_nodes._run_stage`) already
  isolates one stage's failure from the rest of the pipeline.
- **Bespoke 429-specific retry logic** — the existing generic
  exception-based retry already covers this; no evidence it's
  insufficient.
- **Microservices / full scheduler redesign** — never remotely indicated
  by any evidence gathered; explicitly prohibited by the spec regardless.

---

## 14. Final Assessment

| Dimension | Score |
|---|---|
| Correctness under retries | 92/100 |
| Concurrency safety | 90/100 |
| Resource safety | 90/100 |
| Tenant safety | 95/100 |
| AI reliability | 88/100 |
| Production robustness | 90/100 |

(Deductions are almost entirely the two documented, non-critical
trade-offs in §12 — the existing-deployment migration step and the
non-atomic reservation-then-create commit pair — plus the inability to
run a live-LLM smoke test in this environment.)

---

## 15. Final Verdict

- **Did Phase A improve retry/idempotency safety?** Yes — the lead-creation
  race, the daily-quota race, and unprotected concurrent pipeline
  processing are all now closed, each with a passing regression test
  using real concurrency against the actual database.
- **Did Phase B improve resource protection?** Yes — the previously
  uncoordinated three-way Groq call-site concurrency is now bounded by
  one shared, process-wide gate.
- **Did any existing pipeline behavior change?** No — every existing
  stage, agent, fallback, and log record is untouched; the full
  pre-existing test suite (687 tests) passes unmodified.
- **Did any API contract break?** No — only one purely additive response
  value was introduced (`SKIPPED_IN_PROGRESS`), reachable only in the
  race scenario that previously silently duplicated work.
- **Did tenant isolation remain intact?** Yes — explicitly re-verified for
  both new mechanisms with dedicated tests.
- **Did AI functionality remain enabled?** Yes — no gating logic touched.
- **Did deterministic fallback remain intact?** Yes — untouched in all
  three LLM call sites.
- **Is the complete pipeline still integrated?** Yes — `LeadPipeline`,
  LangGraph stage graph, and every existing endpoint call it exactly as
  before.
- **Are further robustness changes actually justified right now?** No —
  every other candidate considered (§13) lacked evidence of an actual
  gap; adding any of them now would be robustness for its own sake.
- **Is the system ready to move to final deployment validation?** Yes,
  with one operational prerequisite: run the one-time `leads` table
  migration (§9) before or during deployment to an already-existing
  production database. Fresh deployments need no extra step.
