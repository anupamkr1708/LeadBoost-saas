"""
Evidence Model (Sprint 1 foundation).

This module is the internal foundation the rest of Discovery is migrating
onto: instead of a stage handing the next stage a bare URL and a boolean,
every stage that has an opinion about a candidate website can attach a
piece of *Evidence* explaining exactly what it observed, how much that
observation should move confidence, and why. Nothing here is exposed on
any public DTO (application.discovery.dto) or provider interface
(application.discovery.providers.base) yet -- it is consumed only inside
application.discovery.website_resolver in this sprint, purely to build
richer internal state and observability without changing what
WebsiteResolver.resolve() returns.

Design goals (see the Sprint 1 brief):
  - Deterministic: every confidence number here is a fixed, documented
    arithmetic combination of evidence -- no randomness, no ML, no
    embeddings.
  - Explainable: every Evidence carries a human-readable `reason`, and
    EvidenceBundle.explain() renders the full chain that produced a given
    confidence value.
  - Testable: every function here is pure (Evidence/EvidenceBundle/
    WebsiteCandidate/CandidatePool are plain in-memory objects; nothing in
    this module performs I/O). See discovery/tests/test_evidence.py.
  - Extensible: EvidenceType is intentionally broader than what Sprint 1
    actually populates (e.g. SCHEMA_PRESENCE, OPENGRAPH, CONTACT_PAGE are
    defined but never constructed by anything today) so a future
    Verification Engine can start emitting them without touching this
    enum's existing members or anyone who already pattern-matches on it.
  - Never fabricated: every helper below that derives Evidence from an
    observation returns `None` (or an empty list) when there is nothing to
    report, rather than inventing a zero-value placeholder. A bundle's
    evidence list is exactly "what was actually observed", nothing more.

Vocabulary used throughout this module and its caller:
  - `Evidence`      -- one atomic, attributed observation.
  - `EvidenceBundle` -- an accumulator of Evidence for one subject (here,
                        one candidate website), with a deterministic
                        confidence derived from it.
  - `WebsiteCandidate` -- one URL considered for one business, plus the
                        EvidenceBundle explaining why it might (or might
                        not) be the right one.
  - `CandidatePool`  -- every WebsiteCandidate considered for one business
                        during one resolution, in the order they were
                        considered. This is the "Business has Evidence;
                        Website is one piece of evidence" shift made
                        concrete: a business no longer *is* a website, it
                        *has* a pool of website candidates, each backed by
                        evidence.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import time

from application.discovery import grounding
from application.discovery.website_validator import ValidationOutcome, domain_of


# ---------------------------------------------------------------------------
# Evidence taxonomy
# ---------------------------------------------------------------------------


class EvidenceCategory(Enum):
    """Coarse grouping used to filter/summarize an EvidenceBundle -- maps
    to the "kinds of evidence objects" named in the Sprint 1 brief (Website
    Evidence, Business Evidence, Provider Evidence, Location Evidence,
    Identity Evidence, Verification Evidence). "Website Evidence" is not a
    separate category of its own here: a WebsiteCandidate's EvidenceBundle
    *is* that candidate's website evidence -- IDENTITY/LOCATION/
    VERIFICATION/PROVIDER evidence about one URL, taken together.
    """

    PROVIDER = "provider"
    IDENTITY = "identity"
    LOCATION = "location"
    VERIFICATION = "verification"
    BUSINESS = "business"


class EvidenceType(Enum):
    """Every kind of observation the evidence model can represent.

    Members marked "(reserved)" below are deliberately defined but not yet
    constructed by any function in this module or any caller in Sprint 1 --
    they exist so a future Verification Engine (schema.org / OpenGraph /
    contact-page presence, all of which require the actual page HTML that
    only the real scrape inside LeadPipeline downloads today) can start
    emitting them without a breaking change to this enum, to
    EvidenceCategory, or to any code that already matches on either.
    """

    # -- Populated in Sprint 1 --
    PROVIDER_FOUND = "provider_found"
    PROVIDER_AGREEMENT = "provider_agreement"
    PROVIDER_DISAGREEMENT = "provider_disagreement"
    BRAND_MATCH = "brand_match"
    LOCATION_MATCH = "location_match"
    REACHABILITY = "reachability"
    HTTPS = "https"
    CANONICAL_URL = "canonical_url"

    # -- Reserved for a future Verification/Provider Evidence Engine --
    PHONE_MATCH = "phone_match"
    ADDRESS_MATCH = "address_match"
    SCHEMA_PRESENCE = "schema_presence"
    ORGANIZATION_SCHEMA = "organization_schema"
    OPENGRAPH = "opengraph"
    ORGANIZATION_METADATA = "organization_metadata"
    CONTACT_PAGE = "contact_page"
    PRIVACY_PAGE = "privacy_page"
    TERMS_PAGE = "terms_page"
    SOCIAL_LINKS = "social_links"
    TITLE_MATCH = "title_match"
    SNIPPET_MATCH = "snippet_match"
    DOMAIN_QUALITY = "domain_quality"


# Single source of truth for type -> category. A dict instead of scattering
# `if type in (...)` checks across the codebase -- adding a new
# EvidenceType later is a one-line addition here, not a hunt through every
# consumer (the same "generic table over special-case logic" principle
# application.discovery.providers.overpass_provider already uses for its
# category -> OSM-tag mapping).
_CATEGORY_BY_TYPE: Dict[EvidenceType, EvidenceCategory] = {
    EvidenceType.PROVIDER_FOUND: EvidenceCategory.PROVIDER,
    EvidenceType.PROVIDER_AGREEMENT: EvidenceCategory.PROVIDER,
    EvidenceType.PROVIDER_DISAGREEMENT: EvidenceCategory.PROVIDER,
    EvidenceType.BRAND_MATCH: EvidenceCategory.IDENTITY,
    EvidenceType.TITLE_MATCH: EvidenceCategory.IDENTITY,
    EvidenceType.SNIPPET_MATCH: EvidenceCategory.IDENTITY,
    EvidenceType.DOMAIN_QUALITY: EvidenceCategory.IDENTITY,
    EvidenceType.PHONE_MATCH: EvidenceCategory.IDENTITY,
    EvidenceType.ADDRESS_MATCH: EvidenceCategory.IDENTITY,
    EvidenceType.LOCATION_MATCH: EvidenceCategory.LOCATION,
    EvidenceType.REACHABILITY: EvidenceCategory.VERIFICATION,
    EvidenceType.HTTPS: EvidenceCategory.VERIFICATION,
    EvidenceType.CANONICAL_URL: EvidenceCategory.VERIFICATION,
    EvidenceType.SCHEMA_PRESENCE: EvidenceCategory.VERIFICATION,
    EvidenceType.ORGANIZATION_SCHEMA: EvidenceCategory.VERIFICATION,
    EvidenceType.OPENGRAPH: EvidenceCategory.VERIFICATION,
    EvidenceType.CONTACT_PAGE: EvidenceCategory.VERIFICATION,
    EvidenceType.PRIVACY_PAGE: EvidenceCategory.VERIFICATION,
    EvidenceType.TERMS_PAGE: EvidenceCategory.VERIFICATION,
    EvidenceType.SOCIAL_LINKS: EvidenceCategory.VERIFICATION,
    EvidenceType.ORGANIZATION_METADATA: EvidenceCategory.BUSINESS,
}


# ---------------------------------------------------------------------------
# Confidence weights
# ---------------------------------------------------------------------------
#
# Every weight below is a deliberate point value in a 0.0-1.0 confidence
# budget (a consistent, explainable scale -- not a calibrated probability
# of correctness). EvidenceBundle.confidence sums these deltas and clamps
# the result to [0.0, 1.0], so the weights below are designed to be
# combined additively without needing a normalization step per-candidate.

# A search/resolution provider returned *a* candidate website at all. This
# is the floor of the budget: most provider hits do turn out correct in
# practice, but a bare hit with nothing else corroborating it is
# "plausible", not yet "confident".
PROVIDER_FOUND_WEIGHT = 0.30

# Maximum contribution for a business-name-to-domain match, scaled by
# application.discovery.grounding.brand_match_strength's own continuous
# 0.0-1.0 output (see brand_identity_evidence below) -- an exact match
# ("Nike" -> nike.com) earns the full weight; a weak partial match earns
# proportionally less, rather than an all-or-nothing bonus.
BRAND_MATCH_MAX_WEIGHT = 0.25

# The candidate's own domain/address text corroborates the queried
# location (e.g. a structured address matching the query city, or a
# domain like "metroshoesmumbai.com"). Half credit when only the weaker,
# domain-text signal is available -- mirrors the same full/half split
# application.discovery.ranking already uses for the same reason.
LOCATION_MATCH_WEIGHT = 0.15
LOCATION_MATCH_WEAK_WEIGHT = LOCATION_MATCH_WEIGHT * 0.5

# The candidate actually responded successfully to WebsiteValidator's
# reachability check. Evidence the domain is a real, live site -- not
# evidence it's the *correct* site (brand/location matching is what
# establishes that); a dead/parked domain can never earn this.
REACHABILITY_WEIGHT = 0.15

# HTTPS is a near-universal, generic quality signal today -- kept small on
# purpose so it can never be decisive on its own, only a tiebreaker.
HTTPS_WEIGHT = 0.03

# The validator's final URL (after redirects) is the site's root, not a
# deep sub-page -- a small, generic signal for the same reason HTTPS is
# small.
CANONICAL_URL_WEIGHT = 0.02

# Two or more *independent* providers converging on the same normalized
# domain for the same business is one of the strongest signals available
# without a real crawl -- independent corroboration is exactly what an
# evidence-driven system is supposed to reward over a single provider's
# opinion. Applied once per additional agreeing provider found in the pool.
PROVIDER_AGREEMENT_WEIGHT = 0.25

# Providers actively disagreeing (different domains for the same business)
# means at least one of them is wrong -- a real negative signal, but
# smaller in magnitude than the agreement bonus, since disagreement is
# common and is already independently resolved by validation/grounding
# elsewhere in the pipeline; it should nudge confidence, not dominate it.
PROVIDER_DISAGREEMENT_PENALTY = -0.10


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Evidence / EvidenceBundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """One atomic, attributed observation. Immutable -- once a stage
    observes something, that observation doesn't change; only the set of
    observations for a subject grows (via EvidenceBundle.add)."""

    type: EvidenceType
    provider: str
    confidence_delta: float
    reason: str
    source: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    @property
    def category(self) -> EvidenceCategory:
        return _CATEGORY_BY_TYPE[self.type]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "category": self.category.value,
            "provider": self.provider,
            "confidence_delta": self.confidence_delta,
            "reason": self.reason,
            "source": self.source,
            "timestamp": self.timestamp,
        }


class EvidenceBundle:
    """Accumulates Evidence for one subject and derives a deterministic
    confidence from it. Cheap by construction: a handful of dataclass
    instances and a sum -- no caching layer needed at this scale."""

    __slots__ = ("_evidence",)

    def __init__(self) -> None:
        self._evidence: List[Evidence] = []

    def add(self, evidence: Optional[Evidence]) -> None:
        """No-op for `None` -- callers pass through the `Optional[Evidence]`
        result of a "did we observe this?" helper directly, so a bundle
        never accumulates a fabricated, meaningless entry for something
        that wasn't actually observed."""
        if evidence is not None:
            self._evidence.append(evidence)

    def extend(self, evidence_list: List[Evidence]) -> None:
        for item in evidence_list:
            self.add(item)

    @property
    def items(self) -> List[Evidence]:
        return list(self._evidence)

    @property
    def confidence(self) -> float:
        total = sum(e.confidence_delta for e in self._evidence)
        return round(_clamp(total, 0.0, 1.0), 3)

    def by_category(self, category: EvidenceCategory) -> List[Evidence]:
        return [e for e in self._evidence if e.category == category]

    def by_type(self, evidence_type: EvidenceType) -> List[Evidence]:
        return [e for e in self._evidence if e.type == evidence_type]

    def explain(self) -> List[str]:
        """Human-readable rendering of the full chain that produced
        `confidence` -- the "never a black box" requirement made
        concrete. Intended for structured logging, not end-user text."""
        return [
            f"[{e.category.value}/{e.type.value}] {e.reason} "
            f"({e.confidence_delta:+.3f} via {e.provider})"
            for e in self._evidence
        ]

    def to_dict(self) -> Dict[str, Any]:
        return {"confidence": self.confidence, "evidence": [e.to_dict() for e in self._evidence]}


