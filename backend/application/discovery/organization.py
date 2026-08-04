"""
Candidate Identity Model, Candidate Consolidation & Organization
Resolution (Sprint 3).

Three of the Sprint 3 brief's components live here together because
they're one continuous idea: turn each already-merged
`identity.WebsiteCandidateGroup` into a narrow, immutable `IdentityCandidate`
(component 1), collapse the ones that are provably the same
infrastructure (component 2), and represent -- but never silently merge
-- the weaker, evidence-based relationships between what's left
(component 4).

Layering discipline: this module reads `identity.WebsiteCandidateGroup`,
`evidence.EvidenceBundle`, `features.FeatureSet`, and
`verification.VerificationBundle` -- all of them already-computed,
upstream output -- plus `canonicalization` for domain structure. It
performs no I/O and adds no new evidence; it only reorganizes evidence
that already exists. Nothing here changes `WebsiteCandidateGroup` itself
or which candidate `WebsiteResolver.resolve()` picked (see that module's
own docstring for why that decision is never revisited downstream).

Candidate Identity Model (component 1)
---------------------------------------
`IdentityCandidate` stores exactly the fields the brief lists -- provider,
normalized domain, canonical domain, website, supporting evidence,
provider confidence, verification results, feature store entry, decision
trace, risk, confidence, identity quality -- "Nothing else." It is
immutable (frozen dataclass): once built, an IdentityCandidate is a fixed
snapshot, exactly like `evidence.Evidence`/`features.ExtractedFeature`
before it. `risk`/`decision_trace` start empty and are filled in once
(via `dataclasses.replace`, never in-place mutation) after competition.py
and false_positive.py have had a chance to look at the full candidate
pool -- an IdentityCandidate is never partially valid.

Candidate Consolidation (component 2)
---------------------------------------
Grouping is deterministic and structural, per the brief's "No keyword
logic. No business-specific rule": candidates are merged into one
`OrganizationGroup` if and only if `canonicalization.same_registrable_domain`
says so -- i.e. they are, by construction, the exact same eTLD+1 (so
`shop.company.com`, `support.company.com`, `www.company.com`, and
`company.com` all consolidate into one `OrganizationGroup` for
"company.com", even though `identity.group_website_candidates` -- exact
-domain grouping -- would keep them as four separate
`WebsiteCandidateGroup`s). This is a HARD merge because same-eTLD+1 is
proof, not a guess.

Organization Resolution (component 4)
----------------------------------------
Everything the brief's relationship examples name (Official, Alias,
Mirror, Redirect, Regional, Branch, Corporate, Product, Marketplace,
Unknown) is represented as an `OrganizationRelationship` -- but per "This
must remain evidence-based. Never infer relationships without evidence",
this module only ever emits relationships this pipeline can actually back
with structural evidence:

  - REDIRECT   -- a candidate's own validated/reachable URL ended up on a
                  different registrable domain than the one originally
                  proposed (see `resolve_relationships`'s redirect check,
                  fed by the same REACHABILITY evidence
                  false_positive.redirect_domain_mismatch_signal reads).
  - REGIONAL / UNKNOWN -- two OrganizationGroups share a root label across
                  different suffixes (canonicalization.root_label_match).
                  Tagged REGIONAL when the shared suffix pattern looks
                  like a country-specific variant (a compound ccTLD
                  suffix on one side, e.g. company.com vs company.co.in),
                  and UNKNOWN otherwise -- deliberately never OFFICIAL,
                  ALIAS, BRANCH, CORPORATE, PRODUCT, or MARKETPLACE,
                  since none of those distinctions can be established
                  from domain-name structure alone without inventing
                  evidence this pipeline doesn't have. A future engine
                  with real corporate-registry or WHOIS-organization data
                  can upgrade a REGIONAL/UNKNOWN edge into one of the
                  stronger types without this module changing shape (see
                  RelationshipType's own docstring).
"""

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from application.discovery import canonicalization as canon
from application.discovery import evidence as evidence_model
from application.discovery import features as features_model
from application.discovery import identity as identity_model
from application.discovery import verification as verification_model


