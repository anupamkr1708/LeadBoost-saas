"""
Tests for application.discovery.providers.serper_provider.

Covers SerperWebsiteResolver (single-business website resolution fallback)
and SerperBusinessSearchProvider (category+location discovery fallback,
used only when Overpass returns zero results for a startup-like
category). Every Serper HTTP response is mocked via
tests.application.discovery.fakes -- no real API calls.
"""

import aiohttp
import pytest

from application.discovery.providers import http_utils
from application.discovery.providers.serper_provider import (
    SerperBusinessSearchProvider,
    SerperWebsiteResolver,
    _is_acceptable_result,
    _looks_like_listicle,
    _score_result,
)
from application.discovery.website_resolver import WebsiteResolver
from application.discovery.website_validator import WebsiteValidator
from tests.application.discovery.fakes import FakeResponse, FakeSession, FakeSessionRaises


async def _instant_sleep(*args, **kwargs):
    return None


def _organic(link, title="", position=None):
    result = {"link": link, "title": title}
    if position is not None:
        result["position"] = position
    return result


# -- Configuration / graceful-missing-key behavior ---------------------------


async def test_returns_none_when_api_key_not_configured():
    resolver = SerperWebsiteResolver(api_key=None)
    assert resolver.is_configured() is False
    result = await resolver.resolve_website("Metro Shoes", "Mumbai")
    assert result is None


async def test_never_calls_network_when_api_key_missing(monkeypatch):
    called = {"n": 0}

    class ExplodingSession:
        def __init__(self, *a, **kw):
            called["n"] += 1

    monkeypatch.setattr(aiohttp, "ClientSession", ExplodingSession)

    resolver = SerperWebsiteResolver(api_key=None)
    await resolver.resolve_website("Metro Shoes", "Mumbai")
    assert called["n"] == 0


# -- Successful resolution ----------------------------------------------------


async def test_successful_resolution_picks_official_domain(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "organic": [
                {"link": "https://www.facebook.com/metroshoes", "title": "Metro Shoes | Facebook"},
                {"link": "https://en.wikipedia.org/wiki/Metro_Shoes", "title": "Metro Shoes - Wikipedia"},
                {"link": "https://www.metroshoes.com/", "title": "Metro Shoes - Official Site"},
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Metro Shoes", "Mumbai")

    assert result == "https://www.metroshoes.com/"


async def test_query_includes_city_when_provided(monkeypatch):
    captured = {}

    class _CapturingSession:
        def __init__(self, *a, **kw):
            pass

        def post(self, url, headers=None, json=None, **kw):
            captured["json"] = json
            return FakeResponse(status=200, json_data={"organic": []})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _CapturingSession)

    resolver = SerperWebsiteResolver(api_key="fake-key")
    await resolver.resolve_website("Metro Shoes", "Mumbai")

    assert captured["json"]["q"] == "Metro Shoes Mumbai official website"


async def test_query_omits_city_when_not_provided(monkeypatch):
    captured = {}

    class _CapturingSession:
        def __init__(self, *a, **kw):
            pass

        def post(self, url, headers=None, json=None, **kw):
            captured["json"] = json
            return FakeResponse(status=200, json_data={"organic": []})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", _CapturingSession)

    resolver = SerperWebsiteResolver(api_key="fake-key")
    await resolver.resolve_website("Metro Shoes", "")

    assert captured["json"]["q"] == "Metro Shoes official website"


async def test_prefers_root_domain_over_subpage(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "organic": [
                {"link": "https://www.apple.com/newsroom/", "title": "Apple Newsroom"},
                {"link": "https://www.apple.com/", "title": "Apple"},
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Apple", "Cupertino")

    assert result == "https://www.apple.com/"


# -- No results -----------------------------------------------------------------


async def test_no_organic_results_returns_none(monkeypatch):
    fake_response = FakeResponse(status=200, json_data={"organic": []})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Some Obscure Business", "Nowhere")

    assert result is None


async def test_missing_organic_key_returns_none(monkeypatch):
    fake_response = FakeResponse(status=200, json_data={})  # no "organic" key at all
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Some Business", "Somewhere")

    assert result is None


# -- Directory / social rejection -----------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/somebusiness",
        "https://www.instagram.com/somebusiness",
        "https://www.linkedin.com/company/somebusiness",
        "https://www.justdial.com/somebusiness",
        "https://www.sulekha.com/somebusiness",
        "https://en.wikipedia.org/wiki/Somebusiness",
        "https://github.com/somebusiness",
        "https://www.yelp.com/biz/somebusiness",
    ],
)
def test_is_acceptable_result_rejects_directories_social_and_wiki_github(url):
    assert _is_acceptable_result(url) is False


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/blog/our-story",
        "https://example.com/news/press-release",
        "https://example.com/forum/thread-1",
        "https://example.com/files/brochure.pdf",
    ],
)
def test_is_acceptable_result_rejects_articles_forums_and_pdfs(url):
    assert _is_acceptable_result(url) is False


