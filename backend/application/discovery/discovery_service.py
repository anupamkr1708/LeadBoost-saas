"""
Discovery Service.

Orchestrates the full, deterministic discovery pipeline:

    parse -> search (Overpass, general web-search fallback if that finds
    nothing) -> resolve+validate website (Overpass website, else Serper
    fallback) -> duplicate detection -> ranking -> Lead creation ->
    existing LeadPipeline.execute()

This is the *only* module that talks to the database and to
application.workflows.lead_pipeline -- every other discovery module
(query_parser, providers, business_normalizer, website_resolver,
website_validator, duplicate_detector, ranking) is a pure/self-contained
building block with no knowledge of Lead, LeadPipeline, or the DB.

No LLM call happens anywhere in this file or anything it calls before a
Lead is hantded to LeadPipeline.execute() -- from that point on, the
existing, unmodified pipeline (and its own AI-feature gating) takes over
exactly as it does for manually-created leads.

Sprint 4 (Discovery Quality Refinement): `_resolve_and_validate` now calls
`WebsiteResolver.resolve_with_digital_identity()` instead of `resolve()`
-- an existing, unchanged public method (Sprint 2C) that returns the
identical `WebsiteResolution` alongside the canonical
`VerifiedDigitalIdentity`, which was already being computed internally on
every call and simply discarded before (see WebsiteResolver's own
docstring). `DiscoveredBusiness.identity` carries it through to
`ranking.py`, which now scores from these already-computed signals
instead of recomputing brand/location/completeness from raw candidate
fields a second time. Every other stage, and every externally observable
behavior of `discover_and_create_leads()`, is unchanged.

The default website-resolution fallback provider is SerperWebsiteResolver
(application.discovery.providers.serper_provider) -- Brave's API now
requires a card on file, whereas Serper offers a free tier with no card
required. BraveWebsiteResolver is untouched and still fully usable by
constructor-injecting it as `resolver_fallback` if you have a Brave key.
Nothing else about provider selection changed: this remains the only
dependency-injection point, exactly as before.

Business-search fallback: Overpass (OpenStreetMap) only knows about
businesses that are physically map-able -- it returns zero results just
as often for a real category with no OSM presence (SaaS companies,
remote-first agencies, freelancers, ...) as for a genuine dead end. Rather
than special-casing a fixed list of categories that "look like" they need
a fallback (which would only ever cover categories someone thought to
enumerate, and wouldn't generalize to whatever a person actually types),
`_search` falls back to `business_search_fallback`
(SerperBusinessSearchProvider by default) for ANY category whenever the
primary provider comes back with zero candidates. Overpass is always
tried first and never skipped -- the fallback only ever runs after it has
already returned nothing.
"""

import asyncio
import os
import time
from typing import List, Optional

import aiohttp
from sqlalchemy.orm import Session

from application.discovery.digital_identity import DigitalIdentityBuilder
from application.discovery.dto import (
    BusinessCandidate,
    DiscoveredBusiness,
    DiscoveryResponse,
    LeadCreationOutcome,
    ParsedQuery,
    WebsiteResolution,
)
from application.discovery.duplicate_detector import DuplicateDetector
from application.discovery.exceptions import ProviderError, QueryParseError
from application.discovery.false_positive import DomainObservationRegistry
from application.discovery.identity_resolution_engine import IdentityResolutionEngine
from application.discovery.providers.base import BusinessSearchProvider, WebsiteResolverProvider
from application.discovery.providers.overpass_provider import OverpassProvider
from application.discovery.providers.serper_provider import (
    SerperBusinessSearchProvider,
    SerperWebsiteResolver,
)
from application.discovery.query_parser import QueryParser
from application.discovery.ranking import rank_businesses
from application.discovery.reliability import ProviderReliabilityRegistry
from application.discovery.website_resolver import WebsiteResolver
from application.discovery.website_validator import WebsiteValidator
from application.observability.repository import create_discovery_run_record
from application.utils.stage_logger import stage_span
from application.workflows.lead_pipeline import run_lead_pipeline
from core.domain.schemas.lead import LeadCreate, LeadUpdate
from core.infrastructure.billing.subscription_service import SubscriptionService
from core.infrastructure.billing.quota_guard import reserve_daily_lead_quota
from core.infrastructure.database.crud import create_lead, get_lead_by_url, update_lead
from core.infrastructure.logging import get_logger
from sqlalchemy.exc import IntegrityError

