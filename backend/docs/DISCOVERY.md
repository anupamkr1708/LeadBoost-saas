# Business Discovery Layer

Sits *before* the existing, unmodified `LeadPipeline`. Turns a natural
-language business search ("Top shoe stores in Mumbai") into validated
`Lead` rows that are handed to `LeadPipeline.execute()` exactly the way
manually-created leads already are. Nothing downstream of Lead creation
changed: Core, LangGraph, the AI agents, evaluation, and observability are
all untouched by this layer.

No LLM executes anywhere in this package. Every stage is deterministic and
independently testable.

---

## 1. Architecture Diagram

```
                    POST /api/v2/discovery/search
                    {"query": "Top shoe stores in Mumbai", "limit": 20}
                                    |
                                    v
                    application/discovery/discovery_service.py
                              (DiscoveryService)
                                    |
   +--------------------------------------------------------------------+
   |                                                                     |
   v                                                                     |
query_parser.QueryParser                                                 |
  (regex only, no LLM)                                                   |
  "Top shoe stores in Mumbai"                                            |
    -> category="shoe stores", location="Mumbai", limit=20               |
   |                                                                     |
   v                                                                     |
providers/overpass_provider.py  (PRIMARY search)                         |
  category -> OSM tag ("shop"="shoes")                                   |
  -> Overpass QL query -> POST overpass-api.de                           |
  -> raw elements                                                        |
   |                                                                     |
   v                                                                     |
business_normalizer.py (pure, no I/O)                                    |
  raw Overpass tags -> BusinessCandidate(name, address, phone, website,  |
                                          lat/lon, category)              |
   |                                                                     |
   v                                                                     |
website_resolver.py  (per business)                                      |
  candidate.website?                                                     |
    -> website_validator.py: reachable + HTML + not a directory?         |
       yes -> DONE (resolved_via="overpass")                             |
       no  -> providers/brave_provider.py (FALLBACK, resolution only)    |
              -> candidate URL -> website_validator.py -> DONE/none      |
    -> no website at all -> brave_provider.py -> validate -> DONE/none   |
  (never fabricates a URL; "none" means website stays null)              |
   |                                                                     |
   v                                                                     |
duplicate_detector.py (pure, in-batch)                                   |
  normalized domain, else name+phone -> mark is_duplicate                |
   |                                                                     |
   v                                                                     |
ranking.py (pure, deterministic scoring, no AI)                          |
  has website / category match / location match / rating / reviews /    |
  contact completeness -> sort best-first -> trim to `limit`             |
   |                                                                     |
   v                                                                     |
DiscoveryService: Lead creation                                          |
  - dedupe against existing org leads (crud.get_lead_by_url, reused)     |
  - quota check (SubscriptionService, reused)                            |
  - core.infrastructure.database.crud.create_lead(...)                  |
   |                                                                     |
   v                                                                     |
   +------------------------> application/workflows/lead_pipeline.py <---+
                               run_lead_pipeline(lead_id)   [UNCHANGED]
                               (bounded concurrency via asyncio.Semaphore,
                                DISCOVERY_MAX_CONCURRENT_PIPELINES)
                                    |
                                    v
                    DiscoveryResponse{businesses: [...pipeline_status]}
```

---

## 2. Discovery Workflow (stage by stage)

| # | Stage | Module | Deterministic? | On failure |
|---|-------|--------|-----------------|------------|
| 1 | Parse query | `query_parser.py` | Yes (regex) | Raises `QueryParseError` -> HTTP 400 |
| 2 | Business search | `providers/overpass_provider.py` | Yes | Retried (3x, backoff) inside the provider, then degrades to an empty result set -- the request still succeeds with `businesses_found: 0` |
| 3 | Normalize | `business_normalizer.py` | Yes (pure function) | Unnamed elements are silently dropped |
| 4 | Website resolution | `website_resolver.py` + `providers/brave_provider.py` | Yes | Per-business; a resolution error is caught and treated as "no website found," never aborts the batch |
| 5 | Website validation | `website_validator.py` | Yes | Per-business; failure -> business is excluded from Lead creation, reported with a `reason` |
| 6 | Duplicate detection | `duplicate_detector.py` | Yes (pure function) | N/A (never fails) |
| 7 | Ranking | `ranking.py` | Yes (pure function) | N/A (never fails) |
| 8 | Lead creation | `discovery_service.py` | Yes | Per-business; a DB error is caught, business reported as `pipeline_error`, batch continues |
| 9 | Pipeline execution | `application/workflows/lead_pipeline.py` (**unchanged**) | N/A -- this is the existing AI pipeline | Per-business; a crash is caught, `pipeline_status: "FAILED"`, batch continues |