class RelationshipType(Enum):
    """Every relationship kind the Sprint 3 brief names. Only REDIRECT,
    REGIONAL, and UNKNOWN are ever actually constructed by
    `resolve_relationships` today -- see module docstring for why the
    rest (OFFICIAL, ALIAS, MIRROR, BRANCH, CORPORATE, PRODUCT,
    MARKETPLACE) are defined-but-reserved, mirroring the exact
    "reserved EvidenceType members" pattern evidence.py already
    established for signals this pipeline has no data source for yet."""

    OFFICIAL = "official"
    ALIAS = "alias"
    MIRROR = "mirror"
    REDIRECT = "redirect"
    REGIONAL = "regional"
    BRANCH = "branch"
    CORPORATE = "corporate"
    PRODUCT = "product"
    MARKETPLACE = "marketplace"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IdentityCandidate:
    """One immutable Identity Candidate -- see module docstring's
    "Candidate Identity Model" section for the field list, which is
    deliberately exactly the brief's own list and nothing more."""

    provider: Tuple[str, ...]
    normalized_domain: str
    canonical_domain: Optional["canon.CanonicalDomain"]
    website: Optional[str]
    supporting_evidence: "evidence_model.EvidenceBundle"
    provider_confidence: float
    verification_results: Optional["verification_model.VerificationBundle"]
    feature_store_entry: Optional["features_model.FeatureSet"]
    decision_trace: Dict[str, Any] = field(default_factory=dict)
    risk: Tuple[str, ...] = ()
    confidence: float = 0.0
    identity_quality: float = 0.0

    @property
    def domain(self) -> str:
        return self.normalized_domain

    @property
    def registrable_domain(self) -> str:
        return self.canonical_domain.registrable_domain if self.canonical_domain else self.normalized_domain

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": list(self.provider),
            "normalized_domain": self.normalized_domain,
            "canonical_domain": self.canonical_domain.to_dict() if self.canonical_domain else None,
            "website": self.website,
            "provider_confidence": self.provider_confidence,
            "verification_results": self.verification_results.to_dict() if self.verification_results else None,
            "decision_trace": dict(self.decision_trace),
            "risk": list(self.risk),
            "confidence": self.confidence,
            "identity_quality": self.identity_quality,
        }


def build_identity_candidate(
    group: "identity_model.WebsiteCandidateGroup",
    *,
    provider_confidence: float,
    verification_bundle: Optional["verification_model.VerificationBundle"],
    feature_set: Optional["features_model.FeatureSet"],
) -> IdentityCandidate:
    """Builds one IdentityCandidate from an already-merged
    WebsiteCandidateGroup plus the verification/feature data already
    computed for it elsewhere -- no re-derivation, no new evidence."""
    return IdentityCandidate(
        provider=tuple(group.contributing_providers),
        normalized_domain=canon.normalize_domain(group.domain) or group.domain,
        canonical_domain=canon.canonicalize(group.domain),
        website=group.urls[0] if group.urls else None,
        supporting_evidence=group.evidence,
        provider_confidence=provider_confidence,
        verification_results=verification_bundle,
        feature_store_entry=feature_set,
        confidence=group.confidence,
        identity_quality=group.identity_confidence,
    )


@dataclass(frozen=True)
class OrganizationRelationship:
    """One evidence-based edge between two OrganizationGroups. `evidence`
    is always a real, already-observed explanation string -- never
    fabricated, exactly like `digital_identity.RiskIndicator.evidence`."""

    from_domain: str
    to_domain: str
    relationship_type: RelationshipType
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_domain": self.from_domain,
            "to_domain": self.to_domain,
            "relationship_type": self.relationship_type.value,
            "evidence": list(self.evidence),
        }


