"""
Tests for core.infrastructure.scraping.scraper.TieredScraper.

Covers the pure parsing/heuristic logic without network I/O, plus one
local-server integration test exercising the retry/backoff and
block-detection behaviour end-to-end (see test_lead_pipeline.py in
tests/application for the full-pipeline-level scraper integration).
"""

import pytest
from bs4 import BeautifulSoup

from core.infrastructure.scraping.scraper import TieredScraper, _looks_blocked


@pytest.fixture()
def scraper():
    return TieredScraper()


SAMPLE_HTML = """
<html><head>
<title>Acme Robotics - Industrial Automation</title>
<meta name="description" content="Acme Robotics builds industrial automation solutions.">
<meta property="og:title" content="Acme Robotics">
<meta property="og:description" content="Leading industrial automation company">
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Organization","name":"Acme Robotics","description":"Industrial automation","email":"info@acme.com","foundingDate":"2015"}
</script>
</head>
<body>
<p>Contact us at <a href="mailto:sales@acme.com">sales@acme.com</a> or call <a href="tel:+14155551234">(415) 555-1234</a></p>
<a href="https://linkedin.com/company/acme">LinkedIn</a>
<a href="/about">About</a>
<a href="/contact">Contact</a>
</body></html>
"""

BLOCKED_HTML = """
<html><body><h1>Attention Required! | Cloudflare</h1>
<p>Please wait while we verify your browser before proceeding...</p></body></html>
"""


def test_looks_blocked_detects_status_codes():
    assert _looks_blocked(403, "") is True
    assert _looks_blocked(429, "") is True
    assert _looks_blocked(503, "") is True
    assert _looks_blocked(200, "") is False


def test_looks_blocked_detects_challenge_markers():
    assert _looks_blocked(200, BLOCKED_HTML) is True


def test_looks_blocked_allows_normal_content():
    assert _looks_blocked(200, SAMPLE_HTML) is False


def test_parse_json_ld_extracts_flattened_fields(scraper):
    """_parse_json_ld was replaced by a two-stage pipeline:
    _extract_json_ld_blocks() returns raw schema.org blocks (this test),
    and _semantic_from_jsonld_blocks() maps them into business fields
    (see test_semantic_from_jsonld_blocks_maps_organization_fields)."""
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    blocks = scraper._extract_json_ld_blocks(soup)
    assert len(blocks) == 1
    data = blocks[0]
    assert data["name"] == "Acme Robotics"
    assert data["email"] == "info@acme.com"
    assert data["foundingDate"] == "2015"


def test_semantic_from_jsonld_blocks_maps_organization_fields(scraper):
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    blocks = scraper._extract_json_ld_blocks(soup)
    semantic = scraper._semantic_from_jsonld_blocks(blocks)
    assert semantic["org_name"] == "Acme Robotics"
    assert semantic["description"] == "Industrial automation"
    assert semantic["founded_year"] == 2015


def test_parse_meta_extracts_title_and_og_tags(scraper):
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    data = scraper._parse_meta(soup, "https://acme.com")
    assert data["title"] == "Acme Robotics - Industrial Automation"
    assert data["og_title"] == "Acme Robotics"
    assert "https://acme.com/about" in data["links"]


def test_extract_contact_info_prefers_mailto_and_tel(scraper):
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    contact = scraper._extract_contact_info(soup, SAMPLE_HTML)
    assert contact["email"] == "sales@acme.com"
    assert contact["phone"] == "+14155551234"
    assert contact["linkedin_url"] == "https://linkedin.com/company/acme"


def test_confidence_scores_are_bounded(scraper):
    soup = BeautifulSoup(SAMPLE_HTML, "html.parser")
    blocks = scraper._extract_json_ld_blocks(soup)
    # Mirrors exactly how _parse_page() builds `jsonld_raw` -- the actual
    # input _calculate_json_ld_confidence receives at runtime -- rather
    # than the semantic dict, which uses different (renamed) keys.
    jsonld_raw = {}
    for block in blocks:
        jsonld_raw.update(scraper._flatten_json(block))
    meta = scraper._parse_meta(soup, "https://acme.com")
    assert 0.0 <= scraper._calculate_json_ld_confidence(jsonld_raw) <= 1.0
    assert 0.0 <= scraper._calculate_meta_confidence(meta) <= 1.0


def test_pick_priority_subpages_filters_to_same_domain_and_keywords(scraper):
    """_pick_priority_subpages(base_url, links) -> List[str] became
    _discover_candidate_pages(base_url, anchors, sitemap_urls, max_pages)
    -> List[(url, category)], scored via the weighted _PAGE_CATEGORIES
    table instead of a flat keyword allow-list. max_pages=2 keeps only
    the two highest-weighted categories here (about/contact, weight 1.0
    each), which reproduces the original expectation that the
    lower-weighted blog page and the cross-domain link don't survive."""
    anchors = [
        ("https://acme.com/about", "About Us"),
        ("https://acme.com/contact", "Contact"),
        ("https://acme.com/blog/post-1", "Blog Post"),
        ("https://other-domain.com/about", "About"),
    ]
    picked = scraper._discover_candidate_pages(
        "https://acme.com", anchors, sitemap_urls=[], max_pages=2
    )
    picked_urls = [url for url, _category in picked]
    assert "https://acme.com/about" in picked_urls
    assert "https://acme.com/contact" in picked_urls
    assert "https://other-domain.com/about" not in picked_urls
    assert "https://acme.com/blog/post-1" not in picked_urls


def test_normalize_url_adds_scheme(scraper):
    assert scraper._normalize_url("acme.com") == "https://acme.com"
    assert scraper._normalize_url("https://acme.com") == "https://acme.com"


# -- Local-server integration test ------------------------------------------


async def test_scrape_retries_on_503_then_succeeds():
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    attempts = {"n": 0}

    async def handler(request):
        attempts["n"] += 1
        if attempts["n"] < 2:
            return web.Response(status=503, text="Service Unavailable")
        return web.Response(
            status=200,
            text='<html><head><title>Retry Co</title>'
            '<meta name="description" content="We survived a 503 retry."></head>'
            "<body>Contact: hello@retryco.com</body></html>",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/retry", handler)

    async with TestClient(TestServer(app)) as client:
        async with TieredScraper(timeout=10, max_retries=2) as scraper:
            # Reuse the test client's own session so the request actually
            # reaches the in-process test server.
            scraper.session = client.session
            result = await scraper.scrape(str(client.make_url("/retry")))

    assert result.data.get("email") == "hello@retryco.com"
    assert attempts["n"] == 2
