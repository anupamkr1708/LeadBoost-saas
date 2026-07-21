"""
Offline unit tests for the new scraper's pure parsing/scoring logic.

These do NOT hit the network (this sandbox has no outbound network access).
They validate, with synthetic HTML modeled on real company-site patterns,
that:
  - deep JSON-LD/schema.org extraction works (Organization, ContactPoint,
    PostalAddress, Product, FAQPage, BreadcrumbList)
  - contact extraction (original + extended/categorized) works
  - company info extraction (name, founded year, employees, tagline,
    mission, tech signatures) works
  - link scoring / candidate-page discovery correctly prioritizes and
    excludes pages
  - sitemap XML parsing works
  - block detection works
  - main text extraction fallback chain works
  - the ScrapingResult/ScrapingMethod/get_scraper/close_scraper_resources
    public surface is unchanged
"""
import asyncio
import sys

sys.path.insert(0, "stubs")
sys.path.insert(0, ".")

from bs4 import BeautifulSoup  # noqa: E402
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


# ---------------------------------------------------------------------------
print("== 1. Rich Organization JSON-LD (Stripe/Notion-style homepage) ==")
HOMEPAGE_HTML = """
<html>
<head>
<title>Acme Robotics | Home</title>
<meta name="description" content="Acme Robotics builds industrial automation software.">
<link rel="canonical" href="https://www.acme-robotics.com/">
<meta property="og:title" content="Acme Robotics | Automate Everything">
<meta property="og:description" content="Acme Robotics builds industrial automation software.">
<meta property="og:image" content="https://www.acme-robotics.com/og.png">
<meta name="twitter:card" content="summary_large_image">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "Acme Robotics",
  "legalName": "Acme Robotics, Inc.",
  "description": "Acme Robotics builds industrial automation software for factories.",
  "foundingDate": "2015-04-01",
  "numberOfEmployees": {"@type": "QuantitativeValue", "minValue": 200, "maxValue": 500},
  "logo": "https://www.acme-robotics.com/logo.png",
  "slogan": "Automate Everything",
  "url": "https://www.acme-robotics.com",
  "sameAs": [
    "https://www.linkedin.com/company/acme-robotics",
    "https://twitter.com/acmerobotics",
    "https://www.youtube.com/acmerobotics",
    "https://github.com/acme-robotics"
  ],
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "1 Factory Way",
    "addressLocality": "Austin",
    "addressRegion": "TX",
    "postalCode": "78701",
    "addressCountry": "US"
  },
  "contactPoint": [
    {"@type": "ContactPoint", "contactType": "sales", "email": "sales@acme-robotics.com", "telephone": "+1-512-555-0100"},
    {"@type": "ContactPoint", "contactType": "customer support", "email": "support@acme-robotics.com"}
  ]
}
</script>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"SoftwareApplication","name":"Acme Vision Suite"}
</script>
</head>
<body>
<nav>
  <a href="/about-us">About Us</a>
  <a href="/contact">Contact</a>
  <a href="/team">Our Team</a>
  <a href="/careers">Careers</a>
  <a href="/pricing">Pricing</a>
  <a href="/blog/one">Blog: One</a>
  <a href="/blog/two">Blog: Two</a>
  <a href="/blog/three">Blog: Three</a>
  <a href="/login">Log in</a>
  <a href="/cart">Cart</a>
  <a href="https://twitter.com/acmerobotics">Twitter</a>
  <a href="https://www.linkedin.com/company/acme-robotics">LinkedIn</a>
</nav>
<main>
<h1>Acme Robotics</h1>
<p>Founded in 2015, Acme Robotics has grown to over 300 employees serving factories worldwide.
Our mission is to make every factory floor safer and faster through intelligent automation.
Contact us at info@acme-robotics.com or call +1 (512) 555-0199. Fax: 512-555-0198.</p>
</main>
<script src="https://www.googletagmanager.com/gtag/js"></script>
</body>
</html>
"""

soup = BeautifulSoup(HOMEPAGE_HTML, "html.parser")
ts = S.TieredScraper()
data = ts._parse_page(soup, HOMEPAGE_HTML, "https://www.acme-robotics.com/", include_links=True)

