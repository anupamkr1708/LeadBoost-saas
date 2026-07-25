"""
Tests for application.discovery.discovery_service.DiscoveryService.

Every external call is mocked:
  - the search provider (Overpass) is a stub returning fixed candidates
  - the website validator is a stub
  - application.workflows.lead_pipeline.run_lead_pipeline is monkeypatched
    so no real scrape/LangGraph run happens -- this test is about
    discovery orchestration, not the (already extensively tested)
    pipeline itself.
"""

from typing import List, Optional

import pytest

from application.discovery.discovery_service import DiscoveryService
from application.discovery.dto import BusinessCandidate
from application.discovery.exceptions import ProviderError
from application.discovery.providers.base import BusinessSearchProvider
from application.discovery.website_validator import ValidationOutcome, WebsiteValidator
from application.observability.repository import get_discovery_runs
from core.infrastructure.database.crud import get_leads_by_organization


class _StubSearchProvider(BusinessSearchProvider):
    name = "stub_search"

    def __init__(self, candidates: List[BusinessCandidate], raise_error: bool = False):
        self._candidates = candidates
        self._raise_error = raise_error

    async def search(self, category, location, limit):
        if self._raise_error:
            raise ProviderError(self.name, "stub failure")
        return self._candidates


class _AllOkValidator(WebsiteValidator):
    """Accepts any non-empty URL, normalizing exactly as given."""

    async def validate(self, url):
        if not url:
            return ValidationOutcome(ok=False, reason="empty_url")
        return ValidationOutcome(ok=True, normalized_url=url)


class _StubFallback:
    name = "brave"

    def __init__(self, mapping: dict):
        self._mapping = mapping  # business_name -> url or None

    async def resolve_website(self, business_name, location):
        return self._mapping.get(business_name)


def _candidate(name, website=None, phone=None) -> BusinessCandidate:
    return BusinessCandidate(name=name, website=website, phone=phone, category="shoe stores")


async def _fake_run_lead_pipeline_success(lead_id: int):
    return {"lead_id": lead_id, "status": "SUCCESS", "errors": []}


async def _fake_run_lead_pipeline_mixed(lead_id: int):
    # Even-numbered lead_ids "fail", odd ones succeed -- exercises batch
    # isolation (one business's pipeline failing must not affect others).
    if lead_id % 2 == 0:
        raise RuntimeError(f"simulated pipeline crash for lead {lead_id}")
    return {"lead_id": lead_id, "status": "SUCCESS", "errors": []}


def _service(db_session, candidates, fallback_mapping=None, search_raises=False) -> DiscoveryService:
    return DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider(candidates, raise_error=search_raises),
        resolver_fallback=_StubFallback(fallback_mapping or {}),
        validator=_AllOkValidator(),
    )


async def test_creates_leads_for_validated_businesses(db_session, sample_org, sample_user, monkeypatch):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    candidates = [
        _candidate("Shoe World", website="https://shoeworld.example.com"),
        _candidate("Foot Palace", website="https://footpalace.example.com"),
    ]
    service = _service(db_session, candidates)

    result = await service.discover_and_create_leads(
        query="Top shoe stores in Mumbai",
        organization_id=sample_org.id,
        owner_id=sample_user.id,
    )

    assert result.businesses_found == 2
    validated = [b for b in result.businesses if b.status == "validated"]
    assert len(validated) == 2
    assert all(b.pipeline_status == "SUCCESS" for b in validated)
    assert all(b.lead_id is not None for b in validated)

    leads = get_leads_by_organization(db_session, sample_org.id)
    assert len(leads) == 2


