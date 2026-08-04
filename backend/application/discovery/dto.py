"""
Discovery layer DTOs.

Plain Pydantic models, matching the style already used throughout
application/dto/models.py. Kept in the discovery package (rather than
merged into application/dto/models.py) because these are internal to the
discovery pipeline's stages, not exchanged with AI agents.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ParsedQuery(BaseModel):
    """Output of query_parser.QueryParser."""

    category: str
    location: str
    limit: int = 20
    modifier: Optional[str] = None  # e.g. "top" -- informational only
    raw_query: str


class BusinessCandidate(BaseModel):
    """A normalized business record from a search provider, before website
    resolution/validation."""

    name: str
    category: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    website: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    source: str = "overpass"


class WebsiteResolution(BaseModel):
    """Output of website_resolver.WebsiteResolver for one business."""

    website: Optional[str] = None
    resolved_via: str = "none"  # "overpass" | "brave" | "none"
    validated: bool = False
    rejection_reason: Optional[str] = None


class DiscoveredBusiness(BaseModel):
    """A candidate merged with its resolution -- the unit duplicate
    detection and ranking operate on."""

    candidate: BusinessCandidate
    resolution: WebsiteResolution
    is_duplicate: bool = False
    duplicate_key: Optional[str] = None
    rank_score: float = 0.0
    # Sprint 4 (Discovery Quality Refinement): the canonical
    # application.discovery.digital_identity.VerifiedDigitalIdentity for
    # this business, when the caller resolved it via
    # WebsiteResolver.resolve_with_digital_identity() rather than plain
    # resolve() -- see discovery_service.py's _resolve_and_validate and
    # ranking.py's own module docstring. Optional and defaulted to None so
    # every existing construction site (and any caller still using plain
    # resolve()) keeps working unchanged; ranking.py falls back to
    # recomputing from `candidate`/`resolution` directly whenever this is
    # absent. Typed as `Any` (not the dataclass itself) because
    # VerifiedDigitalIdentity is a plain dataclass, not a Pydantic model,
    # and this field is never validated or serialized -- it is read once
    # by ranking.py and discarded, exactly like `resolution` is read by
    # duplicate_detector.py without ever being serialized out of this DTO.
    identity: Optional[Any] = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class LeadCreationOutcome(BaseModel):
    """One entry in the Discovery API's response -- what happened to one
    discovered business."""

    name: str
    website: Optional[str] = None
    status: str  # "validated" | "not_selected" | "no_website" | "duplicate" | "validation_failed" | "quota_exceeded" | "pipeline_error"
    lead_id: Optional[int] = None
    pipeline_status: Optional[str] = None  # SUCCESS | PARTIAL_SUCCESS | FAILED
    reason: Optional[str] = None


class DiscoveryResponse(BaseModel):
    """Top-level response of DiscoveryService.discover_and_create_leads().

    `businesses_found` is the true total number of candidates the search
    provider returned. `businesses` reports the outcome of every one of
    them -- it is NOT capped at `requested_limit`:

      - the top `requested_limit` validated businesses (by rank) get a
        Lead created and run through the full pipeline: status
        "validated" (or "quota_exceeded"/"pipeline_error"/"duplicate" if
        Lead creation itself couldn't proceed).
      - validated businesses beyond the limit are reported with status
        "not_selected" -- found and confirmed real, just not among the
        top `requested_limit` by rank. No Lead is created for these (no
        pipeline cost is spent on results that weren't asked for).
      - businesses that never validated get "no_website" or
        "validation_failed", with `reason` explaining why.

    This full accounting is what lets a response like "found 15 shoe
    stores, here are the 3 that were selected" also show *why* the other
    12 weren't -- duplicates, unreachable sites, or simply ranked lower
    than the requested limit -- instead of silently discarding them.
    """

    query: str
    category: str
    location: str
    requested_limit: int
    businesses_found: int
    businesses: List[LeadCreationOutcome] = Field(default_factory=list)
    duration_ms: int = 0
    # H4: run-level provider reliability / domain observation metrics --
    # already computed by ProviderReliabilityRegistry.to_dict() /
    # DomainObservationRegistry.to_dict() (reliability.py, false_positive.py)
    # but previously never surfaced past IdentityResolutionEngine, several
    # layers deep. Optional and defaulted to None: every existing caller
    # that doesn't read this field, and every existing serialization of a
    # DiscoveryResponse, is unaffected.
    provider_metrics: Optional[Dict[str, Any]] = None