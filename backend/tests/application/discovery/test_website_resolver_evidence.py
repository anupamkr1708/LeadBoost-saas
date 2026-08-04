"""
Tests for the internally rewired application.discovery.website_resolver.

Two things are being verified, deliberately kept separate:

  1. Regression: WebsiteResolver.resolve()'s return value is byte-for-byte
     identical to the pre-Sprint-1 implementation for every branch
     (overpass passes, overpass fails + fallback passes, overpass fails +
     fallback fails, overpass fails + no fallback candidate, no fallback
     provider at all) -- the Evidence/CandidatePool machinery must be
     provably a pure internal addition, not a behavior change.
  2. New capability: the internal CandidatePool this call builds actually
     contains the right candidates with non-trivial, explainable
     confidence -- proof the "foundation" is real, not decorative.

Fake provider/validator doubles are used instead of the real
aiohttp-backed OverpassProvider/SerperWebsiteResolver/WebsiteValidator so
these run with no network access and no external services, per "everything
must be testable."
"""

import asyncio

import pytest

from application.discovery.dto import BusinessCandidate, WebsiteResolution
from application.discovery.evidence import EvidenceType
from application.discovery.website_resolver import WebsiteResolver


class FakeValidator:
    """Stands in for WebsiteValidator: a fixed url -> ValidationOutcome
    table, no network calls."""

    def __init__(self, outcomes: dict):
        self._outcomes = outcomes

    async def validate(self, url):
        return self._outcomes[url]


class FakeFallbackProvider:
    name = "serper"

    def __init__(self, resolved_url):
        self._resolved_url = resolved_url

    async def resolve_website(self, business_name, location):
        return self._resolved_url


class Outcome:
    """Minimal stand-in matching website_validator.ValidationOutcome's
    shape (ok / normalized_url / reason) without importing aiohttp."""

    def __init__(self, ok, normalized_url=None, reason=None):
        self.ok = ok
        self.normalized_url = normalized_url
        self.reason = reason


def _run(coro):
    return asyncio.run(coro)


def _candidate(name="Nike India", website=None, address=None, phone=None):
    return BusinessCandidate(name=name, website=website, address=address, phone=phone)


# -- Regression: identical WebsiteResolution for every branch --------------


def test_overpass_website_passes_validation_unchanged_output():
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://should-not-be-called.com"))
    candidate = _candidate(website="https://nike.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website == "https://nike.com/"
    assert result.resolved_via == "overpass"
    assert result.validated is True
    assert result.rejection_reason is None


def test_overpass_fails_fallback_passes_unchanged_output():
    validator = FakeValidator(
        {
            "https://nike-typo.com": Outcome(False, reason="unreachable_http_404"),
            "https://nike.com": Outcome(True, "https://nike.com/"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike.com"))
    candidate = _candidate(website="https://nike-typo.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website == "https://nike.com/"
    assert result.resolved_via == "serper"
    assert result.validated is True
    assert result.rejection_reason is None


def test_overpass_fails_fallback_also_fails_unchanged_output():
    validator = FakeValidator(
        {
            "https://nike-typo.com": Outcome(False, reason="unreachable_http_404"),
            "https://nike-wrong.com": Outcome(False, reason="dns_resolution_failed"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike-wrong.com"))
    candidate = _candidate(website="https://nike-typo.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website is None
    assert result.resolved_via == "none"
    assert result.validated is False
    assert result.rejection_reason == "dns_resolution_failed"


def test_overpass_fails_fallback_finds_nothing_unchanged_output():
    validator = FakeValidator({"https://nike-typo.com": Outcome(False, reason="unreachable_http_404")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider(None))
    candidate = _candidate(website="https://nike-typo.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website is None
    assert result.resolved_via == "none"
    assert result.validated is False
    assert result.rejection_reason == "no_verified_website"


def test_no_website_no_fallback_provider_unchanged_output():
    resolver = WebsiteResolver(FakeValidator({}), fallback_provider=None)
    candidate = _candidate(website=None)

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website is None
    assert result.resolved_via == "none"
    assert result.validated is False
    assert result.rejection_reason == "no_verified_website"


def test_no_website_fallback_resolves_and_validates_unchanged_output():
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike.com"))
    candidate = _candidate(website=None)

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website == "https://nike.com/"
    assert result.resolved_via == "serper"
    assert result.validated is True


# -- New capability: the internal pool actually captures real evidence -----


def test_agreement_evidence_fires_when_failed_overpass_and_fallback_share_a_domain():
    """Overpass's website failed the *reachability* check (transient), but
    Serper independently converges on the exact same domain -- this is
    exactly the scenario provider agreement exists to reward, and it can
    only be exercised when the earlier, failed candidate is still in the
    pool for the later one to compare against."""
    validator = FakeValidator(
        {
            "https://nike.com": Outcome(False, reason="request_timed_out"),
            "https://nike.com/about": Outcome(True, "https://nike.com/"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike.com/about"))
    candidate = _candidate(name="Nike", website="https://nike.com")

    captured = {}
    original_attach = resolver._attach_candidate_evidence

    def spy(pool, website_candidate, business, location, outcome):
        original_attach(pool, website_candidate, business, location, outcome)
        captured["pool"] = pool

    resolver._attach_candidate_evidence = spy

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.validated is True  # unchanged external behavior
    pool = captured["pool"]
    assert len(pool.candidates) == 2
    fallback_candidate = pool.candidates[1]
    assert any(e.type is EvidenceType.PROVIDER_AGREEMENT for e in fallback_candidate.evidence.items)
    # Confidence should be meaningfully higher than a bare provider hit
    # (0.30) once agreement + reachability + https + brand match stack up.
    assert fallback_candidate.confidence > 0.30


def test_candidate_pool_has_no_entries_when_provider_finds_nothing():
    """A provider returning nothing must never produce a fabricated
    candidate -- confirms CandidatePool stays empty rather than padding
    itself with a placeholder."""
    resolver = WebsiteResolver(FakeValidator({}), fallback_provider=FakeFallbackProvider(None))
    candidate = _candidate(website=None)

    captured = {}
    original_summary = resolver._log_pool_summary

    def spy(pool, chosen):
        captured["pool"] = pool
        original_summary(pool, chosen)

    resolver._log_pool_summary = staticmethod(spy)

    _run(resolver.resolve(candidate, "Mumbai"))

    assert captured["pool"].candidates == []


def test_brand_and_location_evidence_populate_for_a_strong_match():
    validator = FakeValidator({"https://metroshoesmumbai.com": Outcome(True, "https://metroshoesmumbai.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=None)
    candidate = _candidate(name="Metro Shoes", website="https://metroshoesmumbai.com")

    captured = {}
    original_attach = resolver._attach_candidate_evidence

    def spy(pool, website_candidate, business, location, outcome):
        original_attach(pool, website_candidate, business, location, outcome)
        captured["candidate"] = website_candidate

    resolver._attach_candidate_evidence = spy

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.validated is True
    wc = captured["candidate"]
    types = {e.type for e in wc.evidence.items}
    assert EvidenceType.BRAND_MATCH in types
    assert EvidenceType.LOCATION_MATCH in types
    assert EvidenceType.REACHABILITY in types
    assert EvidenceType.HTTPS in types
    assert wc.confidence > 0.30


# -- Sprint 2A: identity resolution is wired in without changing behavior --
#
# Same discipline as the Sprint 1 section above: every branch is checked for
# byte-for-byte identical WebsiteResolution output with the identity layer
# now wired in (it is on by default -- GenericIdentityResolver, no opt-in
# required), then the new BusinessIdentity capability itself is checked
# separately.

from application.discovery.identity import GenericIdentityResolver, IdentityResolver  # noqa: E402


class SpyIdentityResolver(IdentityResolver):
    """Wraps the real GenericIdentityResolver so tests can inspect the
    BusinessIdentity a call produced without changing what it computes."""

    def __init__(self):
        self._inner = GenericIdentityResolver()
        self.captured = None

    def resolve(self, business, pool, location, chosen, chosen_url):
        identity = self._inner.resolve(business, pool, location, chosen, chosen_url)
        self.captured = identity
        return identity


def test_default_identity_resolver_does_not_change_overpass_pass_output():
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://should-not-be-called.com"))
    candidate = _candidate(website="https://nike.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website == "https://nike.com/"
    assert result.resolved_via == "overpass"
    assert result.validated is True
    assert result.rejection_reason is None


def test_default_identity_resolver_does_not_change_full_failure_output():
    validator = FakeValidator(
        {
            "https://nike-typo.com": Outcome(False, reason="unreachable_http_404"),
            "https://nike-wrong.com": Outcome(False, reason="dns_resolution_failed"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike-wrong.com"))
    candidate = _candidate(website="https://nike-typo.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website is None
    assert result.resolved_via == "none"
    assert result.validated is False
    assert result.rejection_reason == "dns_resolution_failed"


def test_injected_identity_resolver_never_changes_returned_website_resolution():
    """A custom IdentityResolver (spy or otherwise) is purely observational
    -- swapping it must never change resolve()'s return value, since
    resolve() always makes its decision first and only *afterward* asks
    the identity layer to explain it."""
    validator = FakeValidator(
        {
            "https://nike.com": Outcome(False, reason="request_timed_out"),
            "https://nike.com/about": Outcome(True, "https://nike.com/"),
        }
    )
    spy = SpyIdentityResolver()
    resolver = WebsiteResolver(
        validator,
        fallback_provider=FakeFallbackProvider("https://nike.com/about"),
        identity_resolver=spy,
    )
    candidate = _candidate(name="Nike", website="https://nike.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website == "https://nike.com/"
    assert result.resolved_via == "serper"
    assert result.validated is True
    # And the identity layer actually ran and agrees with the decision.
    assert spy.captured is not None
    assert spy.captured.selected_website == "https://nike.com/"
    assert spy.captured.verification_state == "verified"


def test_business_identity_selected_website_always_matches_resolution_website():
    """The strongest possible backward-compatibility guarantee for the
    identity layer: whatever WebsiteResolution.website comes back as,
    BusinessIdentity.selected_website must be exactly the same value --
    across every branch -- because both come from the same `chosen_url`,
    never independently recomputed."""
    scenarios = [
        (
            {"https://nike.com": Outcome(True, "https://nike.com/")},
            FakeFallbackProvider("https://should-not-be-called.com"),
            "https://nike.com",
        ),
        (
            {
                "https://nike-typo.com": Outcome(False, reason="unreachable_http_404"),
                "https://nike.com": Outcome(True, "https://nike.com/"),
            },
            FakeFallbackProvider("https://nike.com"),
            "https://nike-typo.com",
        ),
        (
            {"https://nike-typo.com": Outcome(False, reason="unreachable_http_404")},
            FakeFallbackProvider(None),
            "https://nike-typo.com",
        ),
    ]
    for outcomes, fallback, website in scenarios:
        spy = SpyIdentityResolver()
        resolver = WebsiteResolver(FakeValidator(outcomes), fallback_provider=fallback, identity_resolver=spy)
        candidate = _candidate(website=website)

        result = _run(resolver.resolve(candidate, "Mumbai"))

        assert spy.captured.selected_website == result.website


def test_business_identity_no_candidates_when_nothing_to_resolve():
    resolver = WebsiteResolver(FakeValidator({}), fallback_provider=None)
    spy = SpyIdentityResolver()
    resolver.identity_resolver = spy
    candidate = _candidate(website=None)

    _run(resolver.resolve(candidate, "Mumbai"))

    assert spy.captured.verification_state == "no_candidates"
    assert spy.captured.website_candidates == []


def test_business_identity_merges_agreeing_candidates_into_one_group():
    """Same agreement scenario as
    test_agreement_evidence_fires_when_failed_overpass_and_fallback_share_a_domain
    above, but checked at the identity layer: two raw candidates on the
    same domain should merge into exactly one WebsiteCandidateGroup with
    both providers recorded."""
    validator = FakeValidator(
        {
            "https://nike.com": Outcome(False, reason="request_timed_out"),
            "https://nike.com/about": Outcome(True, "https://nike.com/"),
        }
    )
    spy = SpyIdentityResolver()
    resolver = WebsiteResolver(
        validator, fallback_provider=FakeFallbackProvider("https://nike.com/about"), identity_resolver=spy
    )
    candidate = _candidate(name="Nike", website="https://nike.com")

    _run(resolver.resolve(candidate, "Mumbai"))

    identity = spy.captured
    assert len(identity.website_candidates) == 1
    group = identity.website_candidates[0]
    assert set(group.contributing_providers) == {"overpass", "serper"}
    assert group.verified is True
    assert identity.decision_trace.provider_agreement.status == "agreement"


# -- Sprint 2B: Feature Extraction / Verification / Confidence Propagation --
#
# Sprint 2B replaced *how* GenericIdentityResolver computes verification
# and confidence (application.discovery.features/verification/confidence),
# but WebsiteResolver never talks to those modules directly -- it only
# ever calls `self.identity_resolver.resolve(...)`, exactly as it did in
# Sprint 2A. These tests confirm that swap is invisible from here: default
# WebsiteResolver behavior, with no constructor changes at all, is
# unaffected by Sprint 2B.


def test_default_website_resolver_output_unaffected_by_sprint_2b_pipeline_change():
    """Same scenario, same assertions as
    test_default_identity_resolver_does_not_change_overpass_pass_output
    above (Sprint 2A) -- re-run here as an explicit Sprint 2B regression
    checkpoint: the internal engine swap changes nothing about what
    WebsiteResolver.resolve() returns."""
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://should-not-be-called.com"))
    candidate = _candidate(website="https://nike.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website == "https://nike.com/"
    assert result.resolved_via == "overpass"
    assert result.validated is True
    assert result.rejection_reason is None


def test_website_resolver_constructor_still_accepts_no_new_required_arguments():
    """WebsiteResolver's own constructor signature is untouched by Sprint
    2B -- GenericIdentityResolver grew new, defaulted constructor
    parameters (feature_engine/verification_pipeline/confidence_engine),
    but nothing about wiring one into WebsiteResolver changed."""
    validator = FakeValidator({})
    resolver = WebsiteResolver(validator)
    assert resolver.identity_resolver is not None
    from application.discovery.identity import GenericIdentityResolver

    assert isinstance(resolver.identity_resolver, GenericIdentityResolver)


def test_business_identity_confidence_is_now_propagated_but_still_verified_end_to_end():
    """The one deliberate behavior change Sprint 2B makes (per its own
    brief): BusinessIdentity.confidence now comes from
    ConfidenceTrace.final instead of a flat evidence sum. Checked here
    through the full WebsiteResolver pipeline, not just identity.py in
    isolation -- confirms the swap is coherent end-to-end, not just at the
    unit level."""
    validator = FakeValidator(
        {
            "https://nike.com": Outcome(False, reason="request_timed_out"),
            "https://nike.com/about": Outcome(True, "https://nike.com/"),
        }
    )
    spy = SpyIdentityResolver()
    resolver = WebsiteResolver(
        validator, fallback_provider=FakeFallbackProvider("https://nike.com/about"), identity_resolver=spy
    )
    candidate = _candidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai")

    _run(resolver.resolve(candidate, "Mumbai"))

    identity = spy.captured
    assert identity.confidence == identity.decision_trace.confidence_trace.final
    assert identity.confidence > 0.0
    assert identity.decision_trace.verification_bundle.overall_passed is True


# -- Sprint 2C: Verified Digital Identity, wired in without changing behavior --
#
# Same discipline as the Sprint 2A/2B sections above: every branch is first
# checked for byte-for-byte identical WebsiteResolution output via resolve()
# (unaffected -- it and resolve_with_digital_identity() share the same
# _resolve_internal(), and resolve() simply discards the second element),
# then the new VerifiedDigitalIdentity capability itself is checked
# separately via resolve_with_digital_identity().

from application.discovery.digital_identity import (  # noqa: E402
    DigitalIdentityStatus,
    VerifiedDigitalIdentity,
)


def test_resolve_still_returns_only_a_website_resolution_overpass_pass():
    """Explicit Sprint 2C regression checkpoint mirroring the Sprint 2A/2B
    ones above: resolve()'s return type/value is completely unaffected by
    the digital-identity wiring."""
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://should-not-be-called.com"))
    candidate = _candidate(website="https://nike.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert not isinstance(result, tuple)  # resolve() must never start returning a tuple
    assert isinstance(result, WebsiteResolution)
    assert result.website == "https://nike.com/"
    assert result.resolved_via == "overpass"
    assert result.validated is True
    assert result.rejection_reason is None


def test_resolve_still_returns_only_a_website_resolution_full_failure():
    validator = FakeValidator(
        {
            "https://nike-typo.com": Outcome(False, reason="unreachable_http_404"),
            "https://nike-wrong.com": Outcome(False, reason="dns_resolution_failed"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike-wrong.com"))
    candidate = _candidate(website="https://nike-typo.com")

    result = _run(resolver.resolve(candidate, "Mumbai"))

    assert result.website is None
    assert result.resolved_via == "none"
    assert result.validated is False
    assert result.rejection_reason == "dns_resolution_failed"


def test_website_resolver_constructor_still_accepts_no_new_required_arguments_sprint_2c():
    """WebsiteResolver's constructor gained one more defaulted, DI
    parameter (digital_identity_builder) -- same pattern as
    identity_resolver before it -- but nothing became required."""
    resolver = WebsiteResolver(FakeValidator({}))
    assert resolver.digital_identity_builder is not None
    from application.discovery.digital_identity import DigitalIdentityBuilder

    assert isinstance(resolver.digital_identity_builder, DigitalIdentityBuilder)


def test_resolve_with_digital_identity_returns_the_same_website_resolution_as_resolve():
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://should-not-be-called.com"))
    candidate = _candidate(name="Nike", website="https://nike.com")

    plain_result = _run(resolver.resolve(candidate, "Mumbai"))
    resolution, digital_identity = _run(resolver.resolve_with_digital_identity(candidate, "Mumbai"))

    assert resolution.website == plain_result.website
    assert resolution.resolved_via == plain_result.resolved_via
    assert resolution.validated == plain_result.validated
    assert isinstance(digital_identity, VerifiedDigitalIdentity)
    assert digital_identity.selected_website == resolution.website


def test_resolve_with_digital_identity_reports_verified_strong_for_a_clean_match():
    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=None)
    candidate = _candidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai", phone="555-1234")

    _resolution, digital_identity = _run(resolver.resolve_with_digital_identity(candidate, "Mumbai"))

    assert digital_identity.status is DigitalIdentityStatus.VERIFIED_STRONG
    assert digital_identity.risk_assessment.risk_level == "none"


def test_resolve_with_digital_identity_reports_no_identity_when_nothing_resolves():
    resolver = WebsiteResolver(FakeValidator({}), fallback_provider=None)
    candidate = _candidate(website=None)

    resolution, digital_identity = _run(resolver.resolve_with_digital_identity(candidate, "Mumbai"))

    assert resolution.website is None
    assert digital_identity.status is DigitalIdentityStatus.NO_IDENTITY
    assert any(i.code == "no_website" for i in digital_identity.risk_assessment.indicators)


def test_resolve_with_digital_identity_flags_conflicting_providers_as_risk_not_a_block():
    """Providers disagree, but the winning candidate still validates and
    resolve()'s own decision is completely unaffected by the risk this
    surfaces -- "risk explains uncertainty; it never blocks."""
    validator = FakeValidator(
        {
            "https://nike-typo.com": Outcome(False, reason="unreachable_http_404"),
            "https://nike-alt.com": Outcome(True, "https://nike-alt.com/"),
        }
    )
    resolver = WebsiteResolver(validator, fallback_provider=FakeFallbackProvider("https://nike-alt.com"))
    candidate = _candidate(name="Something Else Entirely", website="https://nike-typo.com")

    resolution, digital_identity = _run(resolver.resolve_with_digital_identity(candidate, "Mumbai"))

    # resolve()'s own decision: overpass's candidate failed validation, so
    # the fallback's candidate wins -- exactly as every prior sprint's
    # regression test already proves for this branch.
    assert resolution.website == "https://nike-alt.com/"
    assert resolution.validated is True
    assert digital_identity.selected_website == "https://nike-alt.com/"
    # And the risk layer honestly reports the disagreement, without
    # changing that decision at all.
    assert any(i.code == "conflicting_providers" for i in digital_identity.risk_assessment.indicators)


def test_digital_identity_builder_reuses_configured_verification_pipeline():
    """The default wiring passes the identity resolver's own
    VerificationPipeline through to the builder (see WebsiteResolver's
    docstring) -- confirmed here by checking object identity, not just
    behavior."""
    resolver = WebsiteResolver(FakeValidator({}))
    assert resolver.digital_identity_builder.verification_pipeline is resolver.identity_resolver.verification_pipeline


def test_verified_digital_identity_to_dict_is_json_serializable_end_to_end():
    import json

    validator = FakeValidator({"https://nike.com": Outcome(True, "https://nike.com/")})
    resolver = WebsiteResolver(validator, fallback_provider=None)
    candidate = _candidate(name="Nike", website="https://nike.com", address="1 Park St, Mumbai", phone="555-1234")

    _resolution, digital_identity = _run(resolver.resolve_with_digital_identity(candidate, "Mumbai"))

    json.dumps(digital_identity.to_dict())

