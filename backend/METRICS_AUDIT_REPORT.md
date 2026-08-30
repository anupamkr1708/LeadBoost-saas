# LeadBoost SaaS Backend — Evaluation/Analytics Metrics Audit
## Final Report

---

## 1. Metrics Audit Summary

Every suspicious metric named in the brief was traced end-to-end — raw
database records → metric query → aggregation → denominator →
normalization → API response — before any code was touched, per the
brief's own rule. Three genuine, demonstrable bugs were found and fixed,
each with a test that fails on the unmodified code and passes after the
fix. One additional metric was investigated and found to be **correctly
implemented**; the anomaly is explained by two disjoint data sources
being compared, not a defect. Two other suspiciously "perfect" metrics
(`average_completeness = 1.0`, `average_consistency = 1.0`) were traced
and found mathematically correct given their formula and inputs, but
with a documented limitation on their diagnostic power.

No architecture, business logic, scraper behavior, discovery ranking,
LangGraph routing, persistence semantics, tenant isolation, or Phase A/B
concurrency protections were touched. Every change is either (a) which
data feeds an existing formula, or (b) a display-formatting statement —
never the formula/algorithm itself.

**Test suite:** 755/755 passing (717 in `tests/` + 38 in
`scripts/evaluation/tests/` + `discovery_eval/`), including 9 new
regression tests, each proven to fail against the unmodified code first.

---

## 2. Confirmed Metric Bugs

### BUG #1 — `average_grounding ≈ 0.02` (Task 1)

- **Evidence:** `average_completeness = 1.0`, `average_consistency = 1.0`,
  `average_confidence = 0.78`, but `average_grounding = 0.02` — a sharp
  outlier among five metrics computed from the same 35 evaluation
  records, despite runtime evidence that Company Intelligence, Decision,
  and Messaging all executed successfully with real GPT-OSS-120B output.
- **Root Cause:** `confidence_evaluation`'s `source_text` was built from
  only `about_text` + `scraped_data.text_content` + `industry_analysis`.
  But the Decision Agent's own prompt
  (`application/prompts/templates/decision_v1.yaml`) explicitly instructs
  it to draw "evidence" from `pain_points`, `growth_indicators`, `score`,
  `qualification_label`, and `icp_alignment_score` too — none of which
  were in `source_text`. Its deterministic fallback
  (`DecisionAgent._decide_deterministically`) is worse: it produces
  evidence strings made *entirely* of those score/confidence values
  (e.g. `"Deterministic score: 72.3/100"`), which can never match scraped
  prose no matter what. Genuinely-grounded evidence was therefore scored
  as ungrounded purely because the reference text didn't contain what was
  being checked against — not because the AI hallucinated 98% of the
  time.
