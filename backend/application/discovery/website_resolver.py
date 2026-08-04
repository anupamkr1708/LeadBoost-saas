"""
Website Resolver.

Implements the exact resolution priority from the spec:

    Website from Overpass -> validated -> done
    otherwise -> fallback provider (Serper by default) -> candidate -> validated -> done
    otherwise -> website = None, continue (never invented)

This is the only place that decides *which* website ends up on a
DiscoveredBusiness; both providers and the validator are simple building
blocks it composes. Provider-agnostic by design: it depends only on the
WebsiteResolverProvider interface, so swapping the fallback provider
(Brave -> Serper, or any future implementation) never requires a change
here.

Internal evidence architecture
-------------------------------
`resolve()`'s external contract above is unchanged -- same signature, same
return type, same decision (first candidate to pass validation wins, in
provider-priority order), same public WebsiteResolution shape. What has
changed is internal: every candidate URL this method considers (whether it
ends up chosen or not) is now recorded in an application.discovery.evidence
CandidatePool, with an EvidenceBundle explaining -- deterministically and
without any new provider/HTTP calls -- how confident that specific
candidate looks (provider-found, brand match, location corroboration,
reachability, HTTPS, provider agreement/disagreement).

This is the "Business has Evidence; Website is one piece of evidence"
shift from the Sprint 1 brief made concrete, without yet changing what
gets returned: the pool is built and logged for observability and for
Sprint 2 (which can start consulting `pool.best_validated()` -- already
equivalent to today's decision -- instead of the inline if/else below),
but it never overrides the explicit validation-outcome checks that decide
what this method returns today.

Internal identity architecture (Sprint 2A)
-------------------------------------------
Built directly on top of the paragraph above, not a replacement for it:
once a CandidatePool is fully populated for a given return path, this
method also asks an application.discovery.identity.IdentityResolver to
turn that pool into a BusinessIdentity -- the business-centric,
INTERNAL-ONLY object that groups every raw candidate into distinct
website identities, extracts generic identity-matching Features from
them, and records a full decision trace ("why was this one selected").
The resolver is always told exactly which candidate `resolve()` itself
already chose (`chosen`/`chosen_url`, computed by the same explicit
validation-outcome checks as before) -- identity resolution explains that
decision, it never makes or overrides it. See
application.discovery.identity's module docstring for the full design.

Internal digital-identity architecture (Sprint 2C)
-----------------------------------------------------
Same discipline again, one layer further: `resolve()`'s own body moved
into `_resolve_internal()` unchanged (every line of decision logic is
identical -- verified by the full pre-Sprint-2C regression suite passing
byte-for-byte), and `resolve()` is now a two-line wrapper that discards
the second element of `_resolve_internal()`'s return tuple. That second
element is a `application.discovery.digital_identity.
VerifiedDigitalIdentity` -- the canonical, internal-only object Sprint
2C introduces (BusinessIdentity plus quality/risk/consensus assessment;
see that module's docstring). `resolve()`'s return value, type, and
behavior are therefore completely unaffected by this change.

The new capability is opt-in: `resolve_with_digital_identity()` is a new
public method returning both the unchanged `WebsiteResolution` and the
new `VerifiedDigitalIdentity`, for a future Sprint 3 caller (Scraper /
Company Intelligence / AI / Enrichment) that wants the canonical object
instead of raw provider output -- nothing in this sprint wires it into
`application.discovery.discovery_service.DiscoveryService`, which still
only ever calls `resolve()`, unchanged.
"""

from typing import Optional, Tuple

from application.discovery import digital_identity as digital_identity_model
from application.discovery import evidence as evidence_model
from application.discovery import identity as identity_model
from application.discovery.dto import BusinessCandidate, WebsiteResolution
from application.discovery.providers.base import WebsiteResolverProvider
from application.discovery.website_validator import WebsiteValidator
from core.infrastructure.logging import get_logger

logger = get_logger("application.discovery.website_resolver")


