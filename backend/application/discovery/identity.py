"""
Identity Resolution Layer (Sprint 2A).

Sprint 1 (application.discovery.evidence) gave every website candidate an
explainable, evidence-derived confidence, but Discovery still internally
revolved around a website: one business, one CandidatePool, one URL
picked. This module is the "a business no longer *is* a website, it *has*
an identity, of which a website is one attribute" shift made concrete.

    Evidence (Sprint 1) -> Identity Resolution -> Business Identity
                         -> Website Candidates -> Verification -> Confidence

Nothing here changes what application.discovery.website_resolver.
WebsiteResolver.resolve() returns -- see that module's docstring. This
layer is built *around* that decision (which candidate WebsiteResolver
actually picked is passed in, never re-derived), purely to produce a
richer, internal-only, explainable structure: BusinessIdentity is never
serialized onto any public DTO (application.discovery.dto) or provider
interface (application.discovery.providers.base).

Generic by construction: nothing below branches on business category
(no `if restaurant`, `if lawyer`, ...). Every signal here is either
reused from application.discovery.grounding (already category-agnostic)
or a structural property of the candidates themselves (how many
providers agree, whether a URL is canonical, ...). The same pipeline
therefore behaves identically for a restaurant, a law firm, or a SaaS
company without any code path change -- see test_identity.py's
`test_feature_extraction_is_identical_across_categories`.

Design notes
------------
- Deterministic, lightweight, no paid APIs / LLMs / embeddings / vector
  DBs / crawling -- every number below is a fixed arithmetic combination
  of data already sitting in a CandidatePool or a BusinessCandidate.
- Never fabricated: a signal with no underlying data (phone/address/
  coordinate/organization-metadata similarity -- none of which Discovery
  has anywhere to source today, since nothing here scrapes a page) is
  represented as a reserved Feature with `raw_value=None`,
  `normalized_value=None`, `confidence_contribution=0.0`, and an
  `explanation` saying so -- never a fabricated zero-value match. This
  mirrors application.discovery.evidence's own "never fabricate" rule
  for EvidenceType members it defines but doesn't yet populate.
- Reuses, never duplicates: brand/location matching goes through
  application.discovery.grounding (the same functions
  application.discovery.evidence, .ranking, and .providers.serper_provider
  already call) and provider-agreement weighting reuses the exact
  constants application.discovery.evidence already defines, rather than
  inventing a second set of magic numbers that could quietly drift from
  Sprint 1's.
- Two distinct "confidence" questions, kept separate on purpose:
    WebsiteCandidateGroup.confidence          -- "is this a legitimate,
        reachable site?" (the full merged EvidenceBundle -- includes
        reachability/HTTPS/canonical-URL, i.e. Sprint 1's verification
        signals).
    WebsiteCandidateGroup.identity_confidence -- "does this site's
        identity actually match the business we searched for?" (only the
        identity-matching Features below -- brand/location). Conflating
        these would let a fast, HTTPS, root-URL page with zero relation
        to the business name outscore a slower but genuinely-matching
        one; keeping them separate is what lets Sprint 2B combine them
        with its own, possibly different, weighting without touching
        Sprint 1's evidence weights at all.

Sprint 2B: Feature Extraction / Verification / Confidence Propagation
-----------------------------------------------------------------------
Sprint 2B's brief calls out that this module, as Sprint 2A left it, still
mixed three responsibilities together: it extracted features, verified
identity, AND assembled BusinessIdentity all in one place, with
`WebsiteCandidateGroup.confidence` reading straight from Evidence rather
than through a Features layer. Three new, independent modules now own
those responsibilities:

    application.discovery.features      -- Evidence -> Features
    application.discovery.verification  -- Features -> VerificationBundle
    application.discovery.confidence    -- Features + VerificationBundle
                                            -> ConfidenceTrace (staged,
                                            not flat-additive)

This module is now purely the last stage of that chain plus assembly:
`GenericIdentityResolver.resolve()` builds the FeatureStore, runs
verification and confidence propagation for every group, and folds the
result into `BusinessIdentity`. Every one of Sprint 2A's own public names
(`Feature`, `FeatureName`, `extract_features`, `WebsiteCandidateGroup`,
`group_website_candidates`, `ProviderAgreementRecord`,
`compute_provider_agreement`, `verify_identity_features`,
`IdentityVerificationResult`, `DecisionTrace`, `BusinessIdentity`,
`IdentityResolver`, `GenericIdentityResolver`) is unchanged in name and
behavior -- see each one's own docstring below for why it's kept rather
than replaced. `BusinessIdentity.confidence` now comes from
`ConfidenceTrace.final` instead of a flat evidence sum (a like-for-like
upgrade at the same call site, per the Sprint 2B brief's "replace mostly
-additive confidence with confidence propagation"); `DecisionTrace` gains
new fields (`features_used`, `verification_bundle`, `confidence_trace`,
`missing_evidence`) so it can explain the new pipeline too, on top of
what it already explained.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from application.discovery import evidence as evidence_model
from application.discovery import grounding
from application.discovery import confidence as confidence_model
from application.discovery import features as features_model
from application.discovery import verification as verification_model
from application.discovery.dto import BusinessCandidate


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


class FeatureName(Enum):
    """Generic, category-agnostic identity-matching signals.

    The Sprint 2A brief's example list ("Business Name Similarity",
    "Website Domain Similarity", "Brand Similarity") names the same
    underlying deterministic computation three times -- name-to-domain
    matching is symmetric, so scoring it three separate times under three
    names would triple-count one signal rather than add information.
    They are unified here as BRAND_SIMILARITY; see that member's
    docstring below.
    """

    # -- Populated in Sprint 2A --
    BRAND_SIMILARITY = "brand_similarity"          # name <-> domain match
    LOCATION_CONSISTENCY = "location_consistency"    # address/domain <-> query location
    PROVIDER_AGREEMENT = "provider_agreement"        # independent providers converging
    CANONICAL_DOMAIN = "canonical_domain"            # resolved to root, not a sub-page
    EVIDENCE_AGREEMENT = "evidence_agreement"        # internal coherence of the evidence bundle

    # -- Reserved: Discovery has no data source for these yet (no page
    # scrape happens before a Lead exists -- see website_validator.py's
    # own docstring). Defined now so a future Verification Engine that
    # *does* fetch page content (schema.org / contact page / OpenGraph,
    # exactly the extension point application.discovery.evidence.
    # VerificationEngine already anticipates) can start populating these
    # without a breaking change to this enum or to anything that already
    # iterates over it. --
    PHONE_SIMILARITY = "phone_similarity"
    ADDRESS_SIMILARITY = "address_similarity"
    COORDINATE_SIMILARITY = "coordinate_similarity"
    ORGANIZATION_METADATA = "organization_metadata"


# Every feature Sprint 2A ever constructs, in a fixed order -- used so
# extract_features() always returns a complete, predictable set (present
# with a "no data" explanation, not simply absent) rather than callers
# needing to know which subset happened to fire this time.
_ALL_FEATURE_NAMES: List[FeatureName] = list(FeatureName)

_NO_DATA_EXPLANATION = (
    "not available in this pipeline -- no page content is fetched before "
    "a Lead exists, so this signal has nothing to compare against yet"
)


@dataclass(frozen=True)
class Feature:
    """One atomic, named identity-matching observation. Immutable, like
    application.discovery.evidence.Evidence -- a Feature is a fact about
    one candidate at one point in time, not a mutable running total."""

    name: FeatureName
    raw_value: Optional[Any]
    normalized_value: Optional[float]  # 0.0-1.0, or None if not applicable
    confidence_contribution: float  # 0.0 for informational/reserved features
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name.value,
            "raw_value": self.raw_value,
            "normalized_value": self.normalized_value,
            "confidence_contribution": self.confidence_contribution,
            "explanation": self.explanation,
        }


def _reserved_feature(name: FeatureName) -> Feature:
    return Feature(
        name=name,
        raw_value=None,
        normalized_value=None,
        confidence_contribution=0.0,
        explanation=_NO_DATA_EXPLANATION,
    )


def extract_features(
    business: BusinessCandidate, group: "WebsiteCandidateGroup", location: str
) -> List[Feature]:
    """Evidence -> Features. Pure function: everything it reads is already
    sitting on `business` or already computed into `group.evidence` --
    no I/O, no new provider/HTTP calls, exactly like every evidence.py
    derivation helper it builds on.

    Returns one Feature per FeatureName, always, in _ALL_FEATURE_NAMES
    order -- reserved features included with `confidence_contribution=0.0`
    -- so a caller iterating the result never needs a membership check to
    know "did this feature fire."
    """
    features: List[Feature] = []

    # -- BRAND_SIMILARITY --------------------------------------------------
    # Reuses grounding.brand_match_strength (the same function
    # evidence.brand_identity_evidence, ranking.py, and serper_provider.py
    # already call) and evidence.py's own BRAND_MATCH_MAX_WEIGHT constant,
    # so this feature's contribution is never a second, independently
    # -tunable number that could drift from Sprint 1's.
    brand_strength = grounding.brand_match_strength(business.name, group.domain)
    features.append(
        Feature(
            name=FeatureName.BRAND_SIMILARITY,
            raw_value=group.domain,
            normalized_value=brand_strength,
            confidence_contribution=round(brand_strength * evidence_model.BRAND_MATCH_MAX_WEIGHT, 4),
            explanation=(
                f"business name matches domain root with strength {brand_strength:.2f}"
                if brand_strength > 0.0
                else "no meaningful business-name-to-domain match"
            ),
        )
    )

    # -- LOCATION_CONSISTENCY -----------------------------------------------
    # Reuses grounding.location_mentioned and evidence.py's own
    # LOCATION_MATCH_WEIGHT/LOCATION_MATCH_WEAK_WEIGHT, for the same
    # "single source of truth for the weight" reason as above.
    address = business.address
    if address and grounding.location_mentioned(address, location):
        features.append(
            Feature(
                name=FeatureName.LOCATION_CONSISTENCY,
                raw_value=address,
                normalized_value=1.0,
                confidence_contribution=evidence_model.LOCATION_MATCH_WEIGHT,
                explanation="business address corroborates the queried location",
            )
        )
    elif grounding.location_mentioned(group.domain, location):
        features.append(
            Feature(
                name=FeatureName.LOCATION_CONSISTENCY,
                raw_value=group.domain,
                normalized_value=0.5,
                confidence_contribution=evidence_model.LOCATION_MATCH_WEAK_WEIGHT,
                explanation="domain text corroborates the queried location",
            )
        )
    else:
        features.append(
            Feature(
                name=FeatureName.LOCATION_CONSISTENCY,
                raw_value=address,
                normalized_value=0.0,
                confidence_contribution=0.0,
                explanation="nothing available corroborates the queried location",
            )
        )

    # -- PROVIDER_AGREEMENT --------------------------------------------------
    # Informational here (contribution=0.0): the actual confidence bonus
    # for independent corroboration is already inside group.evidence (each
    # source WebsiteCandidate's PROVIDER_AGREEMENT Evidence, computed by
    # evidence.provider_agreement_evidence and merged into the group by
    # group_website_candidates below) -- scoring it again here would
    # double-count the exact same observation under a second name.
    provider_count = len(group.contributing_providers)
    agreement_ratio = _clamp((provider_count - 1) / 2.0, 0.0, 1.0) if provider_count else 0.0
    features.append(
        Feature(
            name=FeatureName.PROVIDER_AGREEMENT,
            raw_value=list(group.contributing_providers),
            normalized_value=agreement_ratio,
            confidence_contribution=0.0,
            explanation=(
                f"{provider_count} independent provider(s) converged on {group.domain}"
                if provider_count
                else "no provider data"
            ),
        )
    )

    # -- CANONICAL_DOMAIN -----------------------------------------------------
    # Also informational (contribution=0.0) for the same double-counting
    # reason: CANONICAL_URL Evidence is already merged into group.evidence
    # by evidence.verification_evidence.
    canonical_evidence = group.evidence.by_type(evidence_model.EvidenceType.CANONICAL_URL)
    canonical_url = canonical_evidence[0].source if canonical_evidence else (group.urls[0] if group.urls else None)
    features.append(
        Feature(
            name=FeatureName.CANONICAL_DOMAIN,
            raw_value=canonical_url,
            normalized_value=1.0 if canonical_evidence else 0.0,
            confidence_contribution=0.0,
            explanation=(
                "resolved to the site's root/canonical URL"
                if canonical_evidence
                else "not confirmed canonical (or not yet validated)"
            ),
        )
    )

    # -- EVIDENCE_AGREEMENT ---------------------------------------------------
    # A generic, structural coherence signal: what fraction of everything
    # observed about this candidate was corroborating (positive delta) vs
    # contradicting (negative delta, e.g. PROVIDER_DISAGREEMENT). Purely
    # diagnostic (contribution=0.0) -- it summarizes the bundle, it
    # doesn't add new information the bundle's own confidence lacks.
    items = group.evidence.items
    if items:
        positive = sum(1 for e in items if e.confidence_delta >= 0)
        ratio = positive / len(items)
        features.append(
            Feature(
                name=FeatureName.EVIDENCE_AGREEMENT,
                raw_value={"positive": positive, "total": len(items)},
                normalized_value=round(ratio, 3),
                confidence_contribution=0.0,
                explanation=f"{positive}/{len(items)} evidence entries were corroborating, not contradicting",
            )
        )
    else:
        features.append(
            Feature(
                name=FeatureName.EVIDENCE_AGREEMENT,
                raw_value={"positive": 0, "total": 0},
                normalized_value=None,
                confidence_contribution=0.0,
                explanation="no evidence collected for this candidate",
            )
        )

    # -- Reserved features (no data source in Sprint 2A) ---------------------
    features.append(_reserved_feature(FeatureName.PHONE_SIMILARITY))
    features.append(_reserved_feature(FeatureName.ADDRESS_SIMILARITY))
    features.append(_reserved_feature(FeatureName.COORDINATE_SIMILARITY))
    features.append(_reserved_feature(FeatureName.ORGANIZATION_METADATA))

    return features


# ---------------------------------------------------------------------------
# Website Candidate Group (candidate merging)
# ---------------------------------------------------------------------------


@dataclass
class WebsiteCandidateGroup:
    """Every raw application.discovery.evidence.WebsiteCandidate the pool
    considered for one distinct domain, merged into a single identity
    -centric candidate. This is the "Website Candidates" node of the
    Sprint 2A pipeline: CandidatePool (Sprint 1) is an audit log of every
    URL considered, in the order considered, one entry per provider hit;
    a WebsiteCandidateGroup is the identity layer's answer to "how many
    genuinely distinct websites were actually proposed for this
    business," with per-provider duplicates collapsed into one entry that
    remembers every provider that contributed to it.
    """

    domain: str
    urls: List[str] = field(default_factory=list)
    contributing_providers: List[str] = field(default_factory=list)
    source_candidates: List["evidence_model.WebsiteCandidate"] = field(default_factory=list)
    evidence: evidence_model.EvidenceBundle = field(default_factory=evidence_model.EvidenceBundle)
    features: List[Feature] = field(default_factory=list)
    verified: bool = False
    rejection_reason: Optional[str] = None

    @property
    def confidence(self) -> float:
        """"Is this a legitimate, reachable site?" -- the full merged
        evidence signal (provider-found, brand, location, reachability,
        HTTPS, canonical, provider agreement/disagreement). Same meaning
        Sprint 1's WebsiteCandidate.confidence already had."""
        return self.evidence.confidence

    @property
    def identity_confidence(self) -> float:
        """"Does this site's identity actually match the business?" --
        only the identity-matching Features (see module docstring for why
        this is kept separate from `confidence`)."""
        return round(_clamp(sum(f.confidence_contribution for f in self.features), 0.0, 1.0), 3)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "urls": list(self.urls),
            "contributing_providers": list(self.contributing_providers),
            "verified": self.verified,
            "rejection_reason": self.rejection_reason,
            "confidence": self.confidence,
            "identity_confidence": self.identity_confidence,
            "evidence": self.evidence.explain(),
            "features": [f.to_dict() for f in self.features],
        }