# ---------------------------------------------------------------------------
# WebsiteCandidate / CandidatePool
# ---------------------------------------------------------------------------


@dataclass
class WebsiteCandidate:
    """One URL considered for one business during one resolution, plus the
    evidence explaining why it might (or might not) be the right one.
    `validated`/`rejection_reason` mirror the meaning of the equivalent
    fields on the public WebsiteResolution DTO for this one candidate,
    rather than for the resolution as a whole."""

    url: str
    domain: str
    provider: str
    validated: bool
    rejection_reason: Optional[str] = None
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)

    @property
    def confidence(self) -> float:
        return self.evidence.confidence


class CandidatePool:
    """Every WebsiteCandidate considered for one business during one
    resolution, in the order they were considered (today, that order *is*
    provider priority order: primary search provider first, fallback
    resolver second -- see website_resolver.py). This is what lets
    WebsiteResolver stop thinking "the business's website" and start
    thinking "the business's evidence, of which a website candidate is one
    piece" -- without yet changing which candidate wins.

    Sprint 1 only ever adds a candidate when a provider actually returned
    one (never a placeholder for "provider found nothing") -- an empty
    pool, or a pool where every candidate has `validated=False`, is itself
    meaningful and explainable, not an error case.
    """

    def __init__(self, business_name: str, location: str):
        self.business_name = business_name
        self.location = location
        self.candidates: List[WebsiteCandidate] = []

    def add_candidate(
        self,
        *,
        url: str,
        provider: str,
        validated: bool,
        rejection_reason: Optional[str] = None,
    ) -> WebsiteCandidate:
        candidate = WebsiteCandidate(
            url=url,
            domain=domain_of(url),
            provider=provider,
            validated=validated,
            rejection_reason=rejection_reason,
        )
        candidate.evidence.add(
            Evidence(
                type=EvidenceType.PROVIDER_FOUND,
                provider=provider,
                confidence_delta=PROVIDER_FOUND_WEIGHT,
                reason=f"{provider} returned this URL as a candidate website",
                source=url,
            )
        )
        self.candidates.append(candidate)
        return candidate

    def best_validated(self) -> Optional[WebsiteCandidate]:
        """First validated candidate in priority order -- matches today's
        "first provider to pass validation wins" rule exactly. Sprint 2
        can change this to "highest-confidence validated candidate" as a
        one-function change once ties/near-ties across providers are worth
        distinguishing; every candidate already carries the confidence
        needed to do that."""
        for candidate in self.candidates:
            if candidate.validated:
                return candidate
        return None

    def summary(self) -> List[Dict[str, Any]]:
        """Structured, log-friendly view of the whole pool -- every
        candidate considered, its confidence, and the evidence chain that
        produced it. Used for observability only; never consulted to
        change WebsiteResolver's actual decision in Sprint 1."""
        return [
            {
                "provider": c.provider,
                "url": c.url,
                "domain": c.domain,
                "validated": c.validated,
                "rejection_reason": c.rejection_reason,
                "confidence": c.confidence,
                "evidence": c.evidence.explain(),
            }
            for c in self.candidates
        ]