class WebsiteResolver:
    def __init__(
        self,
        validator: WebsiteValidator,
        fallback_provider: Optional[WebsiteResolverProvider] = None,
        verification_engine: Optional[evidence_model.VerificationEngine] = None,
        identity_resolver: Optional[identity_model.IdentityResolver] = None,
        digital_identity_builder: Optional["digital_identity_model.DigitalIdentityBuilder"] = None,
    ):
        self.validator = validator
        self.fallback_provider = fallback_provider
        # Constructor-injectable, defaulted -- same DI pattern as
        # `validator`/`fallback_provider` above. Sprint 1 only ever wires
        # in LightweightVerificationEngine (derives Evidence from a
        # ValidationOutcome already computed, no new I/O); see
        # application.discovery.evidence.VerificationEngine's docstring for
        # why this is the seam a future, heavier engine plugs into.
        self.verification_engine = verification_engine or evidence_model.LightweightVerificationEngine()
        # Same DI pattern again, for the Sprint 2A identity layer -- see
        # application.discovery.identity.IdentityResolver's docstring for
        # why a future Sprint 2B resolver plugs in here.
        self.identity_resolver = identity_resolver or identity_model.GenericIdentityResolver()
        # Sprint 2C: canonical VerifiedDigitalIdentity builder -- same DI
        # pattern once more, defaulted. Reuses whatever VerificationPipeline
        # self.identity_resolver is configured with, if it exposes one
        # (the default GenericIdentityResolver does), so
        # VerificationTrace's per-rejected-candidate entries stay
        # consistent with the pipeline actually used to verify the
        # selected one; falls back to a fresh default VerificationPipeline
        # for a bespoke IdentityResolver that doesn't expose the
        # attribute -- see DigitalIdentityBuilder's own docstring.
        self.digital_identity_builder = digital_identity_builder or digital_identity_model.DigitalIdentityBuilder(
            verification_pipeline=getattr(self.identity_resolver, "verification_pipeline", None)
        )

    async def resolve(self, candidate: BusinessCandidate, location: str) -> WebsiteResolution:
        resolution, _digital_identity = await self._resolve_internal(candidate, location)
        return resolution

    async def resolve_with_digital_identity(
        self, candidate: BusinessCandidate, location: str
    ) -> "Tuple[WebsiteResolution, digital_identity_model.VerifiedDigitalIdentity]":
        """Sprint 2C: additive, opt-in capability. Returns the exact same
        `WebsiteResolution` `resolve()` would (both call the same
        `_resolve_internal()`) alongside the new, canonical
        `VerifiedDigitalIdentity` -- the object a future Sprint 3 caller
        should consume instead of raw provider output. Calling this
        instead of `resolve()` changes nothing about how the website
        itself is chosen."""
        return await self._resolve_internal(candidate, location)

    async def _resolve_internal(
        self, candidate: BusinessCandidate, location: str
    ) -> "Tuple[WebsiteResolution, digital_identity_model.VerifiedDigitalIdentity]":
        # Every candidate this call considers -- whether it ends up chosen,
        # rejected, or never reached -- is recorded here purely for
        # internal observability/Sprint-2 groundwork. Nothing read from
        # `pool` below changes the return value; see the module docstring.
        pool = evidence_model.CandidatePool(business_name=candidate.name, location=location)

        # 1. Prefer whatever website the primary search provider already gave us.
        if candidate.website:
            outcome = await self.validator.validate(candidate.website)
            overpass_candidate = pool.add_candidate(
                url=candidate.website,
                provider=candidate.source,
                validated=outcome.ok,
                rejection_reason=None if outcome.ok else outcome.reason,
            )
            self._attach_candidate_evidence(pool, overpass_candidate, candidate, location, outcome)

            if outcome.ok:
                self._log_pool_summary(pool, chosen=overpass_candidate)
                business_identity = self._resolve_identity(
                    pool, candidate, location, chosen=overpass_candidate, chosen_url=outcome.normalized_url
                )
                digital_identity = self._resolve_digital_identity(business_identity, candidate)
                return (
                    WebsiteResolution(website=outcome.normalized_url, resolved_via="overpass", validated=True),
                    digital_identity,
                )
            logger.info(
                "Provider-supplied website failed validation, trying fallback resolution",
                extra={
                    "event": "discovery_website_validation_failed",
                    "business_name": candidate.name,
                    "url": candidate.website,
                    "reason": outcome.reason,
                    "confidence": overpass_candidate.confidence,
                },
            )

        # 2. Fall back to website-resolution-only search (Serper by
        # default; any WebsiteResolverProvider implementation works here),
        # if one is configured.
        if self.fallback_provider is not None:
            resolved_url = await self.fallback_provider.resolve_website(candidate.name, location)
            if resolved_url:
                outcome = await self.validator.validate(resolved_url)
                fallback_candidate = pool.add_candidate(
                    url=resolved_url,
                    provider=self.fallback_provider.name,
                    validated=outcome.ok,
                    rejection_reason=None if outcome.ok else outcome.reason,
                )
                self._attach_candidate_evidence(
                    pool, fallback_candidate, candidate, location, outcome
                )

                if outcome.ok:
                    logger.info(
                        "Fallback-resolved website passed validation",
                        extra={
                            "event": "discovery_website_validation_outcome",
                            "provider": self.fallback_provider.name,
                            "business_name": candidate.name,
                            "url": outcome.normalized_url,
                            "validated": True,
                            "confidence": fallback_candidate.confidence,
                        },
                    )
                    self._log_pool_summary(pool, chosen=fallback_candidate)
                    business_identity = self._resolve_identity(
                        pool,
                        candidate,
                        location,
                        chosen=fallback_candidate,
                        chosen_url=outcome.normalized_url,
                    )
                    digital_identity = self._resolve_digital_identity(business_identity, candidate)
                    return (
                        WebsiteResolution(
                            website=outcome.normalized_url,
                            resolved_via=self.fallback_provider.name,
                            validated=True,
                        ),
                        digital_identity,
                    )
                logger.info(
                    "Fallback-resolved website failed validation",
                    extra={
                        "event": "discovery_website_validation_outcome",
                        "provider": self.fallback_provider.name,
                        "business_name": candidate.name,
                        "url": resolved_url,
                        "validated": False,
                        "reason": outcome.reason,
                        "confidence": fallback_candidate.confidence,
                    },
                )
                self._log_pool_summary(pool, chosen=None)
                business_identity = self._resolve_identity(pool, candidate, location, chosen=None, chosen_url=None)
                digital_identity = self._resolve_digital_identity(business_identity, candidate)
                return (
                    WebsiteResolution(
                        website=None,
                        resolved_via="none",
                        validated=False,
                        rejection_reason=outcome.reason,
                    ),
                    digital_identity,
                )

        # 3. Never fabricate a domain.
        self._log_pool_summary(pool, chosen=None)
        business_identity = self._resolve_identity(pool, candidate, location, chosen=None, chosen_url=None)
        digital_identity = self._resolve_digital_identity(business_identity, candidate)
        return (
            WebsiteResolution(
                website=None,
                resolved_via="none",
                validated=False,
                rejection_reason="no_verified_website",
            ),
            digital_identity,
        )

    def _attach_candidate_evidence(
        self,
        pool: "evidence_model.CandidatePool",
        website_candidate: "evidence_model.WebsiteCandidate",
        business: BusinessCandidate,
        location: str,
        outcome,
    ) -> None:
        """Populates one WebsiteCandidate's EvidenceBundle from everything
        already known about it -- brand/location grounding (reusing
        application.discovery.grounding, not reimplementing it),
        verification evidence derived from the ValidationOutcome already
        computed by self.validator, and cross-provider agreement/
        disagreement against every other candidate already in the pool.
        Purely additive bookkeeping: never raises, never influences what
        `resolve()` returns."""
        website_candidate.evidence.add(
            evidence_model.brand_identity_evidence(
                business.name, website_candidate.domain, website_candidate.provider
            )
        )
        website_candidate.evidence.add(
            evidence_model.location_corroboration_evidence(
                location, website_candidate.domain, business.address, website_candidate.provider
            )
        )
        website_candidate.evidence.extend(
            self.verification_engine.verify(outcome, website_candidate.provider)
        )
        website_candidate.evidence.extend(
            evidence_model.provider_agreement_evidence(pool, website_candidate)
        )

    @staticmethod
    def _log_pool_summary(
        pool: "evidence_model.CandidatePool",
        chosen: "Optional[evidence_model.WebsiteCandidate]",
    ) -> None:
        """One structured log line per resolution showing every candidate
        considered and the evidence chain behind each one's confidence --
        the "never a black box" requirement made visible in production,
        without changing anything resolve() returns. `chosen` is passed
        through only for the log payload and for the defensive consistency
        check below; it is never derived from the pool."""
        best = pool.best_validated()
        if chosen is not None and best is not None and best.url != chosen.url:
            # Today's decision is a plain if/else, not pool.best_validated()
            # -- they are expected to always agree (best_validated() is
            # "first validated candidate in priority order", identical to
            # the if/else's own short-circuit rule). If they ever disagree
            # it means the two have drifted, which is worth surfacing loudly
            # now rather than silently once Sprint 2 starts reading from
            # the pool directly.
            logger.warning(
                "CandidatePool.best_validated() disagreed with the resolver's actual choice",
                extra={
                    "event": "discovery_candidate_pool_inconsistency",
                    "business_name": pool.business_name,
                    "chosen_url": chosen.url,
                    "best_validated_url": best.url,
                },
            )
        logger.info(
            "Website candidate pool evaluated",
            extra={
                "event": "discovery_candidate_pool_summary",
                "business_name": pool.business_name,
                "location": pool.location,
                "candidates_considered": len(pool.candidates),
                "chosen_url": chosen.url if chosen else None,
                "chosen_provider": chosen.provider if chosen else None,
                "chosen_confidence": chosen.confidence if chosen else None,
                "pool": pool.summary(),
            },
        )

    def _resolve_identity(
        self,
        pool: "evidence_model.CandidatePool",
        business: BusinessCandidate,
        location: str,
        chosen: "Optional[evidence_model.WebsiteCandidate]",
        chosen_url: "Optional[str]",
    ) -> "identity_model.BusinessIdentity":
        """Sprint 2A: turns the now-fully-populated `pool` into a
        BusinessIdentity via self.identity_resolver, and logs a decision
        -trace summary. `chosen`/`chosen_url` are resolve()'s own,
        already-made decision -- see the module docstring for why this
        method only ever explains that decision, never makes one. Purely
        additive, like _log_pool_summary: never raises, the returned
        BusinessIdentity is INTERNAL ONLY and is never attached to the
        WebsiteResolution this call ultimately returns."""
        business_identity = self.identity_resolver.resolve(business, pool, location, chosen, chosen_url)
        self._log_identity_summary(business_identity)
        return business_identity

    @staticmethod
    def _log_identity_summary(business_identity: "identity_model.BusinessIdentity") -> None:
        """One structured log line per resolution showing the resolved
        BusinessIdentity -- website candidates actually merged (candidate
        merging made visible), the decision trace ("why was candidate A
        selected"), and provider agreement/disagreement, explicitly
        rather than hidden."""
        logger.info(
            "Business identity resolved",
            extra={
                "event": "discovery_business_identity_resolved",
                "normalized_name": business_identity.normalized_name,
                "selected_website": business_identity.selected_website,
                "confidence": business_identity.confidence,
                "verification_state": business_identity.verification_state,
                "website_candidate_groups": len(business_identity.website_candidates),
                "provider_agreement": business_identity.decision_trace.provider_agreement.to_dict(),
                "decision_trace": business_identity.decision_trace.explain(),
            },
        )

    def _resolve_digital_identity(
        self,
        business_identity: "identity_model.BusinessIdentity",
        business: BusinessCandidate,
    ) -> "digital_identity_model.VerifiedDigitalIdentity":
        """Sprint 2C: builds the canonical VerifiedDigitalIdentity from
        the already-computed `business_identity` (no re-resolution, no
        new I/O -- see DigitalIdentityBuilder's own docstring) and logs a
        summary. Purely additive, exactly like `_resolve_identity` above:
        never raises, never influences `resolve()`'s return value -- its
        result only ever reaches a caller that explicitly asked for it via
        `resolve_with_digital_identity()`."""
        digital_identity = self.digital_identity_builder.build(business_identity, business)
        self._log_digital_identity_summary(digital_identity)
        return digital_identity

    @staticmethod
    def _log_digital_identity_summary(
        digital_identity: "digital_identity_model.VerifiedDigitalIdentity",
    ) -> None:
        """One structured log line per resolution showing the top-line
        Verified Digital Identity verdict -- status, quality, risk, and
        consensus -- without dumping the full nested structure (that's
        what `.to_dict()` on the object itself is for, if a caller wants
        it)."""
        logger.info(
            "Verified digital identity resolved",
            extra={
                "event": "discovery_verified_digital_identity_resolved",
                "status": digital_identity.status.value,
                "selected_website": digital_identity.selected_website,
                "quality_score": digital_identity.quality.overall_score,
                "risk_level": digital_identity.risk_assessment.risk_level,
                "consensus_level": digital_identity.consensus.level,
                "missing_evidence": list(digital_identity.missing_evidence),
            },
        )