def group_website_candidates(
    pool: "evidence_model.CandidatePool",
) -> List[WebsiteCandidateGroup]:
    """Candidate merging: collapses every raw WebsiteCandidate in `pool`
    sharing a normalized domain into one WebsiteCandidateGroup, preserving
    first-seen order (which, as in CandidatePool itself, is provider
    -priority order). Evidence is merged by extending, never de-duplicated
    -- two providers each independently finding the same domain is two
    genuine observations, not one repeated twice, and EvidenceBundle's own
    [0, 1] clamp already prevents unbounded stacking.

    Never fabricates a group for a domain nothing proposed: an empty pool
    produces an empty list, exactly like CandidatePool's own "no
    placeholder for a provider that found nothing" rule.
    """
    groups: Dict[str, WebsiteCandidateGroup] = {}
    order: List[str] = []

    for candidate in pool.candidates:
        if not candidate.domain:
            continue
        group = groups.get(candidate.domain)
        if group is None:
            group = WebsiteCandidateGroup(domain=candidate.domain)
            groups[candidate.domain] = group
            order.append(candidate.domain)

        if candidate.url not in group.urls:
            group.urls.append(candidate.url)
        if candidate.provider not in group.contributing_providers:
            group.contributing_providers.append(candidate.provider)
        group.source_candidates.append(candidate)
        group.evidence.extend(candidate.evidence.items)
        if candidate.validated:
            group.verified = True

    result = [groups[domain] for domain in order]
    for group in result:
        # A rejection reason is only meaningful for a group that never
        # verified at all -- a group with at least one validated source
        # candidate is a verified identity, full stop, even if an earlier
        # provider's attempt at the same domain failed first (see
        # test_website_resolver_evidence.py's agreement-after-failure
        # scenario, which this mirrors).
        if not group.verified:
            group.rejection_reason = next(
                (c.rejection_reason for c in group.source_candidates if c.rejection_reason), None
            )

    return result