def test_is_acceptable_result_accepts_ordinary_business_site():
    assert _is_acceptable_result("https://metroshoes.com/") is True


async def test_all_top5_results_rejected_returns_none(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "organic": [
                {"link": "https://www.facebook.com/x", "title": "x"},
                {"link": "https://en.wikipedia.org/wiki/x", "title": "x"},
                {"link": "https://www.justdial.com/x", "title": "x"},
                {"link": "https://github.com/x", "title": "x"},
                {"link": "https://example.com/news/x", "title": "x"},
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Some Business", "Some City")

    assert result is None


# -- Grounding: mandatory brand-relevance gate (regression tests for the -------
# -- exact real-world bugs application.discovery.grounding was written to fix,
# -- but which weren't actually caught until it was wired into this scorer) --


def test_score_result_rejects_unrelated_domain_despite_good_page_quality():
    """The exact real-world bug: an https, root-path result used to clear
    the old acceptance bar on generic quality signals alone, with zero
    brand relevance required. 'Regal' (a Mumbai shoe store) must not
    match regmovies.com (an unrelated US cinema chain) just because the
    page is clean."""
    assert (
        _score_result({"link": "https://www.regmovies.com/", "title": "Regal Cinemas"}, "Regal", 0, "Mumbai")
        is None
    )


def test_score_result_rejects_incidental_substring_match():
    """'walk' must not match 'walkoffame.com' just because it's a
    substring -- the whole reason grounding.brand_match_strength exists
    instead of a naive `in` check."""
    assert (
        _score_result(
            {"link": "https://walkoffame.com/", "title": "Hollywood Walk of Fame"},
            "Hollywood Walk of Shame",
            0,
            "Mumbai",
        )
        is None
    )


def test_score_result_accepts_genuine_brand_match():
    assert (
        _score_result({"link": "https://www.metroshoes.com/", "title": "Metro Shoes"}, "Metro Shoes", 0, "Mumbai")
        is not None
    )


def test_score_result_rejects_generic_category_word_as_sole_brand_evidence():
    """A domain that only shares a generic category word ('hospital') with
    the business name is not evidence it's *this* business's site --
    otherwise any hospital's domain would satisfy the gate for any other
    same-category business."""
    assert (
        _score_result(
            {"link": "https://www.cityhospitaldelhi.com/", "title": "City Hospital Delhi"},
            "Guru Gobind Singh Hospital",
            0,
            "Patna",
        )
        is None
    )


def test_score_result_accepts_real_match_with_location_corroboration():
    result = _score_result(
        {"link": "https://ggsinghhospitalpatna.com/", "title": "Guru Gobind Singh Hospital, Patna"},
        "Guru Gobind Singh Hospital",
        0,
        "Patna",
    )
    assert result is not None


def test_score_result_returns_none_for_rejected_domain():
    assert _score_result({"link": "https://www.facebook.com/acme", "title": ""}, "Acme", 0, "") is None


def test_score_result_returns_none_for_missing_link():
    assert _score_result({"title": "No link here"}, "Acme", 0, "") is None


def test_score_result_rewards_https_and_root_domain_only_after_brand_gate():
    """The generic https/root-path bonus still exists, but only ever
    applies once the mandatory brand-relevance gate has already passed --
    it's an ordering signal among genuine matches, not a substitute for
    one."""
    https_root = _score_result({"link": "https://acme.com/", "title": "Acme"}, "Acme", 0, "")
    http_subpage = _score_result({"link": "http://acme.com/about", "title": "Acme"}, "Acme", 0, "")
    assert https_root is not None and http_subpage is not None
    assert https_root > http_subpage


# -- Timeout / network failure ---------------------------------------------------


async def test_returns_none_on_timeout(monkeypatch):
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **kw: FakeSessionRaises(TimeoutError("timed out"))
    )
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Metro Shoes", "Mumbai")

    assert result is None  # never raises


async def test_returns_none_on_connection_error(monkeypatch):
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **kw: FakeSessionRaises(aiohttp.ClientError("refused"))
    )
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    resolver = SerperWebsiteResolver(api_key="fake-key")
    result = await resolver.resolve_website("Metro Shoes", "Mumbai")

    assert result is None


# -- HTTP error codes: 401 / 403 / 429 / 500 ------------------------------------


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
async def test_returns_none_on_http_error_status(monkeypatch, status_code):
    fake_response = FakeResponse(status=status_code, json_data={})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    resolver = SerperWebsiteResolver(api_key="wrong-or-limited-key")
    result = await resolver.resolve_website("Metro Shoes", "Mumbai")

    assert result is None  # never crashes Discovery regardless of status code


async def test_invalid_api_key_401_logs_clear_reason(monkeypatch, caplog):
    fake_response = FakeResponse(status=401, json_data={})
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    resolver = SerperWebsiteResolver(api_key="invalid-key")
    result = await resolver.resolve_website("Metro Shoes", "Mumbai")

    assert result is None


# -- Integration with the existing, unmodified website_validator.py -------------


