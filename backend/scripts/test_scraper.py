"""
Robust test harness for TieredScraper -- v2, updated for the canonical-field
and confidence-scoring additions.

WHERE TO PUT THIS
-----------------
Drop this file at:  backend/scripts/test_scraper.py

HOW TO RUN
----------
From the `backend` directory:

    python -m scripts.test_scraper
    # or
    python scripts/test_scraper.py

Both work -- a sys.path shim below makes sure `core` is importable either
way. You can also pass URLs on the command line to override TEST_URLS:

    python -m scripts.test_scraper https://example.com https://another.com
"""

import asyncio
import json
import sys
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from core.infrastructure.scraping.scraper import (  # noqa: E402
    ScrapingResult,
    TieredScraper,
    close_scraper_resources,
)

# ============================================================================
# EDIT THIS: sites to test.
# ============================================================================
TEST_URLS = [
    "https://mochishoes.com",
    "https://www.python.org",
    "https://stripe.com",
]

# Show what each individual tier (static/curl_cffi/playwright/requests)
# extracts on its own, bypassing escalation short-circuiting. Slower.
RUN_INDIVIDUAL_TIER_TESTS = False

OUTPUT_JSON_PATH = _BACKEND_ROOT / "scripts" / "scrape_results.json"
_TRUNCATE_AT = 300

# Canonical fields introduced by the scraper improvements -- shown in their
# own section, separately from the full raw dump, since they're the fields
# most consumers should read going forward.
_CANONICAL_FIELDS = [
    "brand_name", "legal_name", "founding_year", "website_title",
    "website_description", "headquarters", "emails", "phones", "locations",
    "linkedin", "facebook", "instagram", "twitter", "youtube",
    "github", "pinterest", "crunchbase", "glassdoor",
]

_LEGACY_CHECKLIST = [
    "company_name", "title", "description", "email", "phone",
    "linkedin_url", "twitter_url", "facebook_url", "address", "city",
    "state", "country", "postal_code", "founded_year", "employee_count",
    "technologies",
]


def _format_value(key: str, value, limit: int = _TRUNCATE_AT) -> str:
    if key == "links" and isinstance(value, list):
        preview = "\n".join(f"    - {u}" for u in value[:5])
        more = f"\n    ... and {len(value) - 5} more" if len(value) > 5 else ""
        return f"{len(value)} links found\n{preview}{more}"
    if isinstance(value, str):
        s = value
    else:
        try:
            s = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            s = str(value)
    return s if len(s) <= limit else s[:limit] + f"... [truncated, {len(s)} chars total]"


def print_result(url: str, result: ScrapingResult, wall_time: float) -> None:
    data = result.data

    print("\n" + "=" * 80)
    print(f"URL:            {url}")
    print(f"Success:        {result.success}")
    print(f"Method:         {result.method}")
    print(f"Confidence:     {result.confidence:.3f}")
    print(f"Pages scraped:  {result.pages_scraped}")
    print(f"Blocked:        {result.blocked_detected}")
    print(f"Tiers used:     {' -> '.join(result.tiers_attempted) or '(none)'}")
    print(f"Time:           reported {result.processing_time:.2f}s | wall {wall_time:.2f}s")
    if result.error_message:
        print(f"Error:          {result.error_message}")

    print("-" * 80)
    print("CANONICAL FIELDS")
    print("-" * 80)
    any_canonical = False
    for field_name in _CANONICAL_FIELDS:
        if field_name in data:
            any_canonical = True
            print(f"  {field_name}: {_format_value(field_name, data[field_name])}")
    if not any_canonical:
        print("  (none populated)")

    print("-" * 80)
    print("FIELD CONFIDENCE (deterministic, source-based)")
    print("-" * 80)
    confidence = data.get("field_confidence") or {}
    if confidence:
        for field_name, score in sorted(confidence.items(), key=lambda kv: -kv[1]):
            bar = "#" * int(score * 20)
            print(f"  {field_name:<18} {score:.2f} {bar}")
    else:
        print("  (no confidence data -- nothing extracted, or pre-canonical result)")

    print("-" * 80)
    print("LEGACY FIELD CHECKLIST (unchanged field names, for backward-compat sanity)")
    print("-" * 80)
    for field_name in _LEGACY_CHECKLIST:
        mark = "\u2713" if data.get(field_name) else "\u2717"
        print(f"  [{mark}] {field_name}")

    print("-" * 80)
    print("FULL EXTRACTED FIELDS")
    print("-" * 80)
    if not data:
        print("  (no data extracted)")
        return
    for key in sorted(data.keys()):
        print(f"{key}: {_format_value(key, data[key])}")


