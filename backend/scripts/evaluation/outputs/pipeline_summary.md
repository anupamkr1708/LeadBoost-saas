# Full-Pipeline Benchmark Report

Generated: 2026-08-05T15:57:40.255474+00:00

This report evaluates the production pipeline end to end: Discovery -> Scraper -> Normalizer -> Enricher -> Company Intelligence -> Lead Scoring, executed through each stage's own existing, unmodified production code (no shortcuts, no mocking, no re-implemented logic). The benchmark queries are the exact ~100-query Discovery benchmark dataset (`discovery_eval/queries.py`) -- reused as-is, not regenerated.

## Executive Summary

- Benchmark queries: 1
- Sites pushed through the full pipeline: 1
- End-to-end success rate: 100.0%
- Partial success rate: 0.0%
- Failure rate: 0.0%
- Discovery validation success rate: 83.3%
- Discovery Health Score: 83.2 / 100

## Discovery Metrics

- Website resolution success rate: 100.0%
- Validation success rate: 83.3%
- Duplicate rate: 16.7%
- Official domain precision: 100.0%
- Provider agreement rate: 100.0%
- Query parse success rate: 100.0%
- Average query latency: 5691.0 ms (p95 5691 ms)

Full Discovery-stage breakdown (per-query CSV/JSON/charts) is in `discovery_eval/outputs/` -- this section is a read-only summary of that same, unmodified `aggregate_metrics()` output.

## Scraper Metrics

- Sites attempted: 1
- Scrape success rate: 0.0%
- Blocked rate: 100.0%
- Multi-page enrichment rate: 0.0%
- Average pages scraped: None
- Structured extraction coverage: 0.0%
- Fallback tier usage: {"structured_data": 1, "curl_cffi": 1, "playwright": 1, "requests": 1}

## Normalizer Metrics

- Sites normalized: 1
- Organization type coverage: 10000.0%
- Address coverage: 10000.0%
- Social coverage: 10000.0%
- Technology coverage: 10000.0%

Field completeness: {"organization_type": 100.0, "brand_name": 100.0, "description": 100.0, "founded_year": 100.0, "employee_count": 0.0, "emails": 0.0, "phones": 100.0, "address": 100.0, "operating_regions": 100.0, "products": 0.0, "services": 0.0, "social_profiles": 100.0, "technologies": 100.0}

## Enricher Metrics

- Enrichment coverage: 100.0%
- Deterministic (heuristic) contribution: 100.0%
- External API contribution: 0.0%
- LLM contribution: 0.0%
- Merged contribution: 0.0%
- LLM-or-merged rate (upper bound, see caveat in `evaluation/metrics.py`): 0.0%

## Company Intelligence Metrics

- Heuristic usage rate: 0.0%
- LLM usage rate: 100.0%
- Explainability coverage: 100.0%
- Grounded technology precision: 1.0
- Hallucination check: **PASS** (0 heuristic results checked)

## Lead Scoring Metrics

- Leads scored: 1
- Score distribution: avg=25.9446 median=25.9446 min=25.9446 max=25.9446
- Qualification distribution: {"Disqualified": 100.0}
- Criterion average contribution: {"industry_match": 15.0, "company_size": 0.0, "email_quality": 0.0, "scrape_quality": 0.0, "enrichment_quality": 10.9446, "linkedin_presence": 0.0}

## Pipeline Metrics

- Stage completion rate: {"scrape": 0.0, "normalize": 1.0, "enrich": 1.0, "company_intelligence": 1.0, "scoring": 1.0}
- Evidence preservation rate: 100.0%
- Confidence propagation (avg per stage): {"scrape": 0.2, "normalize": 0.8591, "enrich": 0.7296, "company_intelligence": 0.6}

## Top Failure Reasons

No stage failures recorded in this run.

## Most Common Missing Fields

| Field | Missing (%) |
|---|---|
| employee_count | 100.0 |
| emails | 100.0 |
| products | 100.0 |
| services | 100.0 |

## Average Latencies

| Stage | Avg (ms) | Median (ms) | P95 (ms) | n |
|---|---|---|---|---|
| scrape | 17837.0 | 17837 | 17837 | 1 |
| normalize | 1.0 | 1 | 1 | 1 |
| enrich | 3.0 | 3 | 3 | 1 |
| company_intelligence | 3385.0 | 3385 | 3385 | 1 |
| scoring | 0.0 | 0 | 0 | 1 |
| end_to_end | 21226.0 | 21226 | 21226 | 1 |

## Confidence Distributions

| Stage | n | avg | median | min | max |
|---|---|---|---|---|---|
| scrape | 1 | 0.2 | 0.2 | 0.2 | 0.2 |
| normalize (field_confidence) | 11 | 0.8591 | 0.9 | 0.6 | 0.95 |
| enrich | 1 | 0.7296 | 0.7296 | 0.7296 | 0.7296 |
| company_intelligence (icp_alignment) | 1 | 0.6 | 0.6 | 0.6 | 0.6 |
| lead_scoring (total_score) | 1 | 25.9446 | 25.9446 | 25.9446 | 25.9446 |

## Recommendations

Every recommendation below is triggered by a specific metric in this run crossing a fixed threshold (see `scripts/evaluation/generate_report.py::_recommendations`). None of these propose an architectural change -- this framework measures the pipeline, it does not redesign it.

- Scraper blocked rate is 100.0% (>15%) across sites attempted this run -- check `pipeline_results.json` for which sites report `blocked_detected: true` and whether they share a common anti-bot vendor.
- Scraper success rate is 0.0% (<70%) -- review `stage_metrics.csv`'s `scrape` row and cross-check against the `tiers_attempted` field in `pipeline_results.json` for sites that never escalate past the first tier.