logger = get_logger("application.discovery.service")

# Bug fix: `business_search_fallback: Optional[...] = None` cannot, by
# itself, tell "the caller explicitly passed None to disable the
# fallback" apart from "the caller didn't pass anything" -- both look
# identical inside __init__ once the parameter's value is None. The
# result (found via a real, previously-hanging pytest run of
# test_fallback_disabled_when_explicitly_none, and a real failure of
# test_search_provider_failure_degrades_gracefully) was that
# `business_search_fallback=None` -- and simply omitting the argument,
# which every other caller in this file's own test suite does --
# silently constructed a real `SerperBusinessSearchProvider()` and
# attempted a live network call, exactly contradicting the module
# docstring and the __init__ comment below that both promise `None`
# disables it. This sentinel is the only way to distinguish the two
# cases; see its two uses in __init__.
_FALLBACK_NOT_PASSED = object()


class DiscoveryService:
    def __init__(
        self,
        db: Session,
        search_provider: Optional[BusinessSearchProvider] = None,
        resolver_fallback: Optional[WebsiteResolverProvider] = None,
        validator: Optional[WebsiteValidator] = None,
        business_search_fallback: Optional[BusinessSearchProvider] = _FALLBACK_NOT_PASSED,  # type: ignore[assignment]
        session: Optional[aiohttp.ClientSession] = None,
        reliability_registry: Optional[ProviderReliabilityRegistry] = None,
        domain_observations: Optional[DomainObservationRegistry] = None,
    ):
        self.db = db
        self.parser = QueryParser()
        self.search_provider = search_provider or OverpassProvider()
        self.validator = validator or WebsiteValidator()
        self.resolver_fallback = (
            resolver_fallback if resolver_fallback is not None else SerperWebsiteResolver()
        )
        # H4: explicit, named references instead of letting
        # IdentityResolutionEngine construct its own defaults several
        # layers deep with nothing at this level ever reading them back.
        # Both registries were already being built and used every run
        # (reliability.py / false_positive.py, Sprint 3/4) -- this changes
        # nothing about what gets computed, only makes the result visible
        # here and in DiscoveryResponse.provider_metrics (below).
        self.reliability_registry = reliability_registry or ProviderReliabilityRegistry()
        self.domain_observations = domain_observations or DomainObservationRegistry()
        identity_resolution_engine = IdentityResolutionEngine(
            reliability_registry=self.reliability_registry,
            domain_observations=self.domain_observations,
        )
        digital_identity_builder = DigitalIdentityBuilder(
            identity_resolution_engine=identity_resolution_engine
        )
        self.resolver = WebsiteResolver(
            self.validator, self.resolver_fallback, digital_identity_builder=digital_identity_builder
        )
        # Only ever used when self.search_provider returns zero candidates
        # -- see the module docstring and _search below. Pass
        # business_search_fallback=None explicitly to disable it outright
        # (omit the argument entirely to get the real default instead --
        # see _FALLBACK_NOT_PASSED above for why these two are not the
        # same thing).
        self.business_search_fallback = (
            SerperBusinessSearchProvider()
            if business_search_fallback is _FALLBACK_NOT_PASSED
            else business_search_fallback
        )
        self.max_concurrent_pipelines = max(
            1, int(os.getenv("DISCOVERY_MAX_CONCURRENT_PIPELINES", "3"))
        )
        # Website resolution/validation is near-pure network I/O wait
        # (Serper API + HTTP checks), so a modest worker pool captures
        # most of the available speedup without holding more than a
        # handful of requests in flight at once -- kept low by default to
        # stay predictable on a memory-constrained deployment (e.g.
        # Render's Starter tier, ~500MB).
        self.max_concurrent_resolutions = max(
            1, int(os.getenv("DISCOVERY_MAX_CONCURRENT_RESOLUTIONS", "5"))
        )

        # -- H1: shared HTTP session (connection reuse) --------------------
        # `session`, if the caller already manages one (e.g. one created
        # at FastAPI app startup and closed at shutdown -- the standard
        # aiohttp pattern), is used as-is and never closed by this class.
        #
        # If not supplied, we lazily create and own one ourselves --
        # lazily, not here in __init__, because aiohttp.ClientSession()
        # must be constructed inside a running event loop, and this
        # constructor is a plain sync __init__ that may be called from a
        # context with no running loop (e.g. a sync FastAPI dependency
        # provider). See `_ensure_shared_session`, called at the top of
        # `discover_and_create_leads`. Only threaded into the four
        # sub-components THIS constructor created itself (tracked below);
        # a caller-supplied provider instance keeps whatever session
        # configuration the caller already gave it.
        self._session = session
        self._owned_session: Optional[aiohttp.ClientSession] = None
        self._owns_search_provider = search_provider is None
        self._owns_resolver_fallback = resolver_fallback is None
        self._owns_validator = validator is None
        # Only "owns" (and therefore should manage the session lifecycle
        # of) a fallback provider it constructed itself -- i.e. the
        # argument was omitted. When the caller explicitly disabled it
        # (business_search_fallback=None), self.business_search_fallback
        # is None too and there's nothing to own. See _FALLBACK_NOT_PASSED
        # above.
        self._owns_business_search_fallback = business_search_fallback is _FALLBACK_NOT_PASSED

    async def _ensure_shared_session(self) -> Optional[aiohttp.ClientSession]:
        """Lazily creates (once) the one shared aiohttp session this
        instance's own default sub-components reuse for every HTTP call
        across every `discover_and_create_leads` call on this instance --
        not just within one call (see H1 in the hardening plan: several
        hundred HTTP calls happen in a single benchmark run, previously
        each paying its own fresh TCP+TLS handshake). No-op on every call
        after the first. Returns None if this instance has no default
        components to wire a session into (session reuse then simply
        doesn't apply, exactly like today)."""
        if self._session is not None:
            return self._session
        if self._owned_session is None and (
            self._owns_search_provider or self._owns_resolver_fallback
            or self._owns_validator or self._owns_business_search_fallback
        ):
            self._owned_session = aiohttp.ClientSession()
            if self._owns_validator:
                self.validator.session = self._owned_session
            if self._owns_resolver_fallback:
                self.resolver_fallback.session = self._owned_session
            if self._owns_search_provider:
                self.search_provider.session = self._owned_session
            if self._owns_business_search_fallback and self.business_search_fallback is not None:
                self.business_search_fallback.session = self._owned_session
        return self._owned_session

    async def aclose(self) -> None:
        """Closes the shared aiohttp session this instance created for
        itself (H1), if any. No-op if a caller supplied their own session
        (they own its lifecycle) or if no HTTP call has happened yet.
        Safe to call more than once. Callers that keep one DiscoveryService
        instance alive across many `discover_and_create_leads` calls (the
        case H1 is actually optimizing for) should call this once when
        they're done with the instance -- e.g. app shutdown, worker
        teardown, or (for the discovery_eval benchmark harness) once after
        the whole query batch finishes, not after each query."""
        if self._owned_session is not None and not self._owned_session.closed:
            await self._owned_session.close()
        self._owned_session = None

    async def discover_and_create_leads(
        self,
        query: str,
        organization_id: int,
        owner_id: int,
        limit: Optional[int] = None,
    ) -> DiscoveryResponse:
        start = time.time()

        parsed = self.parser.parse(query, limit_override=limit)
        logger.info(
            "Discovery query parsed",
            extra={
                "event": "discovery_query_parsed",
                "query": query,
                "category": parsed.category,
                "location": parsed.location,
                "limit": parsed.limit,
            },
        )

        candidates = await self._search(parsed)
        businesses_returned = len(candidates)
        missing_website_count = sum(1 for c in candidates if not c.website)

        discovered, resolved_via_fallback_count = await self._resolve_and_validate(
            candidates, parsed.location
        )

        duplicates_removed = self._detect_duplicates(discovered)

        eligible = [b for b in discovered if b.resolution.validated and not b.is_duplicate]
        rejected = [b for b in discovered if not b.resolution.validated or b.is_duplicate]

        with stage_span(
            "discovery_ranking", eligible_count=len(eligible), requested_limit=parsed.limit
        ):
            ranked = rank_businesses(eligible, parsed.category, parsed.location)
        selected = ranked[: parsed.limit]
        not_selected = ranked[parsed.limit :]

        outcomes = await self._create_leads_and_run_pipelines(selected, organization_id, owner_id)

        # Full accounting, not a capped preview: every eligible business
        # beyond the requested limit is reported (status "not_selected")
        # rather than silently dropped, and every rejected business is
        # reported too (see DiscoveryResponse's docstring) -- this is what
        # lets a response answer "why did I only get 3 of the 15 shoe
        # stores you found?" instead of just returning 3 with no context.
        outcomes.extend(self._not_selected_outcomes(not_selected))
        outcomes.extend(self._rejected_outcomes(rejected))

        duration_ms = int((time.time() - start) * 1000)
        validated_count = sum(1 for o in outcomes if o.status == "validated")

        self._record_metrics(
            organization_id=organization_id,
            query=query,
            parsed=parsed,
            businesses_returned=businesses_returned,
            missing_website_count=missing_website_count,
            resolved_via_fallback_count=resolved_via_fallback_count,
            duplicates_removed=duplicates_removed,
            validated_leads=validated_count,
            duration_ms=duration_ms,
        )

        return DiscoveryResponse(
            query=query,
            category=parsed.category,
            location=parsed.location,
            requested_limit=parsed.limit,
            businesses_found=businesses_returned,
            businesses=outcomes,
            duration_ms=duration_ms,
            provider_metrics={
                "provider_reliability": self.reliability_registry.to_dict(),
                "domain_observations": self.domain_observations.to_dict(),
            },
        )

    # -- Stage: search (with retry inside the provider, graceful failure here) --

    async def _search(self, parsed: ParsedQuery) -> List[BusinessCandidate]:
        # H1: lazily wire up the shared session on first use. _search is
        # always the first HTTP-issuing stage, whether reached via
        # discover_and_create_leads() or called directly (e.g. by the
        # discovery_eval benchmark harness, which calls _search /
        # _resolve_and_validate on this same instance without ever going
        # through discover_and_create_leads()) -- one call site covers
        # both. No-op after the first call.
        await self._ensure_shared_session()

        with stage_span(
            "discovery_search",
            provider=self.search_provider.name,
            category=parsed.category,
            location=parsed.location,
        ):
            try:
                candidates = await self.search_provider.search(
                    parsed.category, parsed.location, parsed.limit
                )
            except ProviderError as e:
                # Retries already happened inside the provider. A provider
                # failure degrades gracefully to zero candidates (which the
                # fallback below will then also try) rather than failing
                # the whole discovery request.
                logger.error(
                    f"Business search provider failed after retries: {e}",
                    extra={"event": "discovery_provider_failed", "provider": self.search_provider.name},
                )
                candidates = []

        if not candidates and self.business_search_fallback is not None:
            with stage_span(
                "discovery_search_fallback",
                provider=self.business_search_fallback.name,
                category=parsed.category,
                location=parsed.location,
            ):
                try:
                    candidates = await self.business_search_fallback.search(
                        parsed.category, parsed.location, parsed.limit
                    )
                except Exception as e:
                    logger.warning(
                        f"Business-search fallback failed: {e}",
                        extra={
                            "event": "discovery_provider_error",
                            "provider": self.business_search_fallback.name,
                        },
                    )
                    candidates = []
            if candidates:
                logger.info(
                    "Primary search returned zero results; used web-search fallback",
                    extra={
                        "event": "discovery_search_fallback_used",
                        "provider": self.business_search_fallback.name,
                        "category": parsed.category,
                        "location": parsed.location,
                        "candidates_found": len(candidates),
                    },
                )

        return candidates

    # -- Stage: website resolution + validation --

    async def _resolve_and_validate(
        self, candidates: List[BusinessCandidate], location: str
    ) -> "tuple[List[DiscoveredBusiness], int]":
        """Resolves and validates each candidate's website with bounded
        concurrency (default 5 at a time -- see
        DISCOVERY_MAX_CONCURRENT_RESOLUTIONS). This stage dominated
        discovery latency (60-90+ seconds for ~30 candidates) because
        website resolution/validation is almost entirely spent waiting on
        network I/O (Serper API calls, HTTP HEAD/GET checks against
        candidate sites) -- a small worker pool gets most of the possible
        speedup from overlapping that wait time without meaningfully
        increasing memory (each in-flight resolution is one aiohttp
        request, not a heavy process/thread), which matters on a
        constrained deployment target. Results preserve the original
        candidate order regardless of which finished first.

        Sprint 4: calls `resolve_with_digital_identity()` (unchanged
        WebsiteResolver public API) instead of `resolve()`, so every
        `DiscoveredBusiness` also carries the VerifiedDigitalIdentity
        `ranking.py` now ranks from -- no new I/O beyond what `resolve()`
        already performed (both methods run the exact same internal
        resolution; the digital-identity object was already being built
        internally either way, see WebsiteResolver's own docstring)."""
        resolved_via_fallback_count = 0
        semaphore = asyncio.Semaphore(self.max_concurrent_resolutions)

        async def resolve_one(candidate: BusinessCandidate) -> DiscoveredBusiness:
            async with semaphore:
                try:
                    # Sprint 4: resolve_with_digital_identity() (an
                    # existing, unchanged WebsiteResolver public method
                    # since Sprint 2C) instead of plain resolve() -- same
                    # WebsiteResolution, plus the VerifiedDigitalIdentity
                    # ranking.py now consumes instead of recomputing brand/
                    # location/completeness signals from raw candidate
                    # fields. WebsiteResolver's public API is completely
                    # unchanged by this; only which of its two already
                    # -public methods this internal call site uses.
                    resolution, identity = await self.resolver.resolve_with_digital_identity(candidate, location)
                except Exception as e:
                    # A single business's resolution must never abort the batch.
                    logger.warning(f"Website resolution failed for '{candidate.name}': {e}")
                    resolution = WebsiteResolution(
                        website=None,
                        resolved_via="none",
                        validated=False,
                        rejection_reason="resolution_error",
                    )
                    identity = None
            return DiscoveredBusiness(candidate=candidate, resolution=resolution, identity=identity)

        with stage_span("discovery_resolve_validate", candidate_count=len(candidates)):
            discovered = list(await asyncio.gather(*[resolve_one(c) for c in candidates]))

        resolved_via_fallback_count = sum(
            1 for b in discovered
            if b.resolution.resolved_via == self.resolver_fallback.name
        )
        return discovered, resolved_via_fallback_count

    # -- Stage: duplicate detection (in-batch) --

    def _detect_duplicates(self, discovered: List[DiscoveredBusiness]) -> int:
        with stage_span("discovery_duplicate_detection", candidate_count=len(discovered)):
            detector = DuplicateDetector()
            detector.dedup(discovered)
        return sum(1 for b in discovered if b.is_duplicate)

    # -- Stage: Lead creation + existing LeadPipeline execution --

    async def _create_leads_and_run_pipelines(
        self,
        businesses: List[DiscoveredBusiness],
        organization_id: int,
        owner_id: int,
    ) -> List[LeadCreationOutcome]:
        subscription_service = SubscriptionService(self.db)
        usage = subscription_service.get_organization_usage(organization_id)

        to_process: List[tuple] = []
        outcomes: List[LeadCreationOutcome] = []

        with stage_span("discovery_lead_creation", candidate_count=len(businesses)):
            for business in businesses:
                name = business.candidate.name
                website = business.resolution.website

                existing = get_lead_by_url(self.db, website, organization_id)
                if existing:
                    outcomes.append(
                        LeadCreationOutcome(
                            name=name,
                            website=website,
                            status="duplicate",
                            lead_id=existing.id,
                            reason="A lead for this website already exists in your organization",
                        )
                    )
                    continue

                # Production-robustness hardening (Phase A5): quota
                # enforcement for this loop used to be a plain local
                # counter (`remaining_quota`) seeded once from a single
                # COUNT(*)-based snapshot (`usage`) -- correct only
                # against *other businesses in this same request*, not
                # against a concurrent /discovery/search or /leads
                # request for the same organization creating leads at
                # the same time. It's replaced here with an atomic,
                # per-lead reservation (core/infrastructure/billing/
                # quota_guard.py) that is safe across concurrent
                # requests: each call re-checks against the database's
                # current committed count, not a value cached at the
                # start of this loop.
                if not reserve_daily_lead_quota(self.db, organization_id, usage.max_leads_per_day):
                    outcomes.append(
                        LeadCreationOutcome(
                            name=name,
                            website=website,
                            status="quota_exceeded",
                            reason="Daily lead quota exceeded",
                        )
                    )
                    continue

                try:
                    db_lead = create_lead(
                        self.db,
                        LeadCreate(website=website, organization_id=organization_id, owner_id=owner_id),
                    )

                    # Pre-seed fields Discovery already knows from Overpass,
                    # reusing existing Lead columns -- no schema change.
                    seed_fields = {}
                    if business.candidate.phone:
                        seed_fields["phone"] = business.candidate.phone
                    if business.candidate.address:
                        seed_fields["address"] = business.candidate.address
                    if seed_fields:
                        update_lead(self.db, db_lead.id, LeadUpdate(**seed_fields))

                    to_process.append((business, db_lead))
                except IntegrityError:
                    # Production-robustness hardening (Phase A3,
                    # lead-creation race): a concurrent request created a
                    # lead for this same website between the dedup check
                    # above and this call (see the UniqueConstraint on
                    # core/domain/models/lead.py::Lead) -- report it the
                    # same way the ordinary dedup path above does, not as
                    # a failure.
                    self.db.rollback()
                    existing = get_lead_by_url(self.db, website, organization_id)
                    outcomes.append(
                        LeadCreationOutcome(
                            name=name,
                            website=website,
                            status="duplicate",
                            lead_id=existing.id if existing else None,
                            reason="A lead for this website already exists in your organization",
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to create lead for '{name}': {e}")
                    outcomes.append(
                        LeadCreationOutcome(
                            name=name,
                            website=website,
                            status="pipeline_error",
                            reason=f"Lead creation failed: {e}",
                        )
                    )

            self.db.commit()

        if to_process:
            pipeline_outcomes = await self._run_pipelines(to_process)
            outcomes.extend(pipeline_outcomes)

        return outcomes

    async def _run_pipelines(self, to_process: list) -> List[LeadCreationOutcome]:
        semaphore = asyncio.Semaphore(self.max_concurrent_pipelines)

        async def run_one(business: DiscoveredBusiness, lead) -> LeadCreationOutcome:
            async with semaphore:
                try:
                    result = await run_lead_pipeline(lead.id)
                    return LeadCreationOutcome(
                        name=business.candidate.name,
                        website=business.resolution.website,
                        status="validated",
                        lead_id=lead.id,
                        pipeline_status=result.get("status"),
                    )
                except Exception as e:
                    # One business's pipeline failure must never stop the
                    # rest of the batch -- this is caught here, not left to
                    # asyncio.gather.
                    logger.error(f"Pipeline execution failed for lead {lead.id}: {e}")
                    return LeadCreationOutcome(
                        name=business.candidate.name,
                        website=business.resolution.website,
                        status="validated",
                        lead_id=lead.id,
                        pipeline_status="FAILED",
                        reason=f"Pipeline error: {e}",
                    )

        with stage_span("discovery_pipeline_batch", batch_size=len(to_process)):
            results = await asyncio.gather(*[run_one(b, l) for b, l in to_process])
        return list(results)

    # -- Outcome builders for businesses that never reached Lead creation --

    @staticmethod
    def _not_selected_outcomes(not_selected: List[DiscoveredBusiness]) -> List[LeadCreationOutcome]:
        """Businesses that validated successfully but ranked below the
        requested limit. No Lead is created for these (no pipeline cost
        spent on results beyond what was asked for), but they're still
        reported so the response accounts for every business found."""
        return [
            LeadCreationOutcome(
                name=business.candidate.name,
                website=business.resolution.website,
                status="not_selected",
                reason="Found and validated, but ranked beyond the requested result limit",
            )
            for business in not_selected
        ]

    @staticmethod
    def _rejected_outcomes(rejected: List[DiscoveredBusiness]) -> List[LeadCreationOutcome]:
        outcomes = []
        for business in rejected:
            if business.is_duplicate:
                status, reason = "duplicate", "Duplicate business within this search"
            elif not business.resolution.website:
                status = "no_website"
                reason = business.resolution.rejection_reason or "No verified website could be found"
            else:
                status = "validation_failed"
                reason = business.resolution.rejection_reason
            outcomes.append(
                LeadCreationOutcome(
                    name=business.candidate.name,
                    website=business.resolution.website,
                    status=status,
                    reason=reason,
                )
            )
        return outcomes

    # -- Metrics (extends the existing observability/analytics module) --

    def _record_metrics(
        self,
        *,
        organization_id: int,
        query: str,
        parsed: ParsedQuery,
        businesses_returned: int,
        missing_website_count: int,
        resolved_via_fallback_count: int,
        duplicates_removed: int,
        validated_leads: int,
        duration_ms: int,
    ) -> None:
        try:
            create_discovery_run_record(
                self.db,
                organization_id=organization_id,
                query=query,
                category=parsed.category,
                location=parsed.location,
                requested_limit=parsed.limit,
                businesses_returned=businesses_returned,
                businesses_missing_website=missing_website_count,
                websites_resolved_via_fallback=resolved_via_fallback_count,
                duplicates_removed=duplicates_removed,
                validated_leads=validated_leads,
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.error(f"Failed to persist discovery run record: {e}")