# ---------------------------------------------------------------------------
# Provider agreement (first-class)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderAgreementRecord:
    """First-class representation of whether providers agree, per the
    Sprint 2A brief: "If providers disagree, represent disagreement
    explicitly. Never hide conflicting evidence." Complements (does not
    replace) the per-candidate PROVIDER_AGREEMENT/PROVIDER_DISAGREEMENT
    Evidence entries evidence.provider_agreement_evidence already
    produces -- this is the pool-wide summary of that same data."""

    status: str  # "agreement" | "disagreement" | "single_source" | "no_candidates"
    agreeing_domain: Optional[str]
    agreeing_providers: List[str]
    disagreements: List[Dict[str, str]]  # [{"provider": ..., "domain": ...}, ...]

    def explain(self) -> str:
        if self.status == "no_candidates":
            return "no provider returned a candidate website"
        if self.status == "single_source":
            return f"only one provider ({self.agreeing_providers[0]}) proposed a website"
        if self.status == "agreement":
            providers = ", ".join(self.agreeing_providers)
            return f"{providers} independently agree on {self.agreeing_domain}"
        parts = ", ".join(f"{d['provider']} -> {d['domain']}" for d in self.disagreements)
        return f"providers disagree: {parts}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "agreeing_domain": self.agreeing_domain,
            "agreeing_providers": list(self.agreeing_providers),
            "disagreements": list(self.disagreements),
            "explanation": self.explain(),
        }


