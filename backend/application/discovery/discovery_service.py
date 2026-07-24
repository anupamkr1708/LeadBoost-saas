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

from sqlalchemy.orm import Session

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
from application.discovery.providers.base import BusinessSearchProvider, WebsiteResolverProvider
from application.discovery.providers.overpass_provider import OverpassProvider
from application.discovery.providers.serper_provider import (
    SerperBusinessSearchProvider,
    SerperWebsiteResolver,
)
from application.discovery.query_parser import QueryParser
from application.discovery.ranking import rank_businesses
from application.discovery.website_resolver import WebsiteResolver
from application.discovery.website_validator import WebsiteValidator
from application.observability.repository import create_discovery_run_record
from application.utils.stage_logger import stage_span
from application.workflows.lead_pipeline import run_lead_pipeline
from core.domain.schemas.lead import LeadCreate, LeadUpdate
from core.infrastructure.billing.subscription_service import SubscriptionService
from core.infrastructure.database.crud import create_lead, get_lead_by_url, update_lead
from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.service")


class DiscoveryService:
    def __init__(
        self,
        db: Session,
        search_provider: Optional[BusinessSearchProvider] = None,
        resolver_fallback: Optional[WebsiteResolverProvider] = None,
        validator: Optional[WebsiteValidator] = None,
        business_search_fallback: Optional[BusinessSearchProvider] = None,
    ):
        self.db = db
        self.parser = QueryParser()
        self.search_provider = search_provider or OverpassProvider()
        self.validator = validator or WebsiteValidator()
        self.resolver_fallback = (
            resolver_fallback if resolver_fallback is not None else SerperWebsiteResolver()
        )
        self.resolver = WebsiteResolver(self.validator, self.resolver_fallback)
        # Only ever used when self.search_provider returns zero candidates
        # -- see the module docstring and _search below. Pass
        # business_search_fallback=None explicitly to disable it outright.
        self.business_search_fallback = (
            business_search_fallback if business_search_fallback is not None else SerperBusinessSearchProvider()
        )
        self.max_concurrent_pipelines = max(
            1, int(os.getenv("DISCOVERY_MAX_CONCURRENT_PIPELINES", "3"))
        )

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
        )

    # -- Stage: search (with retry inside the provider, graceful failure here) --

    async def _search(self, parsed: ParsedQuery) -> List[BusinessCandidate]:
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
        discovered: List[DiscoveredBusiness] = []
        resolved_via_fallback_count = 0

        with stage_span("discovery_resolve_validate", candidate_count=len(candidates)):
            for candidate in candidates:
                try:
                    resolution = await self.resolver.resolve(candidate, location)
                except Exception as e:
                    # A single business's resolution must never abort the batch.
                    logger.warning(f"Website resolution failed for '{candidate.name}': {e}")
                    resolution = WebsiteResolution(
                        website=None,
                        resolved_via="none",
                        validated=False,
                        rejection_reason="resolution_error",
                    )
                if resolution.resolved_via == "brave":
                    resolved_via_fallback_count += 1
                discovered.append(DiscoveredBusiness(candidate=candidate, resolution=resolution))

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
        remaining_quota = usage.remaining_daily_leads

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

                if remaining_quota <= 0:
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
                    remaining_quota -= 1

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