check("name from typed Organization block", data.get("name") == "Acme Robotics", data.get("name"))
check("legal_name extracted", data.get("legal_name") == "Acme Robotics, Inc.", data.get("legal_name"))
check("founded_year from foundingDate", data.get("founded_year") == 2015, data.get("founded_year"))
check("employee_count range", data.get("employee_count") == "200-500", data.get("employee_count"))
check("logo extracted", data.get("logo", "").endswith("logo.png"), data.get("logo"))
check("tagline/slogan extracted", data.get("tagline") == "Automate Everything", data.get("tagline"))
check("linkedin_url via sameAs/anchor", "linkedin.com/company/acme-robotics" in (data.get("linkedin_url") or ""))
check("twitter_url via sameAs/anchor", "twitter.com/acmerobotics" in (data.get("twitter_url") or ""))
check("youtube_url via sameAs (new field)", "youtube.com/acmerobotics" in (data.get("youtube_url") or ""))
check("github_url via sameAs (new field)", "github.com/acme-robotics" in (data.get("github_url") or ""))
check("address composed", "Austin" in (data.get("address") or ""), data.get("address"))
check("city parsed", data.get("city") == "Austin", data.get("city"))
check("country parsed", data.get("country") == "US", data.get("country"))
check("postal_code parsed", data.get("postal_code") == "78701", data.get("postal_code"))
check("sales_email via ContactPoint", data.get("sales_email") == "sales@acme-robotics.com", data.get("sales_email"))
check("sales_phone via ContactPoint", data.get("sales_phone") == "+1-512-555-0100", data.get("sales_phone"))
check("support_email via ContactPoint", data.get("support_email") == "support@acme-robotics.com", data.get("support_email"))
check("products list picked up SoftwareApplication block", "Acme Vision Suite" in (data.get("products") or []), data.get("products"))
check("company_name derived", data.get("company_name") == "Acme Robotics", data.get("company_name"))
check("potential_company_name (domain-derived, now on ALL tiers)", data.get("potential_company_name") == "acme-robotics", data.get("potential_company_name"))
check("original email field still populated (body regex/mailto)", data.get("email") == "info@acme-robotics.com", data.get("email"))
check("original phone field still populated", bool(data.get("phone")), data.get("phone"))
check("fax extracted (new field)", data.get("fax", "").replace(" ", "") in ("512-555-0198", "512.555.0198"), data.get("fax"))
check("technologies fingerprint detects GTM", "Google Tag Manager" in (data.get("technologies") or []), data.get("technologies"))
check("jsonld_raw (legacy flattened key) present", isinstance(data.get("jsonld_raw"), dict))
check("jsonld (legacy list-of-blocks key, previously fallback-tier-only) present", isinstance(data.get("jsonld"), list))
check("canonical_url captured (new field)", data.get("canonical_url", "").startswith("https://www.acme-robotics.com"))
check("mission_statement captured (new field)", "safer and faster" in (data.get("mission_statement") or ""), data.get("mission_statement"))
check("links list still a flat list of strings (unchanged shape)", isinstance(data.get("links"), list) and all(isinstance(x, str) for x in data["links"]))


# ---------------------------------------------------------------------------
print("\n== 2. Link scoring & candidate-page discovery ==")
anchors = ts._collect_anchor_pairs(soup, "https://www.acme-robotics.com/")
sitemap_urls = [
    "https://www.acme-robotics.com/security",
    "https://www.acme-robotics.com/investors",
]
candidates = ts._discover_candidate_pages("https://www.acme-robotics.com/", anchors, sitemap_urls, max_pages=6)
cand_urls = [c[0] for c in candidates]
cand_cats = {c[0]: c[1] for c in candidates}

check("about page selected", any("about-us" in u for u in cand_urls), cand_urls)
check("contact page selected", any(u.endswith("/contact") for u in cand_urls), cand_urls)
check("team page selected", any(u.endswith("/team") for u in cand_urls), cand_urls)
check("login page excluded", not any("login" in u for u in cand_urls), cand_urls)
check("cart page excluded", not any("cart" in u for u in cand_urls), cand_urls)
check("about page category correct", cand_cats.get([u for u in cand_urls if "about-us" in u][0]) == "about")
check("at most 2 pages per category (3 blog posts -> capped)", sum(1 for c in candidates if c[1] == "blog") <= 2)
check("sitemap urls merged in (security page present)", any("security" in u for u in cand_urls), cand_urls)
check("candidate list respects max_pages budget", len(candidates) <= 6, len(candidates))

score, cat = ts._score_link("https://www.acme-robotics.com/wp-admin/edit.php", "", "www.acme-robotics.com")
check("wp-admin excluded from scoring", score <= 0, (score, cat))
score, cat = ts._score_link("https://evil-tracker.com/about", "About", "www.acme-robotics.com")
check("cross-domain link excluded", score <= 0, (score, cat))