# ---------------------------------------------------------------------------
# Evidence derivation helpers
# ---------------------------------------------------------------------------
#
# Each of these answers "did we actually observe this?" and returns
# `None`/`[]` when the answer is no -- see EvidenceBundle.add's docstring
# for why that matters. None of these perform I/O: they only look at
# values already computed by their caller (a ValidationOutcome already
# returned by WebsiteValidator, a URL already returned by a provider).


def brand_identity_evidence(business_name: str, domain: str, provider: str) -> Optional[Evidence]:
    """Wraps application.discovery.grounding.brand_match_strength (already
    used by ranking.py and serper_provider.py) as Evidence rather than
    duplicating brand-matching logic here -- one grounding implementation,
    multiple consumers."""
    strength = grounding.brand_match_strength(business_name, domain)
    if strength <= 0.0:
        return None
    return Evidence(
        type=EvidenceType.BRAND_MATCH,
        provider=provider,
        confidence_delta=round(strength * BRAND_MATCH_MAX_WEIGHT, 4),
        reason=f"business name matches domain root with strength {strength:.2f}",
        source=domain,
    )


def location_corroboration_evidence(
    location: str, domain: str, address: Optional[str], provider: str
) -> Optional[Evidence]:
    """Full credit when the business's own structured address corroborates
    the query location; half credit for the weaker domain-text signal
    (e.g. "metroshoesmumbai.com" for a Mumbai query) when no address is
    available -- same full/half distinction application.discovery.ranking
    already applies for the identical reason."""
    if address and grounding.location_mentioned(address, location):
        return Evidence(
            type=EvidenceType.LOCATION_MATCH,
            provider=provider,
            confidence_delta=LOCATION_MATCH_WEIGHT,
            reason="business address corroborates the queried location",
            source=address,
        )
    if grounding.location_mentioned(domain, location):
        return Evidence(
            type=EvidenceType.LOCATION_MATCH,
            provider=provider,
            confidence_delta=LOCATION_MATCH_WEAK_WEIGHT,
            reason="domain text corroborates the queried location",
            source=domain,
        )
    return None


