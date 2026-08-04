# Discovery Evaluation Framework

A standalone, read-only evaluation harness for LeadBoost's Discovery
pipeline (`application.discovery.discovery_service.DiscoveryService`).
It runs ~113 real-world queries through the production pipeline exactly
up through ranking, then measures it. It never modifies, redesigns, or
reimplements Discovery, never creates a Lead, never writes to the
database, and never spends subscription quota.

This is meant to become the permanent regression benchmark: run it
before and after any Discovery change and diff `evaluation.json`.

## Install

Drop this `discovery_eval/` folder into your repo **next to**
`application/` (i.e. at repo root, as a sibling of the `application`
package):

```
your-repo/
├── application/
│   └── discovery/
│       └── discovery_service.py
├── core/
└── discovery_eval/        <- this folder
    ├── run_eval.py
    ├── queries.py
    ├── metrics.py
    ├── report.py
    ├── charts.py
    ├── outputs/
    └── tests/
```

Install the two extra dependencies the framework itself needs (your
repo's own dependencies already cover everything Discovery needs):

```bash
pip install matplotlib pytest --break-system-packages
```

## Run it

From the repo root:

```bash
python -m discovery_eval.run_eval
```

Useful flags:

```bash
# Quick smoke run -- first 10 queries only
python -m discovery_eval.run_eval --limit 10

# Throttle concurrency (default 3) if you're rate-limited by Serper/Overpass
python -m discovery_eval.run_eval --concurrency 2
```

**Requires the same environment your production Discovery pipeline
already runs with** -- most importantly `SERPER_API_KEY`, since Serper
is both the fallback business-search provider and (by default) the
fallback website resolver. Overpass needs no key. If a key is missing,
Discovery's own providers degrade gracefully (they log
"unconfigured"/return no candidates rather than throwing) -- this shows
up in the report as a low resolution/validation rate, not a crash.

This will make real, un-cached calls to Overpass and Serper for every
query (~113 queries × however many candidates each returns). Expect the
full run to take several minutes and to count against your Serper
quota for that account, same as any real search traffic would.

## What it actually calls

For each query, in order, reusing Discovery's own code with nothing
recomputed:

```python
parsed = service.parser.parse(query)                                    # QueryParser
candidates = await service._search(parsed)                              # Overpass -> Serper fallback
discovered, fallback_count = await service._resolve_and_validate(...)   # website resolution + validation + identity build
service._detect_duplicates(discovered)                                  # dedup
ranked = rank_businesses(eligible, parsed.category, parsed.location)    # ranking
```

It stops there -- the exact point the pipeline diagram in the brief
calls "Discovery Result." It never calls `discover_and_create_leads()`,
never touches `_create_leads_and_run_pipelines` or `_record_metrics`,
and constructs `DiscoveryService(db=None)` since nothing it calls reads
`self.db`.

## Outputs

Everything lands in `discovery_eval/outputs/`:

| File | Contents |
|---|---|
| `evaluation.json` | Every query record + business record + aggregate metrics, machine-readable |
| `query_results.csv` | One row per query -- timing, counts, winner confidence/stability/margin/agreement |
| `business_results.csv` | One row per business Discovery considered (selected, not-selected, rejected, duplicate, no-website) |
| `manual_review.csv` | One row per **selected** business + one row per query with zero validated results. Review columns (`expected_website`, `correct`, `comments`) are left blank for a human |
| `evaluation_report.md` | Full write-up: latency, validation, provider, ranking, identity analysis, health score, evidence-gated recommendations |
| `latency.png` | Execution-time distribution + average time per pipeline stage |
| `confidence.png` | Winner-confidence distribution |
| `validation_success.png` | Business outcome breakdown (selected / not-selected / validation-failed / no-website / duplicate) |
| `provider_performance.png` | Candidates contributed per provider |

## Regression testing across Discovery changes

```bash
python -m discovery_eval.run_eval                     # before your change
mv discovery_eval/outputs discovery_eval/outputs_before
# ... make your Discovery change ...
python -m discovery_eval.run_eval                     # after
```

Then diff the two `evaluation.json` files' `aggregate_metrics` blocks,
in particular: `discovery_health_score`, `validation_success_rate`,
`website_resolution_success_rate`, `avg_confidence`,
`avg_identity_stability`, `p95_latency_ms`, and the
`directory_marketplace_selected_count` / `structural_false_positive_count`
audit counters.

## The framework's own test suite

```bash
pytest discovery_eval/tests/ -v
```

These tests do **not** import `application.discovery` -- they use
`SimpleNamespace`-based no-op stubs that duck-type
`DiscoveredBusiness`/`VerifiedDigitalIdentity`'s attribute shape, so
they run in isolation and verify metrics math, CSV/JSON export, chart
generation, and report generation without needing API keys or network
access.

## Notes on specific fields (where each number actually comes from)

- **Winner Confidence / Identity Stability / Provider Agreement /
  Verification Strength** — read directly off the selected business's
  `VerifiedDigitalIdentity`: `.selection_confidence`, `.quality.identity_stability`,
  `.consensus.provider_consensus`, `.verification_quality.strength`.
- **Competition Margin / Competition Score** — read off the matching
  `CompetitionResult` in `identity.identity_resolution.competition`
  (`.winning_margin` at query level, `.relative_confidence` at business
  level), matched to the selected business by domain.
- **False Positive Flags** — read off the matching `IdentityCandidate.risk`
  tuple in `identity.identity_resolution.identity_candidates` — these are
  the structural signals `false_positive.py` folds in during resolution,
  not something this framework computes.
- **Evidence / Feature / Verification Count** — `len(identity.evidence_bundle.items)`,
  the sum of `len(fs.features)` across `identity.feature_store.all()`, and
  `len(identity.verification_bundle.results)`.
- **`directory_or_marketplace_selected` / `social_profile_selected`
  audit flags** — checked against a domain list kept *independent* of
  `website_validator.REJECTED_DOMAINS` on purpose: Discovery's own list
  is part of what's being audited (it's previously had gaps -- see that
  module's own comments), so grading the final selection against a
  second, separately-maintained list is what would catch the next one.
- **`discovery_health_score`** — this framework's own unweighted mean
  of five already-computed rates (validation success, resolution
  success, avg confidence, avg identity stability, parse success). It
  is not a number Discovery computes internally.