def compute_provider_agreement(pool: "evidence_model.CandidatePool") -> ProviderAgreementRecord:
    """Pure summary of `pool`: groups raw candidates by domain and reports
    whether the providers involved converged on one domain, split across
    several, or never had more than one source to compare in the first
    place. Never inspects validation outcome -- agreement is about what
    providers *proposed*, independent of whether it later verified."""
    if not pool.candidates:
        return ProviderAgreementRecord(
            status="no_candidates", agreeing_domain=None, agreeing_providers=[], disagreements=[]
        )

    by_domain: Dict[str, List[str]] = {}
    order: List[str] = []
    for candidate in pool.candidates:
        if not candidate.domain:
            continue
        if candidate.domain not in by_domain:
            by_domain[candidate.domain] = []
            order.append(candidate.domain)
        if candidate.provider not in by_domain[candidate.domain]:
            by_domain[candidate.domain].append(candidate.provider)

    if not order:
        return ProviderAgreementRecord(
            status="no_candidates", agreeing_domain=None, agreeing_providers=[], disagreements=[]
        )

    # The largest agreeing group (first-seen order breaks ties) is the
    # one worth naming; every other domain proposed is a disagreement
    # against it -- explicit, never hidden.
    best_domain = max(order, key=lambda d: len(by_domain[d]))
    if len(order) == 1 and len(by_domain[best_domain]) == 1:
        return ProviderAgreementRecord(
            status="single_source",
            agreeing_domain=best_domain,
            agreeing_providers=list(by_domain[best_domain]),
            disagreements=[],
        )
    if len(by_domain[best_domain]) > 1:
        disagreements = [
            {"provider": provider, "domain": domain}
            for domain in order
            if domain != best_domain
            for provider in by_domain[domain]
        ]
        return ProviderAgreementRecord(
            status="agreement",
            agreeing_domain=best_domain,
            agreeing_providers=list(by_domain[best_domain]),
            disagreements=disagreements,
        )

    disagreements = [
        {"provider": provider, "domain": domain} for domain in order for provider in by_domain[domain]
    ]
    return ProviderAgreementRecord(
        status="disagreement", agreeing_domain=None, agreeing_providers=[], disagreements=disagreements
    )


