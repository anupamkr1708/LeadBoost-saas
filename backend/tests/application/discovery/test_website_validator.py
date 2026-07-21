import aiohttp
import pytest

from application.discovery.website_validator import WebsiteValidator, domain_of, is_rejected_domain
from tests.application.discovery.fakes import FakeResponse, FakeSession, FakeSessionRaises


def test_domain_of_strips_www_and_path():
    assert domain_of("https://www.nike.com/shoes?x=1") == "nike.com"
    assert domain_of("https://nike.com") == "nike.com"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.facebook.com/somebusiness",
        "https://www.instagram.com/somebusiness",
        "https://www.linkedin.com/company/somebusiness",
        "https://www.justdial.com/somebusiness",
        "https://www.indiamart.com/somebusiness",
    ],
)
def test_is_rejected_domain_true_for_directories_and_social(url):
    assert is_rejected_domain(url) is True


def test_is_rejected_domain_false_for_ordinary_site():
    assert is_rejected_domain("https://acmeshoes.example.com") is False


async def test_validate_empty_url():
    outcome = await WebsiteValidator().validate(None)
    assert outcome.ok is False
    assert outcome.reason == "empty_url"


async def test_validate_rejects_directory_domain_without_any_http_call(monkeypatch):
    called = {"n": 0}

    class ExplodingSession:
        def __init__(self, *a, **kw):
            called["n"] += 1

    monkeypatch.setattr(aiohttp, "ClientSession", ExplodingSession)

    outcome = await WebsiteValidator().validate("https://www.facebook.com/acme")
    assert outcome.ok is False
    assert outcome.reason == "directory_or_social_domain"
    assert called["n"] == 0  # short-circuited before any network call


async def test_validate_accepts_reachable_html_site(monkeypatch):
    fake_response = FakeResponse(status=200, headers={"Content-Type": "text/html; charset=utf-8"}, url="https://acmeshoes.example.com/")
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    outcome = await WebsiteValidator().validate("acmeshoes.example.com")
    assert outcome.ok is True
    assert outcome.normalized_url == "https://acmeshoes.example.com/"


async def test_validate_rejects_non_html_content_type(monkeypatch):
    fake_response = FakeResponse(status=200, headers={"Content-Type": "application/pdf"}, url="https://example.com/file.pdf")
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    outcome = await WebsiteValidator().validate("https://example.com/file.pdf")
    assert outcome.ok is False
    assert "non_html_content_type" in outcome.reason


async def test_validate_rejects_redirect_into_directory_domain(monkeypatch):
    fake_response = FakeResponse(
        status=200,
        headers={"Content-Type": "text/html"},
        url="https://www.facebook.com/some-parked-domain-redirect",
    )
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    outcome = await WebsiteValidator().validate("https://deadbusiness.example.com")
    assert outcome.ok is False
    assert outcome.reason == "redirected_to_directory_or_social_domain"


async def test_validate_handles_connection_error_gracefully(monkeypatch):
    monkeypatch.setattr(
        aiohttp, "ClientSession", lambda *a, **kw: FakeSessionRaises(aiohttp.ClientError("refused"))
    )

    outcome = await WebsiteValidator().validate("https://unreachable.example.com")
    assert outcome.ok is False
    assert "connection_error" in outcome.reason


async def test_validate_allows_directories_when_flag_set(monkeypatch):
    fake_response = FakeResponse(status=200, headers={"Content-Type": "text/html"}, url="https://www.facebook.com/acme")
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **kw: FakeSession(fake_response))

    validator = WebsiteValidator(allow_directories=True)
    outcome = await validator.validate("https://www.facebook.com/acme")
    assert outcome.ok is True
