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
"""

from typing import Optional

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
    ):
        self.validator = validator
        self.fallback_provider = fallback_provider

    async def resolve(self, candidate: BusinessCandidate, location: str) -> WebsiteResolution:
        # 1. Prefer whatever website the primary search provider already gave us.
        if candidate.website:
            outcome = await self.validator.validate(candidate.website)
            if outcome.ok:
                return WebsiteResolution(
                    website=outcome.normalized_url, resolved_via="overpass", validated=True
                )
            logger.info(
                "Provider-supplied website failed validation, trying fallback resolution",
                extra={
                    "event": "discovery_website_validation_failed",
                    "business_name": candidate.name,
                    "url": candidate.website,
                    "reason": outcome.reason,
                },
            )

        # 2. Fall back to website-resolution-only search (Serper by
        # default; any WebsiteResolverProvider implementation works here),
        # if one is configured.
        if self.fallback_provider is not None:
            resolved_url = await self.fallback_provider.resolve_website(candidate.name, location)
            if resolved_url:
                outcome = await self.validator.validate(resolved_url)
                if outcome.ok:
                    logger.info(
                        "Fallback-resolved website passed validation",
                        extra={
                            "event": "discovery_website_validation_outcome",
                            "provider": self.fallback_provider.name,
                            "business_name": candidate.name,
                            "url": outcome.normalized_url,
                            "validated": True,
                        },
                    )
                    return WebsiteResolution(
                        website=outcome.normalized_url,
                        resolved_via=self.fallback_provider.name,
                        validated=True,
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
                    },
                )
                return WebsiteResolution(
                    website=None,
                    resolved_via="none",
                    validated=False,
                    rejection_reason=outcome.reason,
                )

        # 3. Never fabricate a domain.
        return WebsiteResolution(
            website=None,
            resolved_via="none",
            validated=False,
            rejection_reason="no_verified_website",
        )