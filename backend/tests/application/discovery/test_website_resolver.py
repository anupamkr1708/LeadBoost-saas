from application.discovery.dto import BusinessCandidate
from application.discovery.website_resolver import WebsiteResolver
from application.discovery.website_validator import ValidationOutcome


class _StubValidator:
    def __init__(self, outcomes: dict):
        # outcomes: url -> ValidationOutcome
        self._outcomes = outcomes

    async def validate(self, url):
        return self._outcomes.get(url, ValidationOutcome(ok=False, reason="not_stubbed"))


class _StubFallback:
    name = "brave"

    def __init__(self, url_to_return):
        self._url = url_to_return

    async def resolve_website(self, business_name, location):
        return self._url


async def test_uses_overpass_website_when_valid():
    candidate = BusinessCandidate(name="Acme Shoes", website="https://acmeshoes.com")
    validator = _StubValidator({"https://acmeshoes.com": ValidationOutcome(ok=True, normalized_url="https://acmeshoes.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=_StubFallback("https://should-not-be-used.com"))

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website == "https://acmeshoes.com/"
    assert resolution.resolved_via == "overpass"
    assert resolution.validated is True


async def test_falls_back_to_brave_when_overpass_website_invalid():
    candidate = BusinessCandidate(name="Acme Shoes", website="https://dead-domain.example.com")
    validator = _StubValidator(
        {
            "https://dead-domain.example.com": ValidationOutcome(ok=False, reason="dns_resolution_failed"),
            "https://acmeshoes-real.com": ValidationOutcome(ok=True, normalized_url="https://acmeshoes-real.com/"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=_StubFallback("https://acmeshoes-real.com"))

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website == "https://acmeshoes-real.com/"
    assert resolution.resolved_via == "brave"


async def test_falls_back_to_brave_when_no_overpass_website():
    candidate = BusinessCandidate(name="Acme Shoes", website=None)
    validator = _StubValidator(
        {"https://acmeshoes-real.com": ValidationOutcome(ok=True, normalized_url="https://acmeshoes-real.com/")}
    )
    resolver = WebsiteResolver(validator, fallback_provider=_StubFallback("https://acmeshoes-real.com"))

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website == "https://acmeshoes-real.com/"
    assert resolution.resolved_via == "brave"


async def test_never_fabricates_a_website_when_nothing_resolves():
    candidate = BusinessCandidate(name="Acme Shoes", website=None)
    validator = _StubValidator({})
    resolver = WebsiteResolver(validator, fallback_provider=_StubFallback(None))

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website is None
    assert resolution.resolved_via == "none"
    assert resolution.validated is False
    assert resolution.rejection_reason == "no_verified_website"


async def test_never_fabricates_when_no_fallback_provider_configured():
    candidate = BusinessCandidate(name="Acme Shoes", website=None)
    validator = _StubValidator({})
    resolver = WebsiteResolver(validator, fallback_provider=None)

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website is None
    assert resolution.resolved_via == "none"


async def test_brave_candidate_that_fails_validation_is_not_used():
    candidate = BusinessCandidate(name="Acme Shoes", website=None)
    validator = _StubValidator(
        {"https://fake-candidate.com": ValidationOutcome(ok=False, reason="unreachable_http_404")}
    )
    resolver = WebsiteResolver(validator, fallback_provider=_StubFallback("https://fake-candidate.com"))

    resolution = await resolver.resolve(candidate, "Mumbai")

    assert resolution.website is None
    assert resolution.rejection_reason == "unreachable_http_404"
