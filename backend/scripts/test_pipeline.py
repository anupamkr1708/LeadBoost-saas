"""
Full pipeline integration test: Scraper -> Normalizer -> Enrichment.

Runs the complete production pipeline (exactly as it's wired in the app --
no shortcuts, no mocking) against a configurable list of real websites
spanning many business categories, and saves the full per-stage output
(raw scrape result, normalized fields, enrichment result, plus timing)
to a single JSON file for later analysis.

WHERE TO PUT THIS
-----------------
    backend/scripts/test_pipeline.py

HOW TO RUN
----------
    python -m scripts.test_pipeline
    # or
    python scripts/test_pipeline.py

Both work from the `backend` directory -- a sys.path shim below handles it
either way. Pass URLs on the command line to override TEST_SITES:

    python -m scripts.test_pipeline https://example.com https://another.com

A NOTE ON THE SITE LIST BELOW
------------------------------
These 25 sites were chosen for CATEGORY diversity (retail, SaaS, healthcare,
education, NGO, government, professional services, hospitality,
manufacturing, logistics, finance, anti-bot-prone, JS-heavy, and sparse/
minimal sites) to exercise every scenario in the production-readiness
review's coverage checklist. They have NOT been network-verified against
your actual environment -- swap any of them for your own, and set
RESPECT_ROBOTS_TXT=true in your environment before running this at any
real scale or against sites you don't have an existing relationship with.
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from core.domain.models.lead import Lead  # noqa: E402
from core.infrastructure.enrichment.enricher import WaterfallEnricher  # noqa: E402
from core.infrastructure.logging import (  # noqa: E402
    get_logger,
    log_enrichment_attempt,
    log_scraping_attempt,
    setup_logging,
)
from core.infrastructure.normalization.normalizer import normalize_scraped_fields  # noqa: E402
from core.infrastructure.scraping.scraper import (  # noqa: E402
    TieredScraper,
    close_scraper_resources,
)

setup_logging()
logger = get_logger(__name__)

# ============================================================================
# EDIT THIS: (url, category label) pairs. The category label is purely
# descriptive (for the summary breakdown below) -- it is never passed into
# the pipeline itself.
# ============================================================================
TEST_SITES: List[Tuple[str, str]] = [
    ("https://mochishoes.com", "retail_ecommerce"),
    ("https://www.zappos.com", "retail_ecommerce"),
    ("https://www.allbirds.com", "retail_ecommerce"),
    ("https://stripe.com", "saas_fintech"),
    ("https://www.notion.so", "saas"),
    ("https://slack.com", "saas"),
    ("https://www.mayoclinic.org", "healthcare"),
    ("https://www.clevelandclinic.org", "healthcare"),
    ("https://www.harvard.edu", "education_university"),
    ("https://www.mit.edu", "education_university"),
    ("https://www.wwf.org", "ngo_nonprofit"),
    ("https://www.redcross.org", "ngo_nonprofit"),
    ("https://www.nasa.gov", "government"),
    ("https://www.usa.gov", "government"),
    ("https://www.dlapiper.com", "professional_services_legal"),
    ("https://www.zillow.com", "real_estate"),
    ("https://www.marriott.com", "hospitality_hotel"),
    ("https://www.hilton.com", "hospitality_hotel"),
    ("https://www.3m.com", "manufacturing"),
    ("https://www.caterpillar.com", "manufacturing"),
    ("https://www.fedex.com", "logistics"),
    ("https://www.chase.com", "finance"),
    ("https://www.cloudflare.com", "tech_anti_bot_prone"),
    ("https://www.airbnb.com", "js_heavy_spa"),
    ("https://example.com", "sparse_minimal"),
    ("https://www.python.org", "sparse_oss"),
]

OUTPUT_PATH = _BACKEND_ROOT / "scripts" / "pipeline_results.json"

# Canonical normalized fields whose presence rate we report in the summary
# -- ties directly back to the "field completeness" / "extraction coverage"
# evaluation metrics called out in the production-readiness review.
_CANONICAL_FIELDS_TO_TRACK = [
    "organization_type", "brand_name", "legal_name", "description",
    "founded_year", "employee_count", "emails", "phones", "address",
    "operating_regions", "products", "services", "social_profiles",
    "technologies",
]


def _lead_from(url: str, scraped_data: Dict[str, Any]) -> Lead:
    return Lead(company_name=scraped_data.get("company_name"), website=url)


async def run_one(scraper: TieredScraper, url: str, category: str) -> Dict[str, Any]:
    """Runs one site through all three pipeline stages, capturing full
    output and timing at each stage. Never raises -- any stage failure is
    recorded in the entry and the pipeline moves to the next site."""
    entry: Dict[str, Any] = {
        "url": url,
        "category": category,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # -- Stage 1: Scraper ------------------------------------------------
    stage_start = time.time()
    try:
        scrape_result = await scraper.scrape(url)
    except Exception as e:
        logger.error(f"Scraper stage raised for {url}: {e!r}")
        entry["scrape"] = {"success": False, "error": repr(e)}
        entry["error_stage"] = "scrape"
        return entry
    scrape_wall_time = time.time() - stage_start

    log_scraping_attempt(
        logger,
        url=url,
        method=str(scrape_result.method),
        success=scrape_result.success,
        confidence=scrape_result.confidence,
        processing_time=scrape_result.processing_time * 1000,
        error_message=scrape_result.error_message,
    )

    entry["scrape"] = {
        "success": scrape_result.success,
        "method": str(scrape_result.method),
        "confidence": scrape_result.confidence,
        "pages_scraped": scrape_result.pages_scraped,
        "blocked_detected": scrape_result.blocked_detected,
        "tiers_attempted": scrape_result.tiers_attempted,
        "error_message": scrape_result.error_message,
        "processing_time_s": scrape_result.processing_time,
        "wall_time_s": scrape_wall_time,
        "field_count": len(scrape_result.data),
        "data": scrape_result.data,
    }

    if not scrape_result.data:
        entry["error_stage"] = "scrape_returned_no_data"
        return entry

    # -- Stage 2: Normalizer ----------------------------------------------
    stage_start = time.time()
    try:
        normalized = normalize_scraped_fields(scrape_result.data)
    except Exception as e:
        logger.error(f"Normalizer stage raised for {url}: {e!r}")
        entry["normalized"] = {"error": repr(e)}
        entry["error_stage"] = "normalize"
        return entry
    entry["normalized"] = normalized
    entry["normalize_wall_time_s"] = time.time() - stage_start

    # -- Stage 3: Enrichment -----------------------------------------------
    stage_start = time.time()
    try:
        lead = _lead_from(url, scrape_result.data)
        enrichment_result = WaterfallEnricher().enrich_lead_data(lead, scrape_result.data)
    except Exception as e:
        logger.error(f"Enrichment stage raised for {url}: {e!r}")
        entry["enrichment"] = {"error": repr(e)}
        entry["error_stage"] = "enrich"
        return entry
    enrich_wall_time = time.time() - stage_start

    if enrichment_result is not None:
        log_enrichment_attempt(
            logger,
            lead_id=0,
            method=str(enrichment_result.method),
            success=enrichment_result.success,
            confidence=enrichment_result.confidence,
            processing_time=enrichment_result.processing_time,
        )
        entry["enrichment"] = {
            "success": enrichment_result.success,
            "method": str(enrichment_result.method),
            "confidence": enrichment_result.confidence,
            "processing_time_ms": enrichment_result.processing_time,
            "wall_time_s": enrich_wall_time,
            "data": enrichment_result.data,
        }
    else:
        entry["enrichment"] = None

    return entry


async def main():
    if len(sys.argv) > 1:
        sites = [(u, "cli_override") for u in sys.argv[1:]]
    else:
        sites = TEST_SITES

    results: List[Dict[str, Any]] = []
    try:
        async with TieredScraper() as scraper:
            for url, category in sites:
                print(f"Scraping {url} ({category})...")
                try:
                    entry = await run_one(scraper, url, category)
                except Exception as e:  # belt-and-suspenders; run_one shouldn't raise
                    print(f"  [FATAL] {url}: {e!r}")
                    entry = {"url": url, "category": category, "error_stage": "fatal", "error": repr(e)}
                results.append(entry)
                scrape_ok = entry.get("scrape", {}).get("success")
                has_enrichment = bool(entry.get("enrichment"))
                print(f"  scrape_success={scrape_ok} enrichment_produced={has_enrichment}")
    finally:
        await close_scraper_resources()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "site_count": len(results),
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    print(f"\nFull pipeline results written to: {OUTPUT_PATH}")

    _print_summary(results)


def _print_summary(results: List[Dict[str, Any]]) -> None:
    total = len(results)
    if total == 0:
        print("\nNo results to summarize.")
        return

    scrape_success = sum(1 for r in results if r.get("scrape", {}).get("success"))
    blocked = sum(1 for r in results if r.get("scrape", {}).get("blocked_detected"))
    enriched = sum(1 for r in results if r.get("enrichment"))
    errors = [r for r in results if r.get("error_stage")]

    print("\n" + "=" * 78)
    print("PIPELINE SUMMARY")
    print("=" * 78)
    print(f"Sites tested:                 {total}")
    print(f"Scrape success:               {scrape_success}/{total} ({scrape_success / total * 100:.0f}%)")
    print(f"Anti-bot / blocked detected:  {blocked}/{total}")
    print(f"Enrichment produced a result: {enriched}/{total} ({enriched / total * 100:.0f}%)")
    print(f"Sites that hit an error:      {len(errors)}/{total}")

    print("\n--- Normalized field completeness (extraction+normalization coverage) ---")
    for field_name in _CANONICAL_FIELDS_TO_TRACK:
        present = sum(
            1 for r in results
            if r.get("normalized") and isinstance(r["normalized"], dict) and r["normalized"].get(field_name)
        )
        pct = present / total * 100
        bar = "#" * int(pct / 5)
        print(f"  {field_name:<20} {present:>3}/{total:<3} ({pct:>3.0f}%) {bar}")

    scrape_confidences = [
        r["scrape"]["confidence"] for r in results if r.get("scrape", {}).get("success")
    ]
    enrich_confidences = [r["enrichment"]["confidence"] for r in results if r.get("enrichment")]

    print("\n--- Confidence distribution ---")
    if scrape_confidences:
        print(
            f"  Scrape confidence:     min={min(scrape_confidences):.2f}  "
            f"max={max(scrape_confidences):.2f}  "
            f"avg={sum(scrape_confidences) / len(scrape_confidences):.2f}"
        )
    if enrich_confidences:
        print(
            f"  Enrichment confidence: min={min(enrich_confidences):.2f}  "
            f"max={max(enrich_confidences):.2f}  "
            f"avg={sum(enrich_confidences) / len(enrich_confidences):.2f}"
        )

    specific_org_type = 0
    generic_org_type = 0
    for r in results:
        org_type = (r.get("normalized") or {}).get("organization_type")
        if not org_type:
            continue
        if org_type.get("confidence", 0) > 0.5:
            specific_org_type += 1
        else:
            generic_org_type += 1
    no_org_type = total - specific_org_type - generic_org_type
    print("\n--- Organization type resolution ---")
    print(f"  Specific schema.org type declared: {specific_org_type}/{total}")
    print(f"  Generic 'Organization' only:       {generic_org_type}/{total}")
    print(f"  No organization type at all:       {no_org_type}/{total}")

    if errors:
        print(f"\n--- {len(errors)} site(s) hit an error ---")
        for r in errors:
            err_detail = r.get("error") or r.get("scrape", {}).get("error_message") or "unknown"
            print(f"  {r['url']}: [{r['error_stage']}] {err_detail}")


if __name__ == "__main__":
    asyncio.run(main())