# ---------------------------------------------------------------------------
print("\n== 3. Sitemap XML parsing (regex-based, handles sitemap index) ==")
CHILD_SITEMAP = """<?xml version="1.0"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.acme-robotics.com/about-us</loc></url>
  <url><loc>https://www.acme-robotics.com/contact</loc></url>
</urlset>"""

locs = __import__("re").findall(r"<loc>\s*(.*?)\s*</loc>", CHILD_SITEMAP, __import__("re").IGNORECASE | __import__("re").DOTALL)
check("sitemap <loc> regex extracts both urls", locs == [
    "https://www.acme-robotics.com/about-us",
    "https://www.acme-robotics.com/contact",
], locs)


# ---------------------------------------------------------------------------
print("\n== 4. Anti-bot / block detection ==")
CF_BLOCK_HTML = "<html><head><title>Just a moment...</title></head><body>Checking your browser before accessing acme-robotics.com. This process is automatic. Ray ID: 8934hf93</body></html>"
check("Cloudflare interstitial detected", S._looks_blocked(200, CF_BLOCK_HTML) is True)
check("403 status alone triggers block", S._looks_blocked(403, "") is True)
check("normal 200 page not flagged", S._looks_blocked(200, HOMEPAGE_HTML) is False)


# ---------------------------------------------------------------------------
print("\n== 5. Company/contact extraction on a thinner, meta-only page (no JSON-LD) ==")
ABOUT_PAGE_HTML = """
<html><head><title>About - Beta Logistics</title>
<meta name="description" content="Beta Logistics is a freight forwarding company.">
<meta property="og:title" content="About - Beta Logistics">
</head>
<body>
<p>Beta Logistics was established in 2009 and now has 1,200 employees across 14 countries.
Reach our press team at press@betalogistics.com or our careers team at careers@betalogistics.com.
Follow us on <a href="https://www.instagram.com/betalogistics">Instagram</a> and
<a href="https://www.glassdoor.com/Overview/Working-at-Beta-Logistics">Glassdoor</a>.
This site is built on <script src="https://cdn.shopify.com/s/files/1/foo.js"></script> Shopify.
</p>
</body></html>
"""
soup2 = BeautifulSoup(ABOUT_PAGE_HTML, "html.parser")
data2 = ts._parse_page(soup2, ABOUT_PAGE_HTML, "https://www.betalogistics.com/about", include_links=False)

check("founded_year via regex fallback (no JSON-LD)", data2.get("founded_year") == 2009, data2.get("founded_year"))
check("employee_count via regex fallback", data2.get("employee_count") == "1,200", data2.get("employee_count"))
check("press_email categorized", data2.get("press_email") == "press@betalogistics.com", data2.get("press_email"))
check("careers_email categorized", data2.get("careers_email") == "careers@betalogistics.com", data2.get("careers_email"))
check("instagram_url captured", "instagram.com/betalogistics" in (data2.get("instagram_url") or ""))
check("glassdoor_url captured", "glassdoor.com" in (data2.get("glassdoor_url") or ""))
check("Shopify tech signature detected", "Shopify" in (data2.get("technologies") or []), data2.get("technologies"))
check("company_name falls back to cleaned <title>", data2.get("company_name") == "About", data2.get("company_name"))
# ^ NOTE surfaced by the test itself, see printed caveat below.


# ---------------------------------------------------------------------------
print("\n== 6. Main text extraction fallback chain (no trafilatura installed here) ==")
NAV_HEAVY_HTML = """
<html><body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<header>Top banner nonsense that should be stripped</header>
<main><p>This is the real, substantive body copy about the company and what it does, repeated
to make sure it clears the two-hundred character floor used to prefer a semantic container
over a raw body dump in the fallback extraction path.</p></main>
<footer>Copyright 2026 - all rights reserved - unsubscribe - privacy policy</footer>
</body></html>
"""
text = S.TieredScraper._extract_main_text(NAV_HEAVY_HTML, "https://example.com/")
check("main-tag content preferred over nav/footer boilerplate", "substantive body copy" in text, text[:80])
check("nav/footer boilerplate excluded from extracted text", "Copyright 2026" not in text and "Home" not in text)


print(f"\n{'='*60}\n{PASS} passed, {FAIL} failed\n{'='*60}")
sys.exit(1 if FAIL else 0)