# ---------------------------------------------------------------------------
# Identity verification (consumes Features, never raw provider output)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityVerificationResult:
    verified: bool
    reason: str


def verify_identity_features(features: List[Feature]) -> IdentityVerificationResult:
    """Verification -- consumes Features, exclusively. This function never
    receives (and must never be given) a ValidationOutcome, a raw
    provider response, or an EvidenceBundle: everything it needs to judge
    is already distilled into the normalized_value/confidence_contribution
    of the Features passed in, per the Sprint 2A brief ("Do not allow
    Verification to inspect raw provider output directly. Verification
    should consume features.").

    This answers a narrower question than website_validator/evidence's
    existing verification: not "is the site alive" (that's
    WebsiteCandidateGroup.verified, decided by Sprint 1's
    WebsiteValidator/VerificationEngine already), but "does the matched
    identity actually look like the business" -- a genuinely distinct,
    additive check a live-but-wrong site would fail.
    """
    by_name = {f.name: f for f in features}
    brand = by_name.get(FeatureName.BRAND_SIMILARITY)
    location = by_name.get(FeatureName.LOCATION_CONSISTENCY)
    agreement = by_name.get(FeatureName.PROVIDER_AGREEMENT)

    brand_value = brand.normalized_value if brand and brand.normalized_value is not None else 0.0
    location_value = location.normalized_value if location and location.normalized_value is not None else 0.0
    agreement_value = agreement.normalized_value if agreement and agreement.normalized_value is not None else 0.0

    if brand_value > 0.0:
        return IdentityVerificationResult(
            verified=True, reason=f"business name matches the domain (strength {brand_value:.2f})"
        )
    if location_value > 0.0:
        return IdentityVerificationResult(
            verified=True, reason="queried location is corroborated, even without a brand-name match"
        )
    if agreement_value > 0.0:
        return IdentityVerificationResult(
            verified=True, reason="multiple independent providers converged on this candidate"
        )
    return IdentityVerificationResult(
        verified=False, reason="no brand, location, or cross-provider signal supports this identity"
    )