---

## 3. Provider Strategy

```
                     BusinessSearchProvider (ABC)          WebsiteResolverProvider (ABC)
                     providers/base.py                     providers/base.py
                              ^                                       ^
                              |                                       |
                  OverpassProvider                       BraveWebsiteResolver
                  providers/overpass_provider.py          providers/brave_provider.py
                  "overpass"                               "brave"
                  PRIMARY, business discovery              FALLBACK, website
                  category -> OSM tag map                  resolution ONLY
                  (25+ entries incl. all spec              never scrapes results,
                  examples; unmapped categories             only reads the URL field
                  fall back to a name-text search)          from Brave's own metadata
                  retries via existing                      inert (returns None) if
                  application.utils.retry.with_retry         BRAVE_API_KEY is unset
```

`DiscoveryService` depends only on the two ABCs (constructor-injected,
defaulting to the real implementations) -- it never imports
`OverpassProvider` or `BraveWebsiteResolver` by name in its logic, which is
exactly what makes every test in `tests/application/discovery/` able to
swap in stub providers with zero real network calls.

Why Overpass for search: free, no API key required to get started, has
structured tags (name/address/phone/website/category) rather than just a
list of links, and is a natural fit for "shops/services in a place" style
queries via its `area[name=...]` + tag-filter query shape.

Why Brave for resolution only (not also search): the spec is explicit
that Brave must never be scraped and must only be used for website
resolution -- this keeps the "no LLM, no scraping before the pipeline"
guarantee intact, since a general web-search-based *business discovery*
provider would tempt scraping search-result pages for business details,
which Overpass already gives us in structured form.

---

## 4. Website Resolution Flow

```
BusinessCandidate.website present?
   |
   yes --> website_validator.validate(url)
   |          |
   |          ok --> WebsiteResolution(website=url, resolved_via="overpass", validated=True)   [DONE]
   |          |
   |          not ok --> fall through to Brave fallback (below)
   |
   no  --> fall through to Brave fallback (below)

Brave fallback (only if a BraveWebsiteResolver is configured):
   |
   BRAVE_API_KEY unset? --> WebsiteResolution(website=None, resolved_via="none")               [DONE, never fabricated]
   |
   query = "<business name> <location> official website"
   |
   first Brave result whose domain (a) isn't a directory/social domain and
   (b) shares a word with the business name
   |
   found? --> website_validator.validate(candidate_url)
   |             ok     --> WebsiteResolution(website=url, resolved_via="brave", validated=True) [DONE]
   |             not ok --> WebsiteResolution(website=None, resolved_via="none", validated=False) [DONE, never fabricated]
   |
   not found --> WebsiteResolution(website=None, resolved_via="none", validated=False)          [DONE, never fabricated]
```

`website_validator.py` rejects (before even making an HTTP request, when
the domain alone is enough to tell):

```
facebook.com, instagram.com, linkedin.com, twitter.com/x.com, yelp.com,
justdial.com, indiamart.com, tripadvisor.com, yellowpages.com,
google.com/maps.google.com, youtube.com, pinterest.com, sulekha.com
```

and otherwise requires: HTTPS/HTTP reachable, final status 200 (redirects
to 301/302 followed), an `html` content type, and -- checked *after*
following redirects too -- that the final landing domain isn't one of the
rejected domains either (catches a dead business domain parked and
redirected into a directory).

---

## 5. Failure Recovery Flow

```
OpenStreetMap (Overpass) unavailable
   -> retried up to 3x with exponential backoff (application.utils.retry,
      the same generic retry utility used elsewhere in the codebase)
   -> still failing -> ProviderError caught in discovery_service._search()
   -> graceful failure: businesses_found=0, request still returns 200

Website resolution failure (Brave unset, no candidate, or candidate
invalid)
   -> website = null, business continues through the pipeline as
      "no_website" in the response -- never blocks the batch

Website validation failure (unreachable / non-HTML / directory domain)
   -> business excluded from Lead creation, reported as
      "validation_failed" with a `reason`; batch continues

Lead creation failure (DB error for one business)
   -> caught per-business, reported as "pipeline_error"; batch continues

LeadPipeline failure for one business
   -> caught per-business inside discovery_service._run_pipelines(); that
      business's outcome gets pipeline_status="FAILED"; every other
      business's pipeline still runs (asyncio.gather over independent
      run_lead_pipeline() calls, each opening its own DB session exactly
      as the existing manual-lead-creation endpoints already do)
```