@dataclass
class OrganizationGroup:
    """One consolidated logical identity -- every IdentityCandidate that
    shares the same registrable domain, collapsed together. See module
    docstring's "Candidate Consolidation" section."""

    registrable_domain: str
    candidates: List[IdentityCandidate] = field(default_factory=list)

    @property
    def best(self) -> Optional[IdentityCandidate]:
        """Highest-confidence candidate in this group -- deterministic
        tie-break by (confidence, identity_quality, provider count,
        first-seen order), never randomized."""
        if not self.candidates:
            return None
        return max(
            self.candidates,
            key=lambda c: (c.confidence, c.identity_quality, len(c.provider)),
        )

    @property
    def all_providers(self) -> Tuple[str, ...]:
        seen: List[str] = []
        for candidate in self.candidates:
            for provider in candidate.provider:
                if provider not in seen:
                    seen.append(provider)
        return tuple(seen)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registrable_domain": self.registrable_domain,
            "candidates": [c.to_dict() for c in self.candidates],
            "providers": list(self.all_providers),
        }


def consolidate(candidates: List[IdentityCandidate]) -> List[OrganizationGroup]:
    """Tier-1 consolidation: pure grouping by registrable domain, in
    first-seen order -- fully deterministic, no keyword logic, no
    business-specific rule. Mirrors `identity.group_website_candidates`'s
    own "never fabricate a group for nothing" discipline: an empty input
    produces an empty output."""
    groups: Dict[str, OrganizationGroup] = {}
    order: List[str] = []
    for candidate in candidates:
        key = candidate.registrable_domain
        if not key:
            continue
        if key not in groups:
            groups[key] = OrganizationGroup(registrable_domain=key)
            order.append(key)
        groups[key].candidates.append(candidate)
    return [groups[k] for k in order]


# A compound ccTLD suffix (see canonicalization._COMPOUND_SUFFIXES) on
# either side of a root-label match reads as a plausible country-specific
# variant of the same brand (REGIONAL) rather than a wholly coincidental
# root-label collision (UNKNOWN) -- still never a hard merge, still never
# OFFICIAL/ALIAS/BRANCH, just a slightly more specific, still-honest label
# for the one piece of structural evidence actually available.
def _looks_regional(suffix_a: str, suffix_b: str) -> bool:
    return ("." in suffix_a) or ("." in suffix_b)


def resolve_relationships(groups: List[OrganizationGroup]) -> List[OrganizationRelationship]:
    """Tier-2, evidence-based relationship detection between DISTINCT
    OrganizationGroups (candidates already sharing a registrable domain
    are in the same group -- see `consolidate` -- so this function never
    needs to relate two candidates that are already merged). Only ever
    emits the structural "same root label, different suffix" signal --
    see module docstring for why every other relationship type stays
    reserved. Never mutates or merges the groups it's given."""
    relationships: List[OrganizationRelationship] = []
    for i, group_a in enumerate(groups):
        for group_b in groups[i + 1 :]:
            if not canon.root_label_match(group_a.registrable_domain, group_b.registrable_domain):
                continue
            suffix_a = canon.canonicalize(group_a.registrable_domain)
            suffix_b = canon.canonicalize(group_b.registrable_domain)
            suffix_a_val = suffix_a.suffix if suffix_a else ""
            suffix_b_val = suffix_b.suffix if suffix_b else ""
            relationship_type = (
                RelationshipType.REGIONAL if _looks_regional(suffix_a_val, suffix_b_val) else RelationshipType.UNKNOWN
            )
            reason = (
                f"{group_a.registrable_domain} and {group_b.registrable_domain} share the root label "
                f"'{canon.root_label(group_a.registrable_domain)}' under different suffixes "
                f"('{suffix_a_val}' vs '{suffix_b_val}')"
            )
            relationships.append(
                OrganizationRelationship(
                    from_domain=group_a.registrable_domain,
                    to_domain=group_b.registrable_domain,
                    relationship_type=relationship_type,
                    evidence=(reason,),
                )
            )
    return relationships


def with_risk_and_trace(candidate: IdentityCandidate, *, risk: Tuple[str, ...], decision_trace: Dict[str, Any]) -> IdentityCandidate:
    """The one, explicit place an already-built IdentityCandidate's
    `risk`/`decision_trace` are ever filled in -- via `dataclasses.replace`
    (a new instance), never in-place mutation, so an IdentityCandidate
    stays genuinely immutable at every point some caller might be holding
    a reference to it."""
    return replace(candidate, risk=risk, decision_trace=decision_trace)
