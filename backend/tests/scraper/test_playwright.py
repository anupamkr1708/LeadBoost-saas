"""
Runs the REAL Playwright tier (actual Chromium, actual browser pool, actual
route interception) against a local fixture server. This sandbox has no
outbound internet, but Playwright + a local http.server together let us
exercise the full JS-rendering code path for real rather than mocking it.
"""
import asyncio
import http.server
import sys
import threading
import time

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


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    import os
    os.chdir("fixtures")
    srv = http.server.HTTPServer(("127.0.0.1", 8899), Handler)
    srv.serve_forever()


async def main():
    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.3)

    ts = S.TieredScraper(timeout=15)
    outcome = await ts._scrape_with_playwright("http://127.0.0.1:8899/spa_company.html")
    result = outcome.result

    print(f"  (debug) success={result.success} confidence={result.confidence:.2f} "
          f"error={result.error_message}")

    check("playwright tier reports success", result.success is True)
    check("waited for JS-rendered title (not the pre-hydration 'Loading...')",
          result.data.get("title") == "Nimbus Cloud Storage | Home", result.data.get("title"))
    check("post-render meta description captured",
          "encrypted cloud backup" in (result.data.get("meta_description") or ""),
          result.data.get("meta_description"))
    check("JSON-LD injected after hydration was still picked up (name)",
          result.data.get("name") == "Nimbus Cloud Storage", result.data.get("name"))
    check("JSON-LD founded_year picked up", result.data.get("founded_year") == 2018, result.data.get("founded_year"))
    check("sales_email via ContactPoint (post-hydration JSON-LD)",
          result.data.get("sales_email") == "sales@nimbuscloud.io", result.data.get("sales_email"))
    check("linkedin_url via sameAs (post-hydration JSON-LD)",
          "linkedin.com/company/nimbuscloud" in (result.data.get("linkedin_url") or ""))
    check("general email captured from rendered body text",
          result.data.get("email") == "hello@nimbuscloud.io", result.data.get("email"))
    check("general phone captured from rendered body text", bool(result.data.get("phone")), result.data.get("phone"))
    check("company_name derived", result.data.get("company_name") == "Nimbus Cloud Storage", result.data.get("company_name"))
    check("links collected via live DOM (post-render nav)",
          any("/about" in l for l in (result.data.get("links") or [])), result.data.get("links"))
    check("not flagged as blocked (normal page)", result.blocked_detected is False)
    check("anchors collected for enrichment discovery", len(outcome.anchors) >= 3, outcome.anchors)

    from scraper import get_browser_pool
    pool = await get_browser_pool()
    await pool.close()


asyncio.run(main())
print(f"\n{'='*60}\n{PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)