async def test_website_resolver_rejects_serper_candidate_that_fails_validation(monkeypatch):
    """Serper resolves a candidate URL, but the existing website_validator
    rejects it (e.g. unreachable) -- the business must end up with no
    website, never a fabricated/unvalidated one. Exercises the real
    WebsiteResolver + real SerperWebsiteResolver + real WebsiteValidator
    together, with only the HTTP layer mocked."""
    from application.discovery.dto import BusinessCandidate

    serper_response = FakeResponse(
        status=200,
        json_data={"organic": [{"link": "https://metroshoes-real.com/", "title": "Metro Shoes"}]},
    )
    validator_failure_response = FakeResponse(status=404, json_data={})

    class _Router:
        def post(self, *a, **kw):
            return serper_response  # Serper's POST /search

        def get(self, *a, **kw):
            return validator_failure_response  # the validator's GET

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _Router())

    resolver = WebsiteResolver(WebsiteValidator(), fallback_provider=SerperWebsiteResolver(api_key="fake-key"))
    candidate = BusinessCandidate(name="Metro Shoes", website=None)

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website is None
    assert resolution.validated is False
    assert resolution.rejection_reason == "unreachable_http_404"


async def test_website_resolver_accepts_serper_candidate_that_passes_validation(monkeypatch):
    from application.discovery.dto import BusinessCandidate

    serper_response = FakeResponse(
        status=200,
        json_data={"organic": [{"link": "https://metroshoes-real.com/", "title": "Metro Shoes"}]},
    )
    validator_success_response = FakeResponse(
        status=200, headers={"Content-Type": "text/html"}, url="https://metroshoes-real.com/"
    )

    class _Router:
        def post(self, *a, **kw):
            return serper_response

        def get(self, *a, **kw):
            return validator_success_response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: _Router())

    resolver = WebsiteResolver(WebsiteValidator(), fallback_provider=SerperWebsiteResolver(api_key="fake-key"))
    candidate = BusinessCandidate(name="Metro Shoes", website=None)

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website == "https://metroshoes-real.com/"
    assert resolution.validated is True
    assert resolution.resolved_via == "serper"


# -- SerperBusinessSearchProvider (Part 4: Overpass-zero fallback) -------------


async def test_business_search_returns_none_configured_produces_empty_list(monkeypatch):
    provider = SerperBusinessSearchProvider(api_key=None)
    assert provider.is_configured() is False
    result = await provider.search("SaaS startups", "Noida", 10)
    assert result == []


async def test_business_search_never_calls_network_when_key_missing(monkeypatch):
    called = {"n": 0}

    class ExplodingSession:
        def __init__(self, *a, **kw):
            called["n"] += 1

    monkeypatch.setattr(aiohttp, "ClientSession", ExplodingSession)

    provider = SerperBusinessSearchProvider(api_key=None)
    await provider.search("SaaS startups", "Noida", 10)
    assert called["n"] == 0


async def test_business_search_builds_candidates_from_organic_results(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "organic": [
                {"link": "https://acmeai.example.com/", "title": "Acme AI | Automation Platform"},
                {"link": "https://www.facebook.com/somestartup", "title": "Some Startup | Facebook"},
                {"link": "https://betaworks.example.com/", "title": "BetaWorks - Home"},
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    provider = SerperBusinessSearchProvider(api_key="fake-key")
    candidates = await provider.search("AI automation startups", "Noida", 10)

    names = [c.name for c in candidates]
    websites = [c.website for c in candidates]
    assert "Acme AI" in names
    assert "BetaWorks" in names
    assert not any("facebook.com" in (w or "") for w in websites)
    assert all(c.category == "AI automation startups" for c in candidates)
    assert all(c.source == "serper_business_search" for c in candidates)


async def test_business_search_filters_out_listicle_titles():
    assert _looks_like_listicle("Top 10 AI Startups in Noida") is True
    assert _looks_like_listicle("Best 15 SaaS Companies to Watch") is True
    assert _looks_like_listicle("Acme AI - Home") is False


async def test_business_search_deduplicates_by_domain(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "organic": [
                {"link": "https://acmeai.example.com/", "title": "Acme AI"},
                {"link": "https://acmeai.example.com/about", "title": "Acme AI - About"},
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    provider = SerperBusinessSearchProvider(api_key="fake-key")
    candidates = await provider.search("AI automation startups", "Noida", 10)

    assert len(candidates) == 1


async def test_business_search_respects_limit(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        json_data={
            "organic": [
                {"link": f"https://company{i}.example.com/", "title": f"Company {i}"} for i in range(10)
            ]
        },
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    provider = SerperBusinessSearchProvider(api_key="fake-key")
    candidates = await provider.search("software companies", "Bangalore", 3)

    assert len(candidates) == 3


async def test_business_search_handles_network_failure_gracefully(monkeypatch):
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **kw: FakeSessionRaises(aiohttp.ClientError("down"))
    )
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    provider = SerperBusinessSearchProvider(api_key="fake-key")
    candidates = await provider.search("SaaS startups", "Noida", 10)

    assert candidates == []