No single business's failure at any stage raises out of
`DiscoveryService.discover_and_create_leads()` -- the worst case for any
individual business is an outcome entry explaining why, never a 5xx for
the whole request. (`QueryParseError` is the one intentional exception:
an unparseable *query* is a client input error, correctly surfaced as
HTTP 400 before any provider is even called.)

---

## 6. Metrics (extends the existing Analytics module -- no new framework)

New table `discovery_run_logs` (`application/observability/models.py::DiscoveryRunRecord`),
one row per `discover_and_create_leads()` call. Aggregated by the same
`AnalyticsService` (`application/observability/metrics_service.py`) that
already serves pipeline/evaluation metrics, using the same in-Python
`statistics`-based approach (no caching, no DB-specific SQL):

| Metric | Formula |
|---|---|
| Discovery Success Rate | `validated_leads / businesses_returned * 100` |
| Website Resolution Rate | `websites_resolved_via_fallback / businesses_missing_website * 100` (of the businesses Overpass returned with *no* website, what fraction did the Brave fallback resolve+validate) |
| Duplicate Removal Rate | `duplicates_removed / businesses_returned * 100` |
| Average Discovery Time | mean `duration_ms` across runs, measured from query received to the response being built (includes Lead creation and pipeline execution) |

Served by `GET /api/v2/analytics/discovery-metrics?hours=`, added to the
existing `api/endpoints/analytics.py` router (same file, same
conventions) rather than a new analytics module or endpoint file.

---

## 7. Design Decisions

- **No LLM before the pipeline.** Category classification is a fixed
  keyword->OSM-tag table (`overpass_provider._CATEGORY_TAG_MAP`), not a
  classifier. Ranking is a fixed weighted-points formula
  (`ranking.score_business`), not a model. This was a hard constraint, and
  also keeps the layer fast and free to run per search.
- **Overpass over a paid places API for primary search.** No API key
  required, structured tags map directly onto `BusinessCandidate`, and it
  keeps the Discovery Layer usable out of the box; `BusinessSearchProvider`
  is still an interface, so a paid provider (Google Places, etc.) could be
  added later as an alternative or additional primary source without
  touching `DiscoveryService`.
- **Brave strictly scoped to resolution.** Reusing it for search too would
  blur the "never scrape search results, only structured discovery data"
  boundary the spec draws; keeping it single-purpose also means it's
  optional infrastructure (`BRAVE_API_KEY` unset -> Discovery still works,
  just with a lower resolution rate for businesses OSM has no website for).
- **Validation is a cheap reachability check, not a second scraper.**
  `website_validator.py` makes one lightweight GET request. The *real*
  scrape still happens exactly once, inside the existing
  `TieredScraper`/`LeadPipeline`, avoiding duplicated scraping logic or a
  second scraping abstraction.
- **Synchronous pipeline execution, bounded concurrency.** The response
  contract (`pipeline_status` per business) requires the pipeline to have
  actually finished by the time Discovery responds, so pipelines run via
  `asyncio.gather` bounded by `DISCOVERY_MAX_CONCURRENT_PIPELINES`
  (default 3) rather than serially (too slow for "top 20") or fully
  unbounded (resource risk, especially for the Playwright browser pool).
- **No Domain/schema changes.** Discovery-known fields that already have a
  Lead column (`phone`, `address`) are pre-seeded via the existing
  `LeadUpdate` schema right after creation. Fields with no Lead equivalent
  (category, rating, review_count, coordinates) are used only within the
  Discovery response/ranking and are not persisted to Lead -- adding
  columns for them was judged unnecessary since the existing
  scrape/enrichment stages independently populate the equivalent Lead
  fields once the pipeline runs.
- **One new table, not three.** `DiscoveryRunRecord` captures per-run
  aggregate counts (businesses returned, missing websites, resolved via
  fallback, duplicates, validated leads) rather than one row per business
  per stage -- sufficient for every requested metric while staying a
  single, simple, additive table alongside the existing observability
  tables.