# ---------------------------------------------------------------------------
# Decision trace
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionTrace:
    """Every BusinessIdentity carries one of these so the system can
    always answer "why was candidate A selected?" -- selected candidate,
    every rejected candidate and why, confidence, the supporting evidence
    chain, verification results, and provider agreement, all in one
    place.

    Sprint 2B additions (`features_used`, `verification_bundle`,
    `confidence_trace`, `missing_evidence`) extend, not replace, Sprint
    2A's fields above -- every field this class had before is still here,
    with the same meaning."""

    selected_domain: Optional[str]
    selected_url: Optional[str]
    selected_confidence: float
    rejected: List[Dict[str, Any]]
    supporting_evidence: List[str]
    verification_results: Dict[str, Any]
    provider_agreement: ProviderAgreementRecord
    features_used: List[str]
    verification_bundle: Optional["verification_model.VerificationBundle"]
    confidence_trace: "confidence_model.ConfidenceTrace"
    missing_evidence: List[str]

    def explain(self) -> str:
        if self.selected_domain is None:
            return "No candidate was selected: " + self.provider_agreement.explain()
        lines = [
            f"Selected {self.selected_domain} (confidence={self.selected_confidence:.2f}).",
            self.provider_agreement.explain() + ".",
        ]
        if self.rejected:
            rejected_summary = "; ".join(f"{r['domain']} ({r['reason']})" for r in self.rejected)
            lines.append(f"Rejected: {rejected_summary}.")
        if self.features_used:
            lines.append("Features used: " + ", ".join(self.features_used) + ".")
        if self.missing_evidence:
            lines.append("Missing: " + "; ".join(self.missing_evidence) + ".")
        return " ".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_domain": self.selected_domain,
            "selected_url": self.selected_url,
            "selected_confidence": self.selected_confidence,
            "rejected": list(self.rejected),
            "supporting_evidence": list(self.supporting_evidence),
            "verification_results": dict(self.verification_results),
            "provider_agreement": self.provider_agreement.to_dict(),
            "features_used": list(self.features_used),
            "verification_bundle": self.verification_bundle.to_dict() if self.verification_bundle else None,
            "confidence_trace": self.confidence_trace.to_dict(),
            "missing_evidence": list(self.missing_evidence),
            "explanation": self.explain(),
        }


# ---------------------------------------------------------------------------
# Business Identity
# ---------------------------------------------------------------------------


