"""
False Positive Reduction (Sprint 3).

"Build deterministic rejection reasons... But NEVER use hardcoded domain
lists. Detection must be structural. Evidence-based. Reusable."

Discovery never scrapes a page (see website_validator.py's own
docstring), so this module cannot look at page content the way a real
directory/marketplace/parked-page detector normally would (no schema.org,
no OpenGraph, no page text) -- every signal below has to come from data
this pipeline already has: the candidate's own Features/Evidence/
Verification, and -- the one genuinely new source of structural,
non-hardcoded signal this module introduces -- how a domain behaves
*across many businesses within the same run*.

Two structural detectors, neither one a domain list
-------------------------------------------------------
1. `unsupported_candidate_signal` -- purely per-candidate, purely
   structural: a candidate that is reachable (so it isn't already
   rejected upstream by website_validator) but has *zero* brand-name
   relation, *zero* location corroboration, and *zero* independent
   provider agreement is, structurally, indistinguishable from a generic
   parked page, a holding page, or a coincidentally-reachable unrelated
   domain -- there is nothing else this pipeline could point to as
   evidence it's actually the right site. This reuses Features
   `features.py` already computed; it invents nothing.

2. `DomainObservationRegistry` / `cross_tenant_signal` -- the same
   "observed agreement, not a curated list" philosophy `reliability.py`
   already applies to providers, applied here to domains: a domain that
   gets proposed as a plausible candidate for many DIFFERENT, unrelated
   businesses, with little or no brand-name relation to most of them, is
   structurally behaving like a generic multi-tenant platform
   (aggregator/marketplace/directory/social profile) -- exactly the
   pattern a manually-curated blocklist like website_validator.
   REJECTED_DOMAINS tries to capture ahead of time for a fixed set of
   known offenders, except this signal is observed live, evolves with
   whatever domains this deployment's own traffic actually encounters,
   and never needs a human to add an entry for a new one. This is
   precisely the "Cross-business consensus" extension point Sprint 2C's
   own notes flagged for Sprint 3 ("flagging the same weak domain
   proposed as a top candidate for two unrelated businesses"), scoped
   here (see module docstring below) exactly like
   `reliability.ProviderReliabilityRegistry`.

3. `redirect_domain_mismatch_signal` -- a candidate's original,
   pre-validation domain and the domain it actually validated/redirected
   to (already sitting in its own REACHABILITY Evidence, computed by
   website_validator/evidence.py -- never re-fetched here) land on
   genuinely different registrable domains. A parked domain silently
   redirecting somewhere unrelated looks exactly like this.

Scope of DomainObservationRegistry: identical reasoning to
`reliability.ProviderReliabilityRegistry` -- stateful by design, scoped
to whatever shares one instance (by default, one `WebsiteResolver`'s
lifetime, i.e. one discovery run's worth of businesses), deterministic,
and explainable (every signal cites its own observed counts).
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from application.discovery import canonicalization as canon
from application.discovery import features as features_model
from application.discovery.evidence import EvidenceType
from application.discovery.organization import IdentityCandidate


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class RejectionSignal:
    """One deterministic, structurally-derived false-positive signal.
    `evidence` always cites data already produced elsewhere in the
    pipeline (a Feature's own explanation, an observed count) -- never a
    fabricated justification, exactly like
    `digital_identity.RiskIndicator`."""

    code: str
    severity: str  # "low" | "medium" | "high"
    description: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "description": self.description,
            "evidence": list(self.evidence),
        }


# Exactly zero on every one of these three axes -- brand, location,
# provider agreement -- is the deliberately strict bar: a candidate with
# even weak-but-nonzero support on any one axis is NOT flagged here (that
# weaker case is already covered by digital_identity.py's own
# "weak_brand_match"/"weak_location_match" risk indicators, which are
# advisory, not a rejection reason).
_UNSUPPORTED_THRESHOLD = 0.0

# Cross-tenant signal needs enough independent business observations
# before it fires at all -- a domain seen for only 1-2 businesses so far
# in this run hasn't accumulated enough evidence to distinguish "small
# sample coincidence" from "genuinely generic platform".
_CROSS_TENANT_MIN_BUSINESSES = 4
# Fraction of past proposals for a domain that must show ~zero brand
# relation before the domain reads as a generic platform rather than a
# business that merely has an unusual/rebranded domain name.
_CROSS_TENANT_WEAK_MATCH_RATIO = 0.75
_WEAK_BRAND_MATCH_CEILING = 0.05


def unsupported_candidate_signal(candidate: IdentityCandidate) -> Optional[RejectionSignal]:
    """Structural composite over already-computed Features -- see module
    docstring. Returns None (never fabricates a signal) when the
    candidate isn't reachable at all (nothing new to say beyond what
    website_validator already rejected it for) or when any one axis has
    genuine, non-zero support."""
    feature_set = candidate.feature_store_entry
    if feature_set is None:
        return None
    reachability = feature_set.by_id(features_model.FeatureId.REACHABILITY)
    if reachability is None or (reachability.normalized_value or 0.0) <= 0.0:
        return None

    brand = feature_set.by_id(features_model.FeatureId.BRAND_SIMILARITY)
    location = feature_set.by_id(features_model.FeatureId.LOCATION_CONSISTENCY)
    agreement = feature_set.by_id(features_model.FeatureId.PROVIDER_AGREEMENT)
    brand_v = brand.normalized_value if brand and brand.normalized_value is not None else 0.0
    location_v = location.normalized_value if location and location.normalized_value is not None else 0.0
    agreement_v = agreement.normalized_value if agreement and agreement.normalized_value is not None else 0.0

    if brand_v > _UNSUPPORTED_THRESHOLD or location_v > _UNSUPPORTED_THRESHOLD or agreement_v > _UNSUPPORTED_THRESHOLD:
        return None

    return RejectionSignal(
        code="unsupported_candidate",
        severity="high",
        description=(
            "candidate is reachable, but nothing structurally ties it to this business -- "
            "no brand-name relation, no queried-location corroboration, and no independent "
            "provider agreement"
        ),
        evidence=tuple(
            f.explanation for f in (brand, location, agreement) if f is not None
        ),
    )


def redirect_domain_mismatch_signal(candidate: IdentityCandidate) -> Optional[RejectionSignal]:
    """Compares the candidate's own canonicalized domain against the
    registrable domain it actually validated to (read from its own
    REACHABILITY Evidence's `source`, already computed by
    website_validator/evidence.py -- never re-fetched here)."""
    if candidate.canonical_domain is None or candidate.supporting_evidence is None:
        return None
    reachability_hits = candidate.supporting_evidence.by_type(EvidenceType.REACHABILITY)
    if not reachability_hits or not reachability_hits[0].source:
        return None
    final_url = reachability_hits[0].source
    final_registrable = canon.registrable_domain(final_url)
    original_registrable = candidate.canonical_domain.registrable_domain
    if not final_registrable or not original_registrable or final_registrable == original_registrable:
        return None
    return RejectionSignal(
        code="redirect_domain_mismatch",
        severity="medium",
        description=(
            f"candidate at {original_registrable} validated/redirected to an unrelated "
            f"domain ({final_registrable})"
        ),
        evidence=(f"original={original_registrable}", f"final={final_registrable}", f"validated_url={final_url}"),
    )


class DomainObservationRegistry:
    """See module docstring's "cross_tenant_signal" section. Deliberately
    stateful, mirroring `reliability.ProviderReliabilityRegistry`
    exactly, including its scoping/DI story."""

    def __init__(self) -> None:
        self._by_domain: Dict[str, Dict[str, Any]] = {}

    def observe(self, registrable_domain: str, business_name: str, brand_match_strength: float) -> None:
        if not registrable_domain:
            return
        record = self._by_domain.setdefault(
            registrable_domain, {"businesses": set(), "weak_matches": 0, "total": 0}
        )
        record["businesses"].add(business_name)
        record["total"] += 1
        if brand_match_strength <= _WEAK_BRAND_MATCH_CEILING:
            record["weak_matches"] += 1

    def cross_tenant_signal(self, registrable_domain: str) -> Optional[RejectionSignal]:
        record = self._by_domain.get(registrable_domain)
        if not record:
            return None
        distinct_businesses = record["businesses"]
        if len(distinct_businesses) < _CROSS_TENANT_MIN_BUSINESSES:
            return None
        ratio = record["weak_matches"] / record["total"]
        if ratio < _CROSS_TENANT_WEAK_MATCH_RATIO:
            return None
        return RejectionSignal(
            code="cross_tenant_generic_domain",
            severity="medium",
            description=(
                f"{registrable_domain} was proposed as a candidate for {len(distinct_businesses)} distinct "
                f"businesses observed so far in this run, {ratio:.0%} of them with no meaningful "
                f"brand-name relation -- consistent with a generic multi-tenant platform "
                f"(aggregator/marketplace/directory/social profile) rather than any one business's own site"
            ),
            evidence=(
                f"{record['weak_matches']}/{record['total']} weak-brand-match proposals across "
                f"{len(distinct_businesses)} distinct businesses",
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            domain: {
                "distinct_businesses": len(record["businesses"]),
                "weak_matches": record["weak_matches"],
                "total": record["total"],
            }
            for domain, record in self._by_domain.items()
        }


def evaluate_candidate(
    candidate: IdentityCandidate, registry: DomainObservationRegistry
) -> Tuple[RejectionSignal, ...]:
    """Runs every structural detector against one candidate, in a fixed
    order, returning only the signals that actually fired -- never a
    placeholder for a detector that found nothing (matching every other
    "never fabricate" rule in this package)."""
    signals: List[RejectionSignal] = []
    unsupported = unsupported_candidate_signal(candidate)
    if unsupported is not None:
        signals.append(unsupported)
    redirect = redirect_domain_mismatch_signal(candidate)
    if redirect is not None:
        signals.append(redirect)
    cross_tenant = registry.cross_tenant_signal(candidate.registrable_domain)
    if cross_tenant is not None:
        signals.append(cross_tenant)
    return tuple(signals)
