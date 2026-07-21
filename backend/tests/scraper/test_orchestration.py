"""
Tests the async orchestration logic in `scrape()` -- tier escalation,
short-circuiting on high confidence, and bounded-concurrency multi-page
enrichment -- by monkeypatching the tier methods with canned async
responses. This validates the control flow I rewrote (returning the new
internal `_TierOutcome` instead of the previous `(result, blocked)` tuple)
without needing real network access.
"""
import asyncio
import sys

sys.path.insert(0, "stubs")
sys.path.insert(0, ".")

import scraper as S  # noqa: E402

PASS = 0
FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  OK   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}  {detail}")


def outcome(method, confidence, success=True, blocked=False, data=None):
    return S._TierOutcome(
        result=S.ScrapingResult(
            success=success,
            data=data or {"title": "x"},
            method=method,
            confidence=confidence,
            processing_time=0.01,
        ),
        blocked=blocked,
    )


async def main():
    global PASS, FAIL

    # -----------------------------------------------------------------
    print("== High-confidence static tier short-circuits everything else ==")
    ts = S.TieredScraper()
    ts._robots.is_allowed = lambda *a, **k: _async_true()

    calls = {"static": 0, "curl": 0, "pw": 0, "enrich": 0, "fallback": 0}

    async def fake_static(url):
        calls["static"] += 1
        return outcome(S.ScrapingMethod.JSON_LD, 0.85, data={"title": "x", "email": "a@x.com"})

    async def fake_curl(url):
        calls["curl"] += 1
        return outcome(S.ScrapingMethod.CURL_CFFI, 0.5)

    async def fake_pw(url):
        calls["pw"] += 1
        return outcome(S.ScrapingMethod.PLAYWRIGHT, 0.5)

    async def fake_enrich(url, base):
        calls["enrich"] += 1
        return None

    async def fake_fallback(url):
        calls["fallback"] += 1
        return outcome(S.ScrapingMethod.REQUESTS, 0.1)

    ts._fetch_and_extract_static = fake_static
    ts._scrape_with_curl_cffi = fake_curl
    ts._scrape_with_playwright = fake_pw
    ts._enrich_from_subpages = fake_enrich
    ts._scrape_with_requests_fallback = fake_fallback

    result = await ts.scrape("https://example.com")
    check("static-only call made", calls["static"] == 1, calls)
    check("curl_cffi never invoked (short-circuit)", calls["curl"] == 0, calls)
    check("playwright never invoked (short-circuit)", calls["pw"] == 0, calls)
    check("final confidence matches static tier", result.confidence == 0.85, result.confidence)
    check("tiers_attempted only lists json_ld", result.tiers_attempted == ["json_ld"], result.tiers_attempted)
    check("public ScrapingResult type returned (not _TierOutcome)", isinstance(result, S.ScrapingResult))

    # -----------------------------------------------------------------
    print("\n== Weak static tier escalates through curl_cffi -> playwright -> enrichment ==")
    ts2 = S.TieredScraper()
    ts2._robots.is_allowed = lambda *a, **k: _async_true()
    calls2 = {"static": 0, "curl": 0, "pw": 0, "enrich": 0, "fallback": 0}

    async def weak_static(url):
        calls2["static"] += 1
        return outcome(S.ScrapingMethod.STRUCTURED_DATA, 0.3, data={"title": "x"})

    async def weak_curl(url):
        calls2["curl"] += 1
        return outcome(S.ScrapingMethod.CURL_CFFI, 0.4, data={"title": "x"})

    async def strong_pw(url):
        calls2["pw"] += 1
        return outcome(S.ScrapingMethod.PLAYWRIGHT, 0.6, data={"title": "x"})  # still < 0.9, thin data

    async def enrich_boost(url, base):
        calls2["enrich"] += 1
        return outcome(S.ScrapingMethod.MULTI_PAGE, 0.75, data={"title": "x", "email": "a@x.com"})

    async def unreached_fallback(url):
        calls2["fallback"] += 1
        return outcome(S.ScrapingMethod.REQUESTS, 0.05)

    ts2._fetch_and_extract_static = weak_static
    ts2._scrape_with_curl_cffi = weak_curl
    ts2._scrape_with_playwright = strong_pw
    ts2._enrich_from_subpages = enrich_boost
    ts2._scrape_with_requests_fallback = unreached_fallback

    result2 = await ts2.scrape("https://example.com")
    check("static tier called", calls2["static"] == 1)
    check("curl_cffi called (static was weak)", calls2["curl"] == 1)
    check("playwright called (still < 0.65 after curl)", calls2["pw"] == 1)
    check("enrichment called (thin data, confidence < 0.9)", calls2["enrich"] == 1)
    check("fallback NOT called (enrichment pushed confidence > 0.2)", calls2["fallback"] == 0, calls2)
    check("final result reflects enrichment tier", result2.method == S.ScrapingMethod.MULTI_PAGE)
    check("tiers_attempted records full escalation path",
          result2.tiers_attempted == ["structured_data", "curl_cffi", "playwright", "multi_page"],
          result2.tiers_attempted)

    # -----------------------------------------------------------------
    print("\n== Everything fails -> requests fallback is the last resort ==")
    ts3 = S.TieredScraper()
    ts3._robots.is_allowed = lambda *a, **k: _async_true()
    calls3 = {"fallback": 0}

    async def dead_static(url):
        return outcome(S.ScrapingMethod.STRUCTURED_DATA, 0.0, success=False, data={})

    async def dead_curl(url):
        return outcome(S.ScrapingMethod.CURL_CFFI, 0.0, success=False, data={})

    async def dead_pw(url):
        return outcome(S.ScrapingMethod.PLAYWRIGHT, 0.0, success=False, data={})

    async def rescue_fallback(url):
        calls3["fallback"] += 1
        return outcome(S.ScrapingMethod.REQUESTS, 0.4, data={"title": "rescued"})

    ts3._fetch_and_extract_static = dead_static
    ts3._scrape_with_curl_cffi = dead_curl
    ts3._scrape_with_playwright = dead_pw
    ts3._scrape_with_requests_fallback = rescue_fallback

    result3 = await ts3.scrape("https://example.com")
    check("fallback tier invoked when everything else fails", calls3["fallback"] == 1)
    check("fallback result wins", result3.data.get("title") == "rescued", result3.data)

    # -----------------------------------------------------------------
    print("\n== Bounded-concurrency multi-page enrichment ==")
    ts4 = S.TieredScraper()
    in_flight = {"current": 0, "max_seen": 0}
    fetched_urls = []

    async def fake_http_get(url):
        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        fetched_urls.append(url)
        await asyncio.sleep(0.02)  # simulate network latency
        in_flight["current"] -= 1
        html = f"<html><head><title>{url}</title></head><body><p>Contact us at hello@acme.com</p></body></html>"
        return html, 200, None

    async def fake_sitemap(base_url):
        return []

    ts4._http_get_with_retries = fake_http_get
    ts4._get_sitemap_urls = fake_sitemap

    import os
    os.environ["SCRAPER_MAX_PAGES"] = "6"
    os.environ["SCRAPER_ENRICHMENT_CONCURRENCY"] = "2"

    anchors = [
        ("https://acme.com/about-us", "About"),
        ("https://acme.com/contact", "Contact"),
        ("https://acme.com/team", "Team"),
        ("https://acme.com/careers", "Careers"),
        ("https://acme.com/pricing", "Pricing"),
    ]
    base = S._TierOutcome(
        result=S.ScrapingResult(
            success=True, data={"title": "Acme", "links": [a for a, _ in anchors]},
            method=S.ScrapingMethod.STRUCTURED_DATA, confidence=0.4, processing_time=0.0,
        ),
        blocked=False,
        anchors=anchors,
    )

    enriched = await ts4._enrich_from_subpages("https://acme.com", base)
    check("enrichment fetched multiple subpages", enriched is not None and enriched.result.pages_scraped > 1,
          enriched.result.pages_scraped if enriched else None)
    check("concurrency never exceeded configured limit (2)", in_flight["max_seen"] <= 2, in_flight["max_seen"])
    check("enrichment merged contact info from subpages", enriched.result.data.get("email") == "hello@acme.com")
    check("confidence improved after enrichment", enriched.result.confidence > 0.4, enriched.result.confidence)


def _async_true():
    async def _inner(*a, **k):
        return True
    return _inner()


asyncio.run(main())
print(f"\n{'='*60}\n{PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)