async def run_one(scraper: TieredScraper, url: str) -> ScrapingResult:
    start = time.time()
    result = await scraper.scrape(url)
    elapsed = time.time() - start
    print_result(url, result, elapsed)
    return result


async def test_individual_tiers(scraper: TieredScraper, url: str) -> None:
    print("\n" + "#" * 80)
    print(f"# INDIVIDUAL TIER BREAKDOWN: {url}")
    print("#" * 80)

    normalized = scraper._normalize_url(url)
    tiers = [
        ("Tier 1/2: static fetch", scraper._fetch_and_extract_static),
        ("Tier 3: curl_cffi impersonation", scraper._scrape_with_curl_cffi),
        ("Tier 4: playwright", scraper._scrape_with_playwright),
        ("Tier 6: requests fallback", scraper._scrape_with_requests_fallback),
    ]
    for label, fn in tiers:
        start = time.time()
        try:
            outcome = await fn(normalized)
        except Exception as e:
            print(f"\n--- {label} ---\n  raised: {e!r}")
            continue
        elapsed = time.time() - start
        r = outcome.result
        print(f"\n--- {label} ({elapsed:.2f}s) ---")
        print(f"  success={r.success} confidence={r.confidence:.3f} "
              f"blocked={outcome.blocked} error={r.error_message}")


async def main():
    urls = sys.argv[1:] if len(sys.argv) > 1 else TEST_URLS
    results: dict[str, ScrapingResult] = {}

    try:
        async with TieredScraper() as scraper:
            for url in urls:
                try:
                    results[url] = await run_one(scraper, url)
                except Exception as e:
                    print(f"\n[FATAL] Unhandled exception scraping {url}: {e!r}")
                    continue
                if RUN_INDIVIDUAL_TIER_TESTS:
                    try:
                        await test_individual_tiers(scraper, url)
                    except Exception as e:
                        print(f"[WARN] Individual tier test failed for {url}: {e!r}")
    finally:
        await close_scraper_resources()

    if not results:
        print("\nNo results collected.")
        return

    serializable = {
        url: {
            "success": r.success,
            "method": r.method.value if hasattr(r.method, "value") else str(r.method),
            "confidence": r.confidence,
            "processing_time": r.processing_time,
            "pages_scraped": r.pages_scraped,
            "blocked_detected": r.blocked_detected,
            "tiers_attempted": r.tiers_attempted,
            "error_message": r.error_message,
            "data": r.data,
        }
        for url, r in results.items()
    }
    OUTPUT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON_PATH.write_text(json.dumps(serializable, indent=2, ensure_ascii=False, default=str))
    print(f"\nFull (untruncated) results written to: {OUTPUT_JSON_PATH}")

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'URL':<36} {'Method':<16} {'Conf':<6} {'Pages':<6} {'Emails':<8} {'Phones'}")
    for url, r in results.items():
        print(
            f"{url:<36} {str(r.method):<16} {r.confidence:<6.2f} "
            f"{r.pages_scraped:<6} {len(r.data.get('emails', [])):<8} "
            f"{len(r.data.get('phones', []))}"
        )


if __name__ == "__main__":
    asyncio.run(main())