@dataclass
class BusinessIdentity:
    """The canonical, internal-only representation of a discovered
    business. INTERNAL ONLY -- never place an instance of this on
    application.discovery.dto or any provider interface; it exists purely
    for WebsiteResolver's own reasoning and observability, exactly like
    application.discovery.evidence.CandidatePool before it.

    The website is one attribute of this identity (`selected_website`),
    not the identity itself -- the same "Business has Evidence; Website is
    one piece of evidence" shift application.discovery.evidence.
    CandidatePool made concrete for Sprint 1, extended here to the
    business as a whole.

    `feature_store` (Sprint 2B) is the canonical, per-resolution Feature
    Store (application.discovery.features.FeatureStore) -- BusinessIdentity
    holds a *reference* to it rather than embedding feature lists inline,
    per the Sprint 2B brief. `WebsiteCandidateGroup.features` (Sprint 2A)
    still exists too, unchanged, for backward compatibility -- but nothing
    new in this module reads from it; verification and confidence
    propagation both read exclusively from `feature_store`.
    """

    normalized_name: str
    aliases: List[str]
    website_candidates: List[WebsiteCandidateGroup]
    selected_website: Optional[str]
    phones: List[str]
    emails: List[str]
    addresses: List[str]
    evidence_bundle: evidence_model.EvidenceBundle
    confidence: float
    verification_state: str  # "verified" | "unverified" | "no_candidates"
    decision_trace: DecisionTrace
    feature_store: "features_model.FeatureStore"

    @property
    def provider_evidence(self) -> List["evidence_model.Evidence"]:
        """Business Evidence bullet: the PROVIDER-category slice of
        `evidence_bundle` -- a view, not separately-stored state, so it
        can never drift out of sync with the full bundle."""
        return self.evidence_bundle.by_category(evidence_model.EvidenceCategory.PROVIDER)

    def explain(self) -> List[str]:
        return [self.decision_trace.explain(), *self.evidence_bundle.explain()]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "normalized_name": self.normalized_name,
            "aliases": list(self.aliases),
            "website_candidates": [g.to_dict() for g in self.website_candidates],
            "selected_website": self.selected_website,
            "phones": list(self.phones),
            "emails": list(self.emails),
            "addresses": list(self.addresses),
            "confidence": self.confidence,
            "feature_store": self.feature_store.to_dict(),
            "verification_state": self.verification_state,
            "decision_trace": self.decision_trace.to_dict(),
        }


def normalize_business_name(name: str) -> str:
    """Deterministic canonical form of a business name: lowercase,
    stopword-stripped significant words, space-joined. Reuses
    grounding.significant_words (the exact tokenizer/stopword list
    application.discovery.grounding already uses for brand matching)
    rather than maintaining a second normalization vocabulary."""
    return " ".join(grounding.significant_words(name))


# ---------------------------------------------------------------------------
# Identity Resolver (extension point for Sprint 2B)
# ---------------------------------------------------------------------------


class IdentityResolver(ABC):
    """Interface WebsiteResolver depends on (constructor-injectable,
    defaulted -- the same DI pattern already used for
    `validator`/`fallback_provider`/`verification_engine`), not on a
    concrete resolver, so a future Sprint 2B resolver (e.g. one that also
    reranks across multiple *verified* candidates by identity_confidence,
    rather than only ever having one to consider) can be swapped in
    without WebsiteResolver's call site changing."""

    @abstractmethod
    def resolve(
        self,
        business: BusinessCandidate,
        pool: "evidence_model.CandidatePool",
        location: str,
        chosen: Optional["evidence_model.WebsiteCandidate"],
        chosen_url: Optional[str],
    ) -> BusinessIdentity:
        """Builds a BusinessIdentity from everything already known about
        `pool`. `chosen`/`chosen_url` are WebsiteResolver's own,
        already-made decision (the WebsiteCandidate object it actually
        picked, and the normalized URL it will return) -- passed in, never
        re-derived, so identity resolution can never disagree with the
        decision it is explaining. Must not perform I/O."""