def verification_evidence(outcome: ValidationOutcome, provider: str) -> List[Evidence]:
    """Derives Evidence purely from a ValidationOutcome WebsiteValidator
    already computed -- no new HTTP request. A failed outcome contributes
    no evidence here (its public rejection_reason already explains it on
    the WebsiteResolution DTO); only a passing check is positive evidence
    that the site is live."""
    evidence: List[Evidence] = []
    if not outcome.ok or not outcome.normalized_url:
        return evidence

    evidence.append(
        Evidence(
            type=EvidenceType.REACHABILITY,
            provider=provider,
            confidence_delta=REACHABILITY_WEIGHT,
            reason="site responded successfully to a reachability check",
            source=outcome.normalized_url,
        )
    )
    if outcome.normalized_url.lower().startswith("https://"):
        evidence.append(
            Evidence(
                type=EvidenceType.HTTPS,
                provider=provider,
                confidence_delta=HTTPS_WEIGHT,
                reason="site serves over HTTPS",
                source=outcome.normalized_url,
            )
        )
    if urlparse(outcome.normalized_url).path in ("", "/"):
        evidence.append(
            Evidence(
                type=EvidenceType.CANONICAL_URL,
                provider=provider,
                confidence_delta=CANONICAL_URL_WEIGHT,
                reason="resolved to the site's root/canonical URL, not a sub-page",
                source=outcome.normalized_url,
            )
        )
    return evidence


