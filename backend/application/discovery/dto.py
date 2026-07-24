"""
Discovery layer DTOs.

Plain Pydantic models, matching the style already used throughout
application/dto/models.py. Kept in the discovery package (rather than
merged into application/dto/models.py) because these are internal to the
discovery pipeline's stages, not exchanged with AI agents.
"""

from typing import List, Optional

from pydantic import BaseModel, Field


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