class GenericIdentityResolver(IdentityResolver):
    """Sprint 2A's concrete, fully generic IdentityResolver -- candidate
    merging and decision-trace assembly are unchanged from Sprint 2A.
    What changed in Sprint 2B is *how* verification and confidence are
    computed: this now builds a canonical FeatureStore (via
    application.discovery.features), runs it through a modular
    VerificationPipeline (application.discovery.verification), and
    propagates confidence in stages (application.discovery.confidence)
    instead of pulling a flat evidence sum. This remains the default
    WebsiteResolver uses today.
    """

    def __init__(
        self,
        feature_engine: Optional["features_model.FeatureExtractionEngine"] = None,
        verification_pipeline: Optional["verification_model.VerificationPipeline"] = None,
        confidence_engine: Optional["confidence_model.ConfidencePropagationEngine"] = None,
    ):
        # Same DI pattern as everywhere else in this package -- defaulted,
        # constructor-injectable, so a future engine (e.g. one that
        # populates WEBSITE_STRUCTURE for real) plugs in without this
        # resolver's own code changing.
        self.feature_engine = feature_engine or features_model.GenericFeatureExtractionEngine()
        self.verification_pipeline = verification_pipeline or verification_model.VerificationPipeline()
        self.confidence_engine = confidence_engine or confidence_model.ConfidencePropagationEngine()

    def resolve(
        self,
        business: BusinessCandidate,
        pool: "evidence_model.CandidatePool",
        location: str,
        chosen: Optional["evidence_model.WebsiteCandidate"],
        chosen_url: Optional[str],
    ) -> BusinessIdentity:
        groups = group_website_candidates(pool)
        for group in groups:
            # Sprint 2A features -- unchanged, kept for backward
            # compatibility (WebsiteCandidateGroup.identity_confidence,
            # verify_identity_features, and every Sprint 2A test still
            # depend on this exact list).
            group.features = extract_features(business, group, location)

        # Sprint 2B: one canonical FeatureSet per group, in a FeatureStore
        # -- and a VerificationBundle for every group (not just the
        # selected one), so a rejected candidate's DecisionTrace entry can
        # show *why* it failed verification, not just a one-line reason.
        feature_store = features_model.FeatureStore()
        verification_bundles: Dict[str, "verification_model.VerificationBundle"] = {}
        for group in groups:
            context = features_model.CandidateContext(
                domain=group.domain,
                urls=tuple(group.urls),
                contributing_providers=tuple(group.contributing_providers),
                evidence=group.evidence,
                verified=group.verified,
            )
            feature_set = self.feature_engine.extract(business, context, location)
            feature_store.put(feature_set)
            verification_bundles[group.domain] = self.verification_pipeline.run(feature_set)

        chosen_domain = chosen.domain if chosen else None
        selected_group = next((g for g in groups if g.domain == chosen_domain), None) if chosen_domain else None
        selected_feature_set = feature_store.get(selected_group.domain) if selected_group else None
        selected_verification = verification_bundles.get(selected_group.domain) if selected_group else None

        # Sprint 2A's own identity-feature verification -- unchanged,
        # still computed and still recorded (verification_results below),
        # even though verification_state now comes from the Sprint 2B
        # pipeline's `overall_passed` instead. See IdentityVerifier's
        # docstring in verification.py for why the logic is intentionally
        # duplicated rather than shared between the two.
        if selected_group is not None:
            identity_verification = verify_identity_features(selected_group.features)
        else:
            identity_verification = IdentityVerificationResult(
                verified=False, reason="no candidate was selected"
            )

        if selected_feature_set is not None and selected_verification is not None:
            confidence_trace = self.confidence_engine.propagate(selected_feature_set, selected_verification)
        else:
            confidence_trace = confidence_model.empty_confidence_trace(
                "no candidate was selected" if not groups else "selected candidate has no feature set"
            )

        if not groups:
            verification_state = "no_candidates"
        elif selected_verification is not None and selected_verification.overall_passed:
            verification_state = "verified"
        else:
            verification_state = "unverified"

        merged_bundle = evidence_model.EvidenceBundle()
        for candidate in pool.candidates:
            merged_bundle.extend(candidate.evidence.items)

        provider_agreement = compute_provider_agreement(pool)

        rejected = [
            {
                "domain": g.domain,
                "urls": list(g.urls),
                "reason": g.rejection_reason or ("not selected" if g.verified else "not verified"),
                "confidence": g.confidence,
                "verification": verification_bundles[g.domain].to_dict(),
            }
            for g in groups
            if g is not selected_group
        ]

        features_used = (
            [f.feature_id.value for f in selected_feature_set.features if f.confidence_impact > 0]
            if selected_feature_set
            else []
        )
        missing_evidence: List[str] = []
        for stage in confidence_trace.stages:
            for item in stage.missing:
                if item not in missing_evidence:
                    missing_evidence.append(item)

        decision_trace = DecisionTrace(
            selected_domain=selected_group.domain if selected_group else None,
            selected_url=chosen_url,
            selected_confidence=selected_group.confidence if selected_group else 0.0,
            rejected=rejected,
            supporting_evidence=selected_group.evidence.explain() if selected_group else [],
            verification_results={
                "site_reachable": bool(selected_group.verified) if selected_group else False,
                "identity_verified": identity_verification.verified,
                "identity_verification_reason": identity_verification.reason,
            },
            provider_agreement=provider_agreement,
            features_used=features_used,
            verification_bundle=selected_verification,
            confidence_trace=confidence_trace,
            missing_evidence=missing_evidence,
        )

        normalized_name = normalize_business_name(business.name)
        aliases = sorted({business.name, normalized_name} - {""})

        return BusinessIdentity(
            normalized_name=normalized_name,
            aliases=aliases,
            website_candidates=groups,
            selected_website=chosen_url,
            phones=[business.phone] if business.phone else [],
            emails=[],
            addresses=[business.address] if business.address else [],
            evidence_bundle=merged_bundle,
            confidence=confidence_trace.final,
            verification_state=verification_state,
            decision_trace=decision_trace,
            feature_store=feature_store,
        )