def provider_agreement_evidence(pool: CandidatePool, candidate: WebsiteCandidate) -> List[Evidence]:
    """Compares `candidate` against every other candidate already in
    `pool` from a *different* provider. Same domain -> agreement evidence
    (added to both candidates, since the corroboration is mutual);
    different domain -> a small disagreement penalty on `candidate` only
    (the earlier candidate already had its own chance to be judged on its
    own evidence when it was added).

    Only ever called with pool candidates that were genuinely produced by
    a provider -- never fabricates a comparison against a provider that
    found nothing (see CandidatePool's docstring)."""
    evidence: List[Evidence] = []
    if not candidate.domain:
        return evidence

    for other in pool.candidates:
        if other is candidate or other.provider == candidate.provider or not other.domain:
            continue
        if other.domain == candidate.domain:
            reason = f"{other.provider} and {candidate.provider} independently agree on {candidate.domain}"
            agreement = Evidence(
                type=EvidenceType.PROVIDER_AGREEMENT,
                provider=candidate.provider,
                confidence_delta=PROVIDER_AGREEMENT_WEIGHT,
                reason=reason,
                source=candidate.domain,
            )
            evidence.append(agreement)
            other.evidence.add(
                Evidence(
                    type=EvidenceType.PROVIDER_AGREEMENT,
                    provider=other.provider,
                    confidence_delta=PROVIDER_AGREEMENT_WEIGHT,
                    reason=reason,
                    source=candidate.domain,
                )
            )
        else:
            evidence.append(
                Evidence(
                    type=EvidenceType.PROVIDER_DISAGREEMENT,
                    provider=candidate.provider,
                    confidence_delta=PROVIDER_DISAGREEMENT_PENALTY,
                    reason=(
                        f"{other.provider} suggested {other.domain} while "
                        f"{candidate.provider} suggested {candidate.domain}"
                    ),
                    source=candidate.domain,
                )
            )
    return evidence


# ---------------------------------------------------------------------------
# Verification Engine (extension point for Sprint 2+)
# ---------------------------------------------------------------------------


class VerificationEngine(ABC):
    """Interface for turning a website check into Evidence. WebsiteResolver
    depends on this interface (constructor-injectable, defaulted -- the
    same DI pattern already used for `validator`/`fallback_provider`), not
    on a concrete engine, so a future heavier engine (schema.org presence,
    OpenGraph tags, contact/privacy/terms page detection -- all of which
    need the actual page HTML) can be swapped in without WebsiteResolver's
    call site changing. That heavier engine should reuse the HTML the real
    scrape (core.infrastructure.scraping.scraper.TieredScraper, inside
    LeadPipeline) already downloads once a Lead exists, rather than
    fetching pages a second time here -- Discovery must stay a cheap
    pre-check, not a second scraper (see website_validator.py's own
    docstring)."""

    @abstractmethod
    def verify(self, outcome: ValidationOutcome, provider: str) -> List[Evidence]:
        """Returns whatever Evidence can be derived for one candidate's
        validation outcome. Must not perform additional network I/O beyond
        what `outcome` already represents; must return `[]` rather than
        fabricate evidence when nothing was observed."""


class LightweightVerificationEngine(VerificationEngine):
    """Sprint 1's concrete VerificationEngine: everything it can derive
    from a ValidationOutcome that WebsiteValidator already computed, and
    nothing more. This is the default WebsiteResolver uses today."""

    def verify(self, outcome: ValidationOutcome, provider: str) -> List[Evidence]:
        return verification_evidence(outcome, provider)