async def test_never_fabricates_website_for_businesses_with_none_resolvable(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    candidates = [_candidate("No Website Co", website=None)]
    service = _service(db_session, candidates, fallback_mapping={})  # Brave finds nothing either

    result = await service.discover_and_create_leads(
        query="Dentists in Pune", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert len(result.businesses) == 1
    outcome = result.businesses[0]
    assert outcome.status == "no_website"
    assert outcome.website is None
    assert outcome.lead_id is None


async def test_brave_fallback_resolves_missing_website(db_session, sample_org, sample_user, monkeypatch):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    candidates = [_candidate("Fallback Co", website=None)]
    service = _service(
        db_session, candidates, fallback_mapping={"Fallback Co": "https://fallbackco.example.com"}
    )

    result = await service.discover_and_create_leads(
        query="Hotels in Goa", organization_id=sample_org.id, owner_id=sample_user.id
    )

    outcome = result.businesses[0]
    assert outcome.status == "validated"
    assert outcome.website == "https://fallbackco.example.com"


async def test_duplicate_businesses_in_same_batch_are_not_both_created(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    candidates = [
        _candidate("Nike Store", website="https://www.nike.com"),
        _candidate("Nike Store Branch 2", website="https://nike.com/branch2"),
    ]
    service = _service(db_session, candidates)

    result = await service.discover_and_create_leads(
        query="Top shoe stores in Mumbai", organization_id=sample_org.id, owner_id=sample_user.id
    )

    statuses = sorted(b.status for b in result.businesses)
    assert statuses == ["duplicate", "validated"]

    leads = get_leads_by_organization(db_session, sample_org.id)
    assert len(leads) == 1


async def test_existing_lead_in_db_is_reported_as_duplicate_not_recreated(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module
    from core.domain.schemas.lead import LeadCreate
    from core.infrastructure.database.crud import create_lead

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    existing = create_lead(
        db_session,
        LeadCreate(
            website="https://existing.example.com",
            organization_id=sample_org.id,
            owner_id=sample_user.id,
        ),
    )

    candidates = [_candidate("Existing Biz", website="https://existing.example.com")]
    service = _service(db_session, candidates)

    result = await service.discover_and_create_leads(
        query="Restaurants in Jaipur", organization_id=sample_org.id, owner_id=sample_user.id
    )

    outcome = result.businesses[0]
    assert outcome.status == "duplicate"
    assert outcome.lead_id == existing.id

    leads = get_leads_by_organization(db_session, sample_org.id)
    assert len(leads) == 1  # no new lead created


async def test_search_provider_failure_degrades_gracefully(db_session, sample_org, sample_user):
    service = _service(db_session, candidates=[], search_raises=True)

    result = await service.discover_and_create_leads(
        query="Accounting firms in Chennai", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert result.businesses_found == 0
    assert result.businesses == []  # no exception propagated to the caller


async def test_overflow_beyond_limit_is_reported_not_selected(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    candidates = [
        _candidate(f"Business {i}", website=f"https://business{i}.example.com") for i in range(5)
    ]
    service = _service(db_session, candidates)

    result = await service.discover_and_create_leads(
        query="Real estate agencies in Bangalore",
        organization_id=sample_org.id,
        owner_id=sample_user.id,
        limit=2,
    )

    statuses = [b.status for b in result.businesses]
    assert statuses.count("validated") == 2
    assert statuses.count("not_selected") == 3


async def test_one_pipeline_failure_does_not_stop_remaining_businesses(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_mixed)

    candidates = [
        _candidate(f"Business {i}", website=f"https://business{i}.example.com") for i in range(4)
    ]
    service = _service(db_session, candidates)

    result = await service.discover_and_create_leads(
        query="Top 4 hotels in Goa", organization_id=sample_org.id, owner_id=sample_user.id
    )

    # All 4 businesses still get a Lead created and an outcome reported,
    # even though ~half their pipeline runs "crashed".
    assert len(result.businesses) == 4
    assert all(b.status == "validated" for b in result.businesses)
    failed = [b for b in result.businesses if b.pipeline_status == "FAILED"]
    succeeded = [b for b in result.businesses if b.pipeline_status == "SUCCESS"]
    assert len(failed) > 0
    assert len(succeeded) > 0


async def test_discovery_run_is_persisted_for_metrics(db_session, sample_org, sample_user, monkeypatch):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    candidates = [_candidate("Shoe World", website="https://shoeworld.example.com")]
    service = _service(db_session, candidates)

    await service.discover_and_create_leads(
        query="Top shoe stores in Mumbai", organization_id=sample_org.id, owner_id=sample_user.id
    )

    records = get_discovery_runs(db_session, organization_id=sample_org.id)
    assert len(records) == 1
    assert records[0].businesses_returned == 1
    assert records[0].validated_leads == 1
    assert records[0].query == "Top shoe stores in Mumbai"


async def test_invalid_query_raises_query_parse_error(db_session, sample_org, sample_user):
    service = _service(db_session, candidates=[])
    from application.discovery.exceptions import QueryParseError

    with pytest.raises(QueryParseError):
        await service.discover_and_create_leads(
            query="not a valid shape",
            organization_id=sample_org.id,
            owner_id=sample_user.id,
        )


# -- Business-search fallback (used only when the primary search returns ------
# -- zero candidates -- not gated on any fixed category keyword list, so it
# -- applies to whatever category was actually searched for) ------------------


class _StubBusinessSearchFallback(BusinessSearchProvider):
    name = "stub_fallback"

    def __init__(self, candidates, raise_error: bool = False):
        self._candidates = candidates
        self._raise_error = raise_error
        self.calls: List[tuple] = []

    async def search(self, category, location, limit):
        self.calls.append((category, location, limit))
        if self._raise_error:
            raise RuntimeError("fallback exploded")
        return self._candidates


async def test_fallback_used_when_primary_search_returns_nothing(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    fallback_candidates = [_candidate("Acme AI", website="https://acmeai.example.com")]
    fallback = _StubBusinessSearchFallback(fallback_candidates)

    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider([]),  # primary finds nothing
        resolver_fallback=_StubFallback({}),
        validator=_AllOkValidator(),
        business_search_fallback=fallback,
    )

    result = await service.discover_and_create_leads(
        query="AI automation startups in Noida", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert fallback.calls == [("AI automation startups", "Noida", 20)]
    assert result.businesses_found == 1
    assert any(b.name == "Acme AI" and b.status == "validated" for b in result.businesses)


async def test_fallback_not_used_when_primary_search_finds_candidates(
    db_session, sample_org, sample_user, monkeypatch
):
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    fallback = _StubBusinessSearchFallback([_candidate("Should Not Appear")])
    candidates = [_candidate("Real Overpass Result", website="https://real.example.com")]

    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider(candidates),
        resolver_fallback=_StubFallback({}),
        validator=_AllOkValidator(),
        business_search_fallback=fallback,
    )

    result = await service.discover_and_create_leads(
        query="Dentists in Pune", organization_id=sample_org.id, owner_id=sample_user.id
    )

    # Overpass is always tried first and never skipped -- the fallback
    # must not be called at all when the primary provider already found
    # something, regardless of category.
    assert fallback.calls == []
    assert result.businesses_found == 1
    assert result.businesses[0].name == "Real Overpass Result"


async def test_fallback_failure_degrades_gracefully(db_session, sample_org, sample_user):
    fallback = _StubBusinessSearchFallback([], raise_error=True)

    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider([]),
        resolver_fallback=_StubFallback({}),
        validator=_AllOkValidator(),
        business_search_fallback=fallback,
    )

    result = await service.discover_and_create_leads(
        query="Boutique consultancies in Jaipur", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert result.businesses_found == 0
    assert result.businesses == []


async def test_fallback_disabled_when_explicitly_none(db_session, sample_org, sample_user):
    """Passing business_search_fallback=None explicitly (not just omitting
    it) turns the fallback off entirely -- an explicit opt-out remains
    possible even though a real fallback is wired in by default."""
    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider([]),
        resolver_fallback=_StubFallback({}),
        validator=_AllOkValidator(),
        business_search_fallback=None,
    )

    result = await service.discover_and_create_leads(
        query="Anything in Nowhereville", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert result.businesses_found == 0


async def test_fallback_applies_to_any_category_not_just_startups(
    db_session, sample_org, sample_user, monkeypatch
):
    """The fallback trigger is 'the primary search found nothing', full
    stop -- not a hardcoded list of SaaS/startup-sounding categories, so
    it generalizes to any query a person actually types."""
    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    fallback = _StubBusinessSearchFallback([_candidate("Jaipur Pottery Studio", website="https://jaipurpottery.example.com")])

    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider([]),
        resolver_fallback=_StubFallback({}),
        validator=_AllOkValidator(),
        business_search_fallback=fallback,
    )

    result = await service.discover_and_create_leads(
        query="artisanal pottery studios in Jaipur", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert len(fallback.calls) == 1
    assert result.businesses_found == 1


# -- Bounded concurrency for website resolution (performance) ---------------


async def test_resolution_runs_with_bounded_concurrency_not_sequentially(
    db_session, sample_org, sample_user, monkeypatch
):
    """Website resolution used to run strictly one-at-a-time, which
    dominated discovery latency (60-90+ seconds for ~30 candidates in
    production). It must now overlap resolutions up to the configured
    worker-pool size, never more."""
    import asyncio

    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)
    monkeypatch.setenv("DISCOVERY_MAX_CONCURRENT_RESOLUTIONS", "4")

    in_flight = {"current": 0, "max_seen": 0}

    class _ConcurrencyTrackingValidator(_AllOkValidator):
        async def validate(self, url):
            in_flight["current"] += 1
            in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
            await asyncio.sleep(0.02)
            in_flight["current"] -= 1
            return await super().validate(url)

    candidates = [_candidate(f"Business {i}", website=f"https://biz{i}.example.com") for i in range(12)]

    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider(candidates),
        resolver_fallback=_StubFallback({}),
        validator=_ConcurrencyTrackingValidator(),
    )

    await service.discover_and_create_leads(
        query="Businesses in Testville", organization_id=sample_org.id, owner_id=sample_user.id
    )

    assert in_flight["max_seen"] > 1, "resolutions ran strictly sequentially, no speedup gained"
    assert in_flight["max_seen"] <= 4, "exceeded the configured concurrency limit"


async def test_resolution_results_preserve_candidate_order_despite_concurrency(
    db_session, sample_org, sample_user, monkeypatch
):
    """Concurrent completion order must never scramble which website
    outcome belongs to which candidate."""
    import asyncio

    import application.discovery.discovery_service as svc_module

    monkeypatch.setattr(svc_module, "run_lead_pipeline", _fake_run_lead_pipeline_success)

    # Deliberately finish in reverse order (last candidate resolves
    # fastest) to prove ordering isn't just an accident of scheduling.
    class _ReverseOrderValidator(_AllOkValidator):
        async def validate(self, url):
            index = int(url.rsplit("biz", 1)[1].split(".")[0])
            await asyncio.sleep(0.05 - index * 0.01)
            return await super().validate(url)

    candidates = [_candidate(f"Business {i}", website=f"https://biz{i}.example.com") for i in range(5)]

    service = DiscoveryService(
        db=db_session,
        search_provider=_StubSearchProvider(candidates),
        resolver_fallback=_StubFallback({}),
        validator=_ReverseOrderValidator(),
    )

    result = await service.discover_and_create_leads(
        query="Businesses in Testville", organization_id=sample_org.id, owner_id=sample_user.id
    )

    names_in_order = [b.name for b in result.businesses]
    assert names_in_order == [f"Business {i}" for i in range(5)]
