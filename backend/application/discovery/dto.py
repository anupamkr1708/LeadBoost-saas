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
    status: str  # "validated" | "no_website" | "duplicate" | "validation_failed" | "quota_exceeded" | "pipeline_error"
    lead_id: Optional[int] = None
    pipeline_status: Optional[str] = None  # SUCCESS | PARTIAL_SUCCESS | FAILED
    reason: Optional[str] = None


class DiscoveryResponse(BaseModel):
    """Top-level response of DiscoveryService.discover_and_create_leads().

    `businesses_found` is the true total number of candidates the search
    provider returned. `businesses` is capped at `requested_limit` entries
    (validated leads first, then rejected/no-website businesses filling
    any remaining slots for diagnostic value) -- it will not necessarily
    equal `businesses_found` when more candidates were found than asked
    for.
    """

    query: str
    category: str
    location: str
    requested_limit: int
    businesses_found: int
    businesses: List[LeadCreationOutcome] = Field(default_factory=list)
    duration_ms: int = 0