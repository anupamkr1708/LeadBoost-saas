"""
Run this in YOUR environment (with network access, and aiohttp / curl_cffi /
playwright / trafilatura installed) to sanity-check the upgraded scraper
against 10 real company websites.

    pip install aiohttp curl_cffi trafilatura playwright beautifulsoup4 lxml requests
    playwright install chromium
    python live_test_10_companies.py

This sandbox this scraper was built in has NO outbound network access, so
this specific script could not be executed here -- everything else (parsing
logic, tier-escalation control flow, and a real Playwright run against a
local fixture) was tested in this environment; this script is the one piece
that needs to run on your side against the live internet.
"""
import asyncio
import json
import time

from scraper import TieredScraper, get_scraper, close_scraper_resources  # noqa: E402

COMPANIES = [
    "https://stripe.com",
    "https://www.notion.so",
    "https://www.hubspot.com",
    "https://www.atlassian.com",
    "https://www.salesforce.com",
    "https://github.com",
    "https://www.mongodb.com",
    "https://www.cloudflare.com",
    "https://www.shopify.com",
    "https://www.zendesk.com",
]

# Fields worth eyeballing per company to judge extraction quality.
FIELDS_OF_INTEREST = [
    "company_name", "legal_name", "description", "industry",
    "founded_year", "employee_count", "tagline",
    "email", "sales_email", "support_email", "phone", "fax",
    "address", "city", "country",
    "linkedin_url", "twitter_url", "github_url", "youtube_url",
    "technologies", "products",
]


async def run_one(scraper: TieredScraper, url: str) -> dict:
    start = time.time()
    result = await scraper.scrape(url)
    elapsed = time.time() - start
    row = {
        "url": url,
        "success": result.success,
        "method": result.method.value,
        "confidence": round(result.confidence, 2),
        "pages_scraped": result.pages_scraped,
        "blocked_detected": result.blocked_detected,
        "tiers_attempted": result.tiers_attempted,
        "elapsed_s": round(elapsed, 1),
        "fields": {k: result.data.get(k) for k in FIELDS_OF_INTEREST if result.data.get(k)},
    }
    return row


async def main():
    scraper = await get_scraper()
    results = []
    for url in COMPANIES:
        print(f"Scraping {url} ...")
        try:
            row = await run_one(scraper, url)
        except Exception as e:
            row = {"url": url, "success": False, "error": str(e)}
        results.append(row)
        print(json.dumps(row, indent=2, default=str))
        print("-" * 70)

    await close_scraper_resources()

    ok = sum(1 for r in results if r.get("success"))
    print(f"\n{ok}/{len(results)} succeeded")
    avg_conf = sum(r.get("confidence", 0) for r in results) / len(results)
    print(f"Average confidence: {avg_conf:.2f}")

    with open("live_test_results.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print("Full results written to live_test_results.json")


if __name__ == "__main__":
    asyncio.run(main())