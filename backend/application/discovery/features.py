"""
Feature Extraction Engine (Sprint 2B).

Sprint 2A introduced Features, but only as a side-effect of Identity
Resolution: `application.discovery.identity.extract_features` lived
inside the identity module, and `WebsiteCandidateGroup.confidence` still
read `self.evidence.confidence` directly -- Evidence was being consumed
downstream of Identity, not upstream of it. That is exactly the "leakage
between responsibilities" Sprint 2B's brief calls out.

This module is the fix: a standalone Feature Extraction Engine that
depends only on `application.discovery.evidence` (raw data),
`application.discovery.grounding` (shared deterministic checks), and
`application.discovery.dto` (the business record) -- it does NOT import
`application.discovery.identity`, on purpose. That is what makes
    Evidence -> Feature Extraction -> Verification -> Confidence -> Identity
a genuine one-way pipeline instead of a cycle: Identity is downstream of
Features, so Features cannot depend on Identity's types.
`application.discovery.identity.WebsiteCandidateGroup` therefore hands
this engine a small, local `CandidateContext` (domain/urls/providers/
evidence/verified) rather than itself -- see that dataclass below.

Sprint 2A's own `identity.Feature`/`identity.FeatureName`/
`identity.extract_features` are UNCHANGED and remain in use -- see
identity.py's module docstring for why (they are already
regression-tested and still correctly serve their original, narrower
purpose: per-candidate-group identity-matching signals feeding
`WebsiteCandidateGroup.identity_confidence` and
`identity.verify_identity_features`). This module's `FeatureId`/
`ExtractedFeature` are a deliberately broader, richer, independent
taxonomy -- the canonical "Features" the Sprint 2B brief means when it
says "everything downstream should consume Features only" -- consumed by
`application.discovery.verification` and `application.discovery.confidence`,
never by anything that still needs raw Evidence.

Feature design (per the Sprint 2B brief): every ExtractedFeature carries
a feature id, a normalized value, a raw value, a confidence impact,
supporting evidence, source providers, an explanation, and a timestamp --
and is immutable (frozen dataclass; list-shaped fields are tuples).

Never fabricated: exactly like evidence.py and identity.py before it, a
feature with no underlying data source (WEBSITE_STRUCTURE, still
reserved in Sprint 2B -- nothing in Discovery scrapes a page) is
represented with `raw_value=None`, `normalized_value=None`,
`confidence_impact=0.0`, and an explicit explanation -- never fabricated
data. "Missing data should produce missing features", not invented ones.

No double-counting, by construction: each ExtractedFeature's
`confidence_impact`, where non-zero, is read back from the exact
Evidence entries evidence.py already computed (via `EvidenceBundle.by_type`)
rather than recomputed from grounding.py a second time -- one source of
truth for a given weight, never two independently-tunable numbers that
could drift apart. `application.discovery.confidence`'s propagation
stages each draw on a disjoint subset of these features, so nothing is
summed twice across stages either.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Tuple

from application.discovery import evidence as evidence_model
from application.discovery import grounding
from application.discovery.dto import BusinessCandidate


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Candidate context (the seam that keeps this module identity.py-free)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateContext:
    """Everything the Feature Extraction Engine needs about one merged
    website candidate, without depending on
    `application.discovery.identity.WebsiteCandidateGroup`. Built by
    identity.py from a WebsiteCandidateGroup's own fields immediately
    before calling `extract()` -- a thin, one-way adapter, never the
    reverse."""

    domain: str
    urls: Tuple[str, ...]
    contributing_providers: Tuple[str, ...]
    evidence: "evidence_model.EvidenceBundle"
    verified: bool


# ---------------------------------------------------------------------------
# Feature taxonomy
# ---------------------------------------------------------------------------


class FeatureId(Enum):
    """The Sprint 2B brief's example feature list, each mapped to a
    concrete, generic, deterministic computation below. Always present in
    full for every extraction -- reserved/no-data members included -- so
    a caller iterating a FeatureSet never needs a membership check."""

    BRAND_SIMILARITY = "brand_similarity"
    CANONICAL_DOMAIN = "canonical_domain"
    REACHABILITY = "reachability"
    HTTPS = "https"
    DOMAIN_QUALITY = "domain_quality"                # composite of the 3 above
    PROVIDER_AGREEMENT = "provider_agreement"
    LOCATION_CONSISTENCY = "location_consistency"
    BUSINESS_COMPLETENESS = "business_completeness"   # website/phone/address on the business record
    CONTACT_COMPLETENESS = "contact_completeness"     # phone/address/email as reach-out channels
    VERIFICATION_READINESS = "verification_readiness"  # is there enough signal to verify confidently
    WEBSITE_STRUCTURE = "website_structure"           # reserved -- no page is ever scraped here
    EVIDENCE_DENSITY = "evidence_density"             # how much was actually observed, not how positive


_NO_DATA_EXPLANATION = (
    "not available in this pipeline -- no page content is fetched before "
    "a Lead exists, so this signal has nothing to compare against yet"
)

# A generic, fixed, documented ceiling for EVIDENCE_DENSITY's normalization
# -- the number of distinct EvidenceType members Sprint 1 actually
# populates today (PROVIDER_FOUND, BRAND_MATCH, LOCATION_MATCH,
# REACHABILITY, HTTPS, CANONICAL_URL). Not calibrated/learned -- just "how
# many kinds of observation could this candidate possibly have", so a
# candidate with all of them reads as fully dense (1.0) and a bare
# provider hit reads as sparse (~0.17), without needing a second,
# separately-maintained constant.
_MAX_OBSERVABLE_EVIDENCE_TYPES = 6


@dataclass(frozen=True)
class ExtractedFeature:
    """One atomic, named, evidence-derived observation -- the canonical
    Feature shape for Sprint 2B's pipeline. Immutable: list-shaped fields
    are tuples, not lists, so an ExtractedFeature can never be mutated
    after construction (stronger than a merely-frozen dataclass with
    mutable-typed fields)."""

    feature_id: FeatureId
    normalized_value: Optional[float]  # 0.0-1.0, or None if not applicable
    raw_value: Optional[Any]
    confidence_impact: float  # 0.0 for informational/composite/reserved features
    supporting_evidence: Tuple["evidence_model.Evidence", ...]
    source_providers: Tuple[str, ...]
    explanation: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature_id": self.feature_id.value,
            "normalized_value": self.normalized_value,
            "raw_value": self.raw_value,
            "confidence_impact": self.confidence_impact,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "source_providers": list(self.source_providers),
            "explanation": self.explanation,
            "timestamp": self.timestamp,
        }


def _reserved_feature(feature_id: FeatureId) -> ExtractedFeature:
    return ExtractedFeature(
        feature_id=feature_id,
        normalized_value=None,
        raw_value=None,
        confidence_impact=0.0,
        supporting_evidence=(),
        source_providers=(),
        explanation=_NO_DATA_EXPLANATION,
    )


@dataclass(frozen=True)
class FeatureSet:
    """An immutable, ordered collection of every ExtractedFeature produced
    for one candidate context."""

    domain: str
    features: Tuple[ExtractedFeature, ...]

    def by_id(self, feature_id: FeatureId) -> Optional[ExtractedFeature]:
        return next((f for f in self.features if f.feature_id is feature_id), None)

    def to_dict(self) -> Dict[str, Any]:
        return {"domain": self.domain, "features": [f.to_dict() for f in self.features]}


# ---------------------------------------------------------------------------
# Feature Store (in-memory only, per the Sprint 2B brief)
# ---------------------------------------------------------------------------


class FeatureStore:
    """Lightweight, in-memory-only store of FeatureSets keyed by domain,
    for one BusinessIdentity resolution. No database, no persistence
    across resolutions -- this is scoped to a single `resolve()` call,
    exactly like `application.discovery.evidence.CandidatePool` already
    is. This is the seam the Sprint 2B brief's "Business Identity ->
    Feature Store -> Verification -> Confidence -> Decision" diagram
    describes: BusinessIdentity holds a reference to one of these rather
    than embedding feature lists inline."""

    def __init__(self) -> None:
        self._by_domain: Dict[str, FeatureSet] = {}
        self._order: List[str] = []

    def put(self, feature_set: FeatureSet) -> None:
        if feature_set.domain not in self._by_domain:
            self._order.append(feature_set.domain)
        self._by_domain[feature_set.domain] = feature_set

    def get(self, domain: str) -> Optional[FeatureSet]:
        return self._by_domain.get(domain)

    def domains(self) -> List[str]:
        return list(self._order)

    def all(self) -> List[FeatureSet]:
        return [self._by_domain[d] for d in self._order]

    def to_dict(self) -> Dict[str, Any]:
        return {domain: self._by_domain[domain].to_dict() for domain in self._order}


# ---------------------------------------------------------------------------
# Feature Extraction Engine
# ---------------------------------------------------------------------------


class FeatureExtractionEngine(ABC):
    """Interface identity.py depends on (constructor-injectable pattern
    consistent with every other extension point in this package), not on
    a concrete engine, so a future engine that extracts real
    WEBSITE_STRUCTURE/CONTACT_COMPLETENESS data (once a page is actually
    scraped) can be swapped in without callers changing."""

    @abstractmethod
    def extract(
        self, business: BusinessCandidate, context: CandidateContext, location: str
    ) -> FeatureSet:
        """Pure: reads only `business`, `context.evidence`, and
        `location` -- no I/O, no new provider/HTTP calls."""


class GenericFeatureExtractionEngine(FeatureExtractionEngine):
    """Sprint 2B's concrete, fully generic engine. Every non-zero
    `confidence_impact` below is read back from Evidence evidence.py
    already computed (see module docstring) -- this function invents no
    new weights."""

    def extract(
        self, business: BusinessCandidate, context: CandidateContext, location: str
    ) -> FeatureSet:
        bundle = context.evidence
        features: List[ExtractedFeature] = [
            self._brand_similarity(business, context, bundle),
            self._reachability(context, bundle),
            self._https(context, bundle),
            self._canonical_domain(context, bundle),
        ]
        # DOMAIN_QUALITY composes the 3 just computed -- kept after them so
        # it can read their already-derived normalized values instead of
        # re-deriving from `bundle` a second time.
        features.append(self._domain_quality(context, features))
        features.append(self._provider_agreement(context, bundle))
        features.append(self._location_consistency(business, context, bundle, location))
        features.append(self._business_completeness(business))
        features.append(self._contact_completeness(business))
        features.append(self._verification_readiness(business, features))
        features.append(_reserved_feature(FeatureId.WEBSITE_STRUCTURE))
        features.append(self._evidence_density(context, bundle))
        return FeatureSet(domain=context.domain, features=tuple(features))

    # -- Domain-quality trio -------------------------------------------------

    @staticmethod
    def _brand_similarity(
        business: BusinessCandidate, context: CandidateContext, bundle: "evidence_model.EvidenceBundle"
    ) -> ExtractedFeature:
        matches = bundle.by_type(evidence_model.EvidenceType.BRAND_MATCH)
        strength = grounding.brand_match_strength(business.name, context.domain)
        impact = round(sum((e.confidence_delta for e in matches), 0.0), 4)
        return ExtractedFeature(
            feature_id=FeatureId.BRAND_SIMILARITY,
            normalized_value=strength,
            raw_value=context.domain,
            confidence_impact=impact,
            supporting_evidence=tuple(matches),
            source_providers=tuple(context.contributing_providers),
            explanation=(
                matches[0].reason if matches else "no meaningful business-name-to-domain match"
            ),
        )

    @staticmethod
    def _reachability(context: CandidateContext, bundle: "evidence_model.EvidenceBundle") -> ExtractedFeature:
        """`context.verified` (Sprint 1's own WebsiteCandidate.validated,
        the authoritative "did this pass validation" flag) is trusted
        directly, not only inferred from REACHABILITY evidence being
        present -- the two should always agree in the real
        WebsiteResolver flow (`_attach_candidate_evidence` adds
        REACHABILITY evidence exactly when validation passes), but
        `verified` is the ground truth Sprint 1 already established;
        evidence is corroborating detail, not a second, independently
        -fallible source of the same fact."""
        hits = bundle.by_type(evidence_model.EvidenceType.REACHABILITY)
        reachable = bool(hits) or context.verified
        return ExtractedFeature(
            feature_id=FeatureId.REACHABILITY,
            normalized_value=1.0 if reachable else 0.0,
            raw_value=context.verified,
            confidence_impact=round(sum((e.confidence_delta for e in hits), 0.0), 4),
            supporting_evidence=tuple(hits),
            source_providers=tuple(context.contributing_providers),
            explanation=hits[0].reason if hits else ("passed validation" if reachable else "did not pass a reachability check"),
        )

    @staticmethod
    def _https(context: CandidateContext, bundle: "evidence_model.EvidenceBundle") -> ExtractedFeature:
        hits = bundle.by_type(evidence_model.EvidenceType.HTTPS)
        return ExtractedFeature(
            feature_id=FeatureId.HTTPS,
            normalized_value=1.0 if hits else 0.0,
            raw_value=bool(hits),
            confidence_impact=round(sum((e.confidence_delta for e in hits), 0.0), 4),
            supporting_evidence=tuple(hits),
            source_providers=tuple(context.contributing_providers),
            explanation=hits[0].reason if hits else "not confirmed to serve over HTTPS",
        )

    @staticmethod
    def _canonical_domain(context: CandidateContext, bundle: "evidence_model.EvidenceBundle") -> ExtractedFeature:
        hits = bundle.by_type(evidence_model.EvidenceType.CANONICAL_URL)
        return ExtractedFeature(
            feature_id=FeatureId.CANONICAL_DOMAIN,
            normalized_value=1.0 if hits else 0.0,
            raw_value=hits[0].source if hits else (context.urls[0] if context.urls else None),
            confidence_impact=round(sum((e.confidence_delta for e in hits), 0.0), 4),
            supporting_evidence=tuple(hits),
            source_providers=tuple(context.contributing_providers),
            explanation=hits[0].reason if hits else "not confirmed canonical (or not yet validated)",
        )

    @staticmethod
    def _domain_quality(context: CandidateContext, computed_so_far: List[ExtractedFeature]) -> ExtractedFeature:
        """Composite rollup of reachability/HTTPS/canonical -- informational
        only (confidence_impact=0.0): each component already carries its
        own weighted contribution above, and double-counting them a second
        time here would inflate confidence for the same three
        observations. No SEO analysis, no WHOIS, no DNS lookups -- exactly
        the three signals Discovery already has cheaply, per the brief."""
        by_id = {f.feature_id: f for f in computed_so_far}
        sub_scores = [
            by_id[fid].normalized_value
            for fid in (FeatureId.REACHABILITY, FeatureId.HTTPS, FeatureId.CANONICAL_DOMAIN)
            if by_id[fid].normalized_value is not None
        ]
        quality = round(sum(sub_scores) / len(sub_scores), 3) if sub_scores else None
        supporting = tuple(e for fid in (FeatureId.REACHABILITY, FeatureId.HTTPS, FeatureId.CANONICAL_DOMAIN) for e in by_id[fid].supporting_evidence)
        return ExtractedFeature(
            feature_id=FeatureId.DOMAIN_QUALITY,
            normalized_value=quality,
            raw_value={"reachability": by_id[FeatureId.REACHABILITY].normalized_value,
                       "https": by_id[FeatureId.HTTPS].normalized_value,
                       "canonical": by_id[FeatureId.CANONICAL_DOMAIN].normalized_value},
            confidence_impact=0.0,
            supporting_evidence=supporting,
            source_providers=tuple(context.contributing_providers),
            explanation=(
                f"domain quality {quality:.2f} from reachability/HTTPS/canonical checks"
                if quality is not None
                else "no domain-quality signal collected"
            ),
        )

    # -- Cross-provider / location --------------------------------------------

    @staticmethod
    def _provider_agreement(context: CandidateContext, bundle: "evidence_model.EvidenceBundle") -> ExtractedFeature:
        agreements = bundle.by_type(evidence_model.EvidenceType.PROVIDER_AGREEMENT)
        provider_count = len(context.contributing_providers)
        ratio = _clamp((provider_count - 1) / 2.0, 0.0, 1.0) if provider_count else 0.0
        return ExtractedFeature(
            feature_id=FeatureId.PROVIDER_AGREEMENT,
            normalized_value=ratio,
            raw_value=list(context.contributing_providers),
            confidence_impact=round(sum((e.confidence_delta for e in agreements), 0.0), 4),
            supporting_evidence=tuple(agreements),
            source_providers=tuple(context.contributing_providers),
            explanation=(
                f"{provider_count} independent provider(s) converged on {context.domain}"
                if provider_count
                else "no provider data"
            ),
        )

    @staticmethod
    def _location_consistency(
        business: BusinessCandidate,
        context: CandidateContext,
        bundle: "evidence_model.EvidenceBundle",
        location: str,
    ) -> ExtractedFeature:
        hits = bundle.by_type(evidence_model.EvidenceType.LOCATION_MATCH)
        if hits:
            normalized = 1.0 if business.address and grounding.location_mentioned(business.address, location) else 0.5
            explanation = hits[0].reason
        else:
            normalized = 0.0
            explanation = "nothing available corroborates the queried location"
        return ExtractedFeature(
            feature_id=FeatureId.LOCATION_CONSISTENCY,
            normalized_value=normalized,
            raw_value=business.address,
            confidence_impact=round(sum((e.confidence_delta for e in hits), 0.0), 4),
            supporting_evidence=tuple(hits),
            source_providers=tuple(context.contributing_providers),
            explanation=explanation,
        )

    # -- Business-record completeness ------------------------------------

    @staticmethod
    def _business_completeness(business: BusinessCandidate) -> ExtractedFeature:
        """website/phone/address present on the business record itself --
        mirrors the same 3-field completeness concept
        application.discovery.ranking.score_business already scores
        independently, now formalized as a reusable Feature."""
        fields = {"website": bool(business.website), "phone": bool(business.phone), "address": bool(business.address)}
        present = sum(1 for v in fields.values() if v)
        return ExtractedFeature(
            feature_id=FeatureId.BUSINESS_COMPLETENESS,
            normalized_value=round(present / 3.0, 3),
            raw_value=fields,
            confidence_impact=0.0,
            supporting_evidence=(),
            source_providers=(business.source,),
            explanation=f"{present}/3 core business fields present (website, phone, address)",
        )

    @staticmethod
    def _contact_completeness(business: BusinessCandidate) -> ExtractedFeature:
        """phone/address/email as reach-out channels -- distinct
        denominator from BUSINESS_COMPLETENESS (email, not website).
        BusinessCandidate has no email field anywhere in Discovery today
        (see dto.py), so this can never exceed 2/3 -- never fabricated,
        the gap is reported explicitly rather than silently ignored."""
        fields = {"phone": bool(business.phone), "address": bool(business.address), "email": False}
        present = sum(1 for v in fields.values() if v)
        return ExtractedFeature(
            feature_id=FeatureId.CONTACT_COMPLETENESS,
            normalized_value=round(present / 3.0, 3),
            raw_value=fields,
            confidence_impact=0.0,
            supporting_evidence=(),
            source_providers=(business.source,),
            explanation=f"{present}/3 contact channels present (phone, address, email -- email is never available today)",
        )

    @staticmethod
    def _verification_readiness(business: BusinessCandidate, computed_so_far: List[ExtractedFeature]) -> ExtractedFeature:
        """Generic (not category-specific) meta-signal: is there enough
        signal on this business to verify its identity confidently at
        all? Reuses grounding.is_low_signal_business_name -- the same
        generic "name alone isn't distinctive" check already used
        elsewhere, no new heuristic invented here."""
        by_id = {f.feature_id: f for f in computed_so_far}
        low_signal = grounding.is_low_signal_business_name(business.name)
        location_feature = by_id.get(FeatureId.LOCATION_CONSISTENCY)
        agreement_feature = by_id.get(FeatureId.PROVIDER_AGREEMENT)
        has_corroboration = bool(
            (location_feature and (location_feature.normalized_value or 0.0) > 0.0)
            or (agreement_feature and (agreement_feature.normalized_value or 0.0) > 0.0)
        )
        if not low_signal:
            normalized, explanation = 1.0, "business name itself is distinctive enough to verify confidently"
        elif has_corroboration:
            normalized, explanation = 0.6, "name is generic, but location/provider corroboration compensates"
        else:
            normalized, explanation = 0.2, "generic business name with no corroborating signal available"
        return ExtractedFeature(
            feature_id=FeatureId.VERIFICATION_READINESS,
            normalized_value=normalized,
            raw_value={"low_signal_name": low_signal, "has_corroboration": has_corroboration},
            confidence_impact=0.0,
            supporting_evidence=(),
            source_providers=(business.source,),
            explanation=explanation,
        )

    @staticmethod
    def _evidence_density(context: CandidateContext, bundle: "evidence_model.EvidenceBundle") -> ExtractedFeature:
        """How much was actually observed about this candidate -- volume,
        not coherence (that's a separate question; see identity.py's
        EVIDENCE_AGREEMENT). Normalized against a fixed, documented
        ceiling (see _MAX_OBSERVABLE_EVIDENCE_TYPES), not a learned one."""
        count = len(bundle.items)
        return ExtractedFeature(
            feature_id=FeatureId.EVIDENCE_DENSITY,
            normalized_value=round(min(count / _MAX_OBSERVABLE_EVIDENCE_TYPES, 1.0), 3),
            raw_value=count,
            confidence_impact=0.0,
            supporting_evidence=tuple(bundle.items),
            source_providers=tuple(context.contributing_providers),
            explanation=f"{count} evidence entries collected for this candidate",
        )