- **Affected File:** `application/workflows/graph_nodes.py`
- **Affected Function:** `LeadPipelineNodes.confidence_evaluation` (the
  `_do` inner function's `source_text` construction)
- **Severity:** High — this metric was actively misleading: a healthy,
  working pipeline appeared to have a near-total hallucination problem.
- **Business Impact:** Anyone using `average_grounding` to gauge AI
  trustworthiness (or to decide whether to disable/restrict AI features)
  would have concluded the system was unsafe when it was not.
- **Minimal Fix:** Expanded `source_text` to include `pain_points`,
  `growth_indicators`, and formatted score/qualification/confidence
  values — i.e., every field the evidence-producing code (both the LLM
  path and the deterministic fallback) can legitimately cite. No change
  to `evaluate_grounding`'s algorithm itself.
- **Regression Test:** `tests/application/test_metrics_audit.py::TestGroundingSourceTextCompleteness`
  — 3 tests: (1) LLM-path evidence citing real pain_points/growth_indicators
  now scores ≥0.9 (previously failed at <0.9, i.e. ~0.0), (2)
  deterministic-fallback evidence now scores ≥0.75 (previously 0.0
  exactly), (3) a negative control — evidence with no real support —
  still correctly scores <0.5 both before and after, proving the fix
  didn't just disable the check.
- **Before → After (on the crafted regression cases):** LLM-path case:
  ~0.0 → ≥0.9 (test asserted; ran and confirmed failing before, passing
  after). Deterministic-fallback case: exactly 0.0 → ≥0.75. Negative
  control: <0.5 → <0.5 (unchanged, as required).

### BUG #2 — `website_resolution_rate_pct = 0.0` (Task 2)

- **Evidence:** Runtime logs show real websites being resolved and leads
  created via the discovery pipeline, yet the metric reports exactly
  0.0%.
- **Root Cause:** `DiscoveryService._resolve_and_validate` counted a
  fallback resolution with the hardcoded check
  `b.resolution.resolved_via == "brave"`. But
  `DiscoveryService.__init__`'s actual default fallback provider is
  `SerperWebsiteResolver()` (`application/discovery/providers/serper_provider.py`),
  whose `.name` is `"serper"` — and `WebsiteResolver` always stamps
  `resolved_via` with `self.fallback_provider.name`
  (`application/discovery/website_resolver.py`), never the literal
  string `"brave"` unless a Brave-based resolver is explicitly injected.
  `api/endpoints/discovery.py` constructs `DiscoveryService(db)` with no
  override, so **every real deployment** uses the Serper default —
  making `"brave"` dead code and the count structurally guaranteed to be
  0 regardless of real fallback activity. This also explains why the
  *existing* test suite never caught it: its own stub fallback provider
  (`tests/application/discovery/test_discovery_service.py::_StubFallback`)
  happens to be named `"brave"` too, silently matching the same wrong
  assumption instead of the real production default.
- **Affected File:** `application/discovery/discovery_service.py`
- **Affected Function:** `DiscoveryService._resolve_and_validate`
- **Severity:** Medium — misleading operational metric, no data
  corruption or business-logic impact (the resolution itself was always
  correct; only the *count of it* was wrong).
- **Business Impact:** Anyone monitoring fallback-provider health/cost
  via this metric would see "0% ever resolves anything" and might
  wrongly conclude the fallback path is broken or unused, when it is
  actually working.
- **Minimal Fix:** Compare against `self.resolver_fallback.name` (the
  actually-configured provider) instead of the literal `"brave"`. Also
  future-proofs the check if the fallback provider is ever swapped
  again. Updated one stale code comment in
  `application/observability/metrics_service.py` that also referenced
  "Brave" specifically (documentation only, no behavior change).
- **Regression Test:** `TestWebsiteResolutionFallbackCounting` — 2 tests:
  (1) a website genuinely resolved via the real default provider
  (`"serper"`) is now counted (previously asserted `0 == 1` and failed);
  (2) a guardrail confirming primary-path (`"overpass"`) and failed
  (`"none"`) resolutions are still correctly never counted as fallback.
- **Before → After:** `resolved_via_fallback_count` for a genuine
  Serper-fallback resolution: `0` → `1` (confirmed failing then passing).

### BUG #3 — `social coverage = 10000.0%`, `technology coverage = 10000.0%` (Task 4)

- **Evidence:** An earlier benchmark report showed these two coverage
  figures at exactly 10000.0% — a mathematically impossible percentage.
- **Root Cause:** `scripts/evaluation/metrics.py::_pct()` already returns
  a 0–100 scaled percentage (`round(100.0 * numerator / denominator, 1)`).
  `scripts/evaluation/generate_report.py::_fmt_pct()` assumes its input
  is a 0–1 *fraction* and multiplies by 100 (`f"{x * 100:.1f}%"`). Every
  metric computed via `_rate()` (a genuine 0–1 fraction) is displayed
  correctly by `_fmt_pct`; every metric computed via `_pct()` and then
  *also* passed through `_fmt_pct` is double-converted. Confirmed and
  reproduced from scratch: a synthetic run with genuine 100% coverage
  computed `combined["normalizer"]["social_coverage"] == 100.0`
  (correct) but rendered `"- Social coverage: 10000.0%"` in the report.
- **Affected File:** `scripts/evaluation/generate_report.py`
- **Affected Function:** `write_pipeline_summary_md` (the `## Normalizer
  Metrics` section)
- **Severity:** Medium — cosmetic/reporting only; the underlying stored
  metric values (`pipeline_metrics.json`, `combined_metrics`) were
  **always correct**. This affects exactly four display lines:
  `organization_type_coverage`, `address_coverage`, `social_coverage`,
  `technology_coverage`. (The first two weren't named in the original
  evidence but are affected by the identical root cause — confirmed and
  fixed alongside the two that were.)
- **Business Impact:** An obviously-wrong number in a benchmark report
  undermines trust in every other number in that same report, even
  though only these 4 display lines were ever affected.
- **Minimal Fix:** Format these four already-0–100-scaled values
  directly (`f"{value:.1f}%"`) instead of routing them through
  `_fmt_pct()`. No change to `_pct()`, `_fmt_pct()` itself, or any
  `_rate()`-based metric (which were already correct).
- **Regression Test:** `TestBenchmarkCoveragePercentDoubleConversion` —
  2 tests: (1) genuine 100% coverage now renders as `"100.0%"`, not
  `"10000.0%"` (reproduced the exact bug against unmodified code first,
  confirmed the underlying metric value was already correct at 100.0,
  isolating the bug to display only); (2) a partial (50%) coverage case
  still renders correctly as `"50.0%"`, not `"5000.0%"` — proving the
  fix isn't a hardcoded 100%-only patch.
- **Before → After:** Display string for 100% coverage: `"10000.0%"` →
  `"100.0%"`. Underlying stored metric value: unchanged (`100.0`, always
  correct).

---

## 3. Metrics That Looked Suspicious But Are Actually Correct

### `duplicate_removal_rate_pct = 0.0` (Task 3) — NOT A BUG

- **Investigation:** Traced `_detect_duplicates` →
  `DiscoveryService._record_metrics` →
  `observability.repository.create_discovery_run_record` →
  `DiscoveryRunRecord.duplicates_removed` →
  `AnalyticsService.get_discovery_metrics`'s
  `total_duplicates / total_businesses`. This path is fully, correctly
  wired — proven by constructing a real `DiscoveryRunRecord` with
  `duplicates_removed=5` out of `businesses_returned=10` and confirming
  `get_discovery_metrics` correctly returns `50.0`, not `0.0`.
- **Why the evidence looked contradictory:** The "one duplicate test run
  reported 5 duplicate-flagged results" evidence uses the exact
  vocabulary (`"duplicate-flagged"`) of `discovery_eval/`'s own reporting
  (`outcome == "duplicate"`, `duplicate_businesses`,
  `discovery_eval/report.py`'s `"- Duplicates removed: N"` line) — a
  **separate, standalone benchmark harness** that, confirmed by
  inspecting its source directly, never calls
  `create_discovery_run_record` and never touches `DiscoveryRunRecord`
  (matching its own module docstring: "zero database writes"). The
  5-duplicate evidence and the live `duplicate_removal_rate_pct` are
  drawn from two disjoint data sources that were never meant to share a
  denominator — exactly the "mixing benchmark metrics with runtime
  metrics" trap Task 5 explicitly warns about.
- **Conclusion:** A `0.0%` live rate at a given moment is consistent with
  there having been genuinely zero in-batch duplicates among whichever
  `DiscoveryRunRecord`s were persisted and queried at that time — a
  property of that dataset, not a metric defect.
- **Verification tests (not bug-fix regressions, proof-of-correctness):**
  `TestDuplicateRemovalRateIsCorrectlyWired` — 2 tests confirming (1) the
  live metric correctly computes 50.0% given 5/10 real persisted
  duplicates, (2) `discovery_eval` genuinely never writes
  `DiscoveryRunRecord` (source-inspection assertion).
- **No code changed.**

### `average_completeness = 1.0` (exactly, every evaluation) — Correct, low diagnostic power

- **Investigation:** `evaluate_completeness` checks whether
  `expected_fields=["qualification", "recommended_action"]` are
  populated on the decision dict. Both `DecisionOutput.qualification`
  (default `"Unqualified"`) and `DecisionOutput.recommended_action`
  (default `"review"`) are Pydantic fields with non-empty string
  defaults — they can **never** be empty/falsy by construction, in
  either the LLM path or the deterministic fallback.
- **Conclusion:** `1.0` is mathematically correct given the formula and
  the schema guarantee — not a wiring bug. However, checking these two
  specific fields for "completeness" is close to a tautology: the metric
  cannot ever detect an incomplete decision output for these fields,
  because the schema structurally forbids emptiness. This is a
  **diagnostic-power observation**, not a bug, and per the brief's
  instruction not to invent/redesign metrics, `expected_fields` was left
  unchanged.

### `average_consistency = 1.0` (exactly, every evaluation) — Correct, but partly tautological in the fallback path

- **Investigation:** `evaluate_consistency` checks whether
  `decision.qualification == score_result.qualification_label`. In
  `DecisionAgent._decide_deterministically` (the no-LLM fallback path):
  `qualification = ctx.qualification_label` — a **direct, unconditional
  copy** of the score's own label, not an independently-derived
  judgment. Any evaluation produced via this path is tautologically
  guaranteed to score exactly `1.0`, by construction. In the LLM path,
  `qualification = payload.get("qualification") or ctx.qualification_label`
  — the LLM *can* genuinely diverge and score `0.4`.
- **Conclusion:** Mathematically correct given the formula. If the real
  `1.0` average came from a mix of fallback runs (tautological) and LLM
  runs that happened to agree every time, that's a legitimate, if
  notable, result — not a bug. Flagged as a **remaining interpretive
  caveat** below rather than a confirmed bug, since distinguishing
  "genuine 100% LLM agreement" from "mostly fallback runs" requires the
  actual per-record `source` field (`"llm"` vs `"rule_based"`) from the
  live database, which this environment does not have access to.

---

## 4. Regression Test Summary

All 9 new tests live in `tests/application/test_metrics_audit.py`, run
against the **unmodified** code first to prove each bug, then against
the fixed code to prove the fix, per the brief's Task 7 requirement:

| Test | Before (unmodified code) | After (fixed code) |
|---|---|---|
| `test_evidence_citing_pain_points_and_growth_indicators_is_grounded` | FAILED (grounding ≈0, assert `≥0.9` failed) | PASSED |
| `test_deterministic_fallback_evidence_is_grounded` | FAILED (grounding = 0.0 exactly) | PASSED |
| `test_evidence_with_no_support_anywhere_is_still_scored_ungrounded` | PASSED (already correct — negative control) | PASSED |
| `test_fallback_count_reflects_the_actually_configured_provider_name` | FAILED (`0 == 1`) | PASSED |
| `test_overpass_resolved_and_unresolved_are_never_counted_as_fallback` | PASSED (already correct — guardrail) | PASSED |
| `test_full_coverage_reports_as_100_not_10000_percent` | FAILED (`"10000.0%"` present) | PASSED |
| `test_partial_coverage_still_reports_correctly` | FAILED (`"5000.0%"` reproduced) | PASSED |
| `test_duplicate_removal_rate_is_correctly_nonzero_when_duplicates_exist` | PASSED (proves NOT a bug) | PASSED |
| `test_discovery_eval_harness_never_writes_a_discovery_run_record` | PASSED (proves disjoint data source) | PASSED |

**Full suite:** 755/755 passing (`tests/` + `scripts/evaluation/tests/`
+ `discovery_eval/`) — no regressions in any previously-passing test,
including the pre-existing `scripts/evaluation/tests/test_generate_report.py`
and the Phase A/B concurrency-protection tests from the prior hardening
pass.

---

## 5. Task 6 Compliance — What Was NOT Touched

Confirmed by diff review (`git diff --stat`): exactly 4 production files
changed (`graph_nodes.py`, `discovery_service.py`, `metrics_service.py`,
`generate_report.py`), all edits are either (a) which fields feed an
existing text-comparison input, or (b) a display-format string — plus
one comment-accuracy fix. Specifically preserved, unchanged:

- Scraper behavior, tiers, and retry logic
- Discovery ranking, identity resolution, and duplicate-detection logic
  itself (only the *counting* of one already-correct field was fixed)
- Enrichment behavior
- Company Intelligence behavior and prompts
- Scoring thresholds and formula
- LangGraph node routing and stage order
- Persistence semantics (no schema change; no new tables; no column
  changes)
- Tenant isolation (`organization_id` scoping untouched everywhere)
- Phase A/B concurrency protections (`ActivePipelineLock`,
  `DailyLeadQuotaUsage`, the LLM concurrency semaphore) — untouched,
  confirmed by the full suite still passing

---

## 6. Remaining UNVERIFIED Items

- **Whether the real production `average_consistency = 1.0` reflects
  genuine LLM agreement or mostly-fallback runs** — requires querying the
  live `ai_decision_logs`/evaluation records' `source` field
  (`"llm"` vs `"rule_based"`) against the actual database, which this
  environment does not have access to. The *implementation* is confirmed
  correct either way; only the *interpretation* of the specific
  production number is unverified.
- **Whether the specific 35 evaluations behind the reported
  `average_grounding = 0.02` snapshot will land at a specific new value**
  once this fix is deployed — the fix is proven correct on crafted,
  representative cases; the exact new production average depends on the
  real mix of LLM-path vs. fallback-path evidence in that dataset, which
  was not directly queryable here.
- **The exact discrepancy between 41 leads and 33 pipeline executions**
  noted in the runtime evidence was not investigated — it was not named
  as a suspicious metric in the brief, and chasing it would exceed the
  brief's explicit scope of only fixing named/discovered metric bugs.

---

## 7. Final Answers

- **Are the analytics metrics mathematically correct?** After this fix,
  yes, for every metric investigated. Before the fix, two were not
  (grounding's input data, and the coverage-percentage display).
- **Is grounding being measured correctly?** Now yes. Previously no —
  `source_text` was missing fields the evidence legitimately cites,
  causing systematic under-scoring unrelated to actual hallucination.
- **Is discovery resolution rate being measured correctly?** Now yes.
  Previously no — a hardcoded provider-name string didn't match the
  actually-configured default provider.
- **Is duplicate removal rate being measured correctly?** Yes, and it
  always was — the apparent contradiction was two different data sources
  being compared, not a metric bug.
- **Are normalization coverage percentages correct?** The underlying
  values always were; the *display* of four of them was wrong (doubled)
  and is now fixed.
- **Which metrics are production-trustworthy?** All of Task 1–4's
  metrics, post-fix: `average_grounding`, `website_resolution_rate_pct`,
  `duplicate_removal_rate_pct`, and the four normalizer coverage
  percentages. Also `success_rate_pct`, `avg/median/p95_processing_time_ms`,
  `discovery_success_rate_pct` — all traced and found correct with no
  changes needed.
- **Which metrics remain UNVERIFIED?** The *production interpretation*
  (not the *implementation correctness*) of `average_consistency = 1.0`
  and the exact post-fix value of `average_grounding` on the real
  dataset — both require live database access this environment doesn't
  have.
- **Did you change anything outside metric calculation/test code?** No.
  Every change is confined to how metric inputs are constructed or how
  a value is displayed; no business logic, thresholds, routing, schema,
  or runtime behavior was altered.
