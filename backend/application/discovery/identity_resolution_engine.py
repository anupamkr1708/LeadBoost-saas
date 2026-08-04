"""
Identity Resolution Engine (Sprint 3; extended Sprint 4).

Orchestrates every other Sprint 3 module -- `canonicalization`,
`organization`, `reliability`, `competition`, `false_positive` -- into
one `IdentityResolutionResult`, consumed additively by
`digital_identity.DigitalIdentityBuilder` exactly the way that module
already consumes `ConsensusEngine`/`assess_risk`/`assess_verification_quality`.

Position in the pipeline / layering discipline (per the Sprint 3 brief's
section 13, "Follow existing architecture... Do NOT bypass layers"):

    Evidence -> Features -> Verification -> Confidence -> Business Identity
                                                                |
                                              IdentityResolutionEngine.resolve()
                                                                |
                                              DigitalIdentityBuilder.build()
                                                                |
                                                Verified Digital Identity

Every input `resolve()` reads is already-computed, upstream output:
`BusinessIdentity.website_candidates` (Sprint 2A), `.feature_store`
(Sprint 2B), `.decision_trace.provider_agreement`/`.confidence_trace`
(Sprint 2A/2B), the `VerificationTrace` `DigitalIdentityBuilder` itself
already builds (Sprint 2C), and -- Sprint 4 -- the `ConsensusRecord`
`DigitalIdentityBuilder` also already builds, one line earlier in the
same method. Nothing here performs I/O, calls a provider, or scrapes a
page. Every new signal produced is one of the four kinds section 13
allows: a Feature-level composite (`evidence_diversity`), a Confidence
Signal (`competition.CompetitionResult.relative_confidence`), or an
Identity Signal (organization consolidation, conflict/risk reason
codes) -- never a standalone heuristic bolted on outside the pipeline.

Never changes the selected website: exactly like `digital_identity.py`
itself (see that module's own "never fabricated, never blocking"
section), nothing here can feed back into `WebsiteCandidateGroup.
confidence`, `VerificationBundle.overall_passed`, `ConfidenceTrace.final`,
or which domain `WebsiteResolver.resolve()` returns. `IdentityResolutionResult`
is purely an additional, richer explanation of the pool `BusinessIdentity`
already decided on -- "gain or lose confidence relative to competing
candidates" (component 5) only ever adjusts the *diagnostic*
`relative_confidence` a caller sees on `VerifiedDigitalIdentity`, never
`BusinessIdentity.selected_website` itself.

Sprint 4 (Discovery Quality Refinement)
-------------------------------------------
Three additive changes, no new abstraction layer, no redesign of
anything above:

  1. `resolve()` gains one new, optional, keyword-only parameter,
     `consensus` (defaulted to None) -- when provided, `identity_stability`
     is computed from FOUR already-existing 0.0-1.0 signals (competition
     margin, consensus, evidence diversity, conflict) instead of two, per
     that sprint's "combine existing metrics, don't invent new
     heuristics" goal. Every existing direct call to `resolve()` (this
     module's own tests included) omits the new parameter and gets back
     the exact, unchanged Sprint 3 two-factor formula.
  2. Provider Reliability observation now also threads through
     `agreed_with_peers` (from the already-computed
     `ProviderAgreementRecord`, Sprint 2A, untouched), and
     `provider_operational_metrics` exposes `reliability.
     ProviderReliabilityRecord.operational_metrics()` for every provider
     involved -- see `reliability.py`'s own Sprint 4 docstring section for
     what each metric means.
  3. `IdentityResolutionResult.explain()` -- a new method, not a new
     class -- produces the concise, structured, deterministic
     explanation (per that sprint's goal #6) for the selected candidate
     and every rejected one, in the same `.explain() -> List[str]` style
     `DecisionTrace`/`ProviderAgreementRecord`/`VerifiedDigitalIdentity`
     already use elsewhere in this package.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from application.discovery import competition as competition_model
from application.discovery import false_positive as fp_model
from application.discovery import features as features_model
from application.discovery import grounding
from application.discovery import identity as identity_model
from application.discovery import organization as org_model
from application.discovery import reliability as reliability_model
from application.discovery.dto import BusinessCandidate

# Note: `digital_identity.VerificationTrace`/`.ConsensusRecord` are
# referenced only as quoted (forward-reference) type hints below, never
# imported for real -- `digital_identity.py` is downstream of this module
# (it is the one that imports and calls `IdentityResolutionEngine`), so
# importing it back here would create the exact import cycle every
# module in this package is built to avoid (see features.py's own
# docstring for the general rule this follows).


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class AlternativeCandidateReport:
    """One candidate that was NOT the final selection, reported alongside
    it -- "Alternative Candidates" from the Sprint 3 brief's section 11.
    Never includes the selected candidate itself."""

    domain: str
    rank: int
    confidence: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"domain": self.domain, "rank": self.rank, "confidence": self.confidence, "reason": self.reason}


@dataclass(frozen=True)
class RejectedCandidateReport:
    """One candidate's full set of reasons for losing -- "Rejected
    Candidate Reasons" from section 11. Combines the original,
    Sprint-2A-level rejection reason (`WebsiteCandidateGroup.
    rejection_reason`, e.g. a validation failure) with any Sprint 3
    structural false-positive signals that additionally fired for it --
    never replaces the former, only adds to it."""

    domain: str
    reasons: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"domain": self.domain, "reasons": list(self.reasons)}


# Fixed, documented, 0.5/0.5 combination of two already-computed 0.0-1.0
# signals -- how decisively the leader won its own competition, and how
# conflict-free the pool as a whole is -- in the same "no new arbitrary
# weights" style digital_identity.py's own _CRITICAL_QUALITY_WEIGHT/
# _ADVISORY_QUALITY_WEIGHT already use. Split evenly on purpose: a
# decisive win in a pool full of OTHER conflicts (e.g. a close
# runner-up on a different axis) shouldn't alone read as fully stable,
# and a conflict-free pool with a wafer-thin margin shouldn't either.
# Used whenever `resolve()` is called without a `consensus` argument
# (every pre-Sprint-4 caller) -- see `_STABILITY_QUARTER_WEIGHT` below
# for the Sprint 4 formula used when consensus IS available.
_STABILITY_MARGIN_WEIGHT = 0.5
_STABILITY_CONFLICT_WEIGHT = 0.5

# Sprint 4: when a ConsensusRecord is available, identity_stability
# combines FOUR already-computed 0.0-1.0 signals -- competition margin,
# consensus, evidence diversity, and conflict (inverted) -- per that
# sprint's "combine existing metrics, don't invent new heuristics" goal.
# Equal quartering is the simplest, most defensible combination of four
# already-bounded signals with no basis (and no goal, per the brief) for
# weighting one over another.
_STABILITY_QUARTER_WEIGHT = 0.25


@dataclass(frozen=True)
class IdentityResolutionResult:
    """The complete Sprint 3 Identity Resolution Engine output for one
    business. See each field's producing module for how it was computed;
    nothing here is computed twice."""

    identity_candidates: Tuple["org_model.IdentityCandidate", ...]
    organization_groups: Tuple["org_model.OrganizationGroup", ...]
    organization_relationships: Tuple["org_model.OrganizationRelationship", ...]
    competition: Tuple["competition_model.CompetitionResult", ...]
    conflict_assessment: "competition_model.ConflictAssessment"
    provider_reliability: Dict[str, float]
    selection_confidence: float
    selection_reason: str
    alternative_candidates: Tuple[AlternativeCandidateReport, ...]
    rejected_candidate_reasons: Tuple[RejectedCandidateReport, ...]
    identity_stability: float
    evidence_diversity: float
    # Sprint 4: reliability.ProviderReliabilityRecord.operational_metrics()
    # for every provider involved in this business's resolution -- see
    # reliability.py's own docstring for what each of the four metrics
    # means. Defaulted so `empty_identity_resolution_result` (and any
    # pre-Sprint-4 direct construction) doesn't need to supply it.
    provider_operational_metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_candidates": [c.to_dict() for c in self.identity_candidates],
            "organization_groups": [g.to_dict() for g in self.organization_groups],
            "organization_relationships": [r.to_dict() for r in self.organization_relationships],
            "competition": [c.to_dict() for c in self.competition],
            "conflict_assessment": self.conflict_assessment.to_dict(),
            "provider_reliability": dict(self.provider_reliability),
            "selection_confidence": self.selection_confidence,
            "selection_reason": self.selection_reason,
            "alternative_candidates": [a.to_dict() for a in self.alternative_candidates],
            "rejected_candidate_reasons": [r.to_dict() for r in self.rejected_candidate_reasons],
            "identity_stability": self.identity_stability,
            "evidence_diversity": self.evidence_diversity,
            "provider_operational_metrics": {k: dict(v) for k, v in self.provider_operational_metrics.items()},
            "explanation": self.explain(),
        }

    def explain(self) -> List[str]:
        """Sprint 4, goal #6: a concise, structured, deterministic
        explanation covering the selected candidate and every rejected
        one -- same `.explain() -> List[str]` convention
        `DecisionTrace`/`ProviderAgreementRecord`/`VerifiedDigitalIdentity`
        already use. Every line is built from a field already sitting on
        this object; nothing here is recomputed or fabricated."""
        lines = [f"selection: {self.selection_reason} (confidence={self.selection_confidence:.3f})"]
        lines.append(
            f"stability={self.identity_stability:.3f}, evidence_diversity={self.evidence_diversity:.3f}, "
            f"conflict_score={self.conflict_assessment.conflict_score:.3f}"
        )
        if self.conflict_assessment.conflicts:
            lines.append(
                "conflicts: " + "; ".join(f"{c.code} ({c.description})" for c in self.conflict_assessment.conflicts)
            )
        if self.alternative_candidates:
            lines.append(
                "alternatives: "
                + "; ".join(f"{a.domain} (rank {a.rank}, {a.reason})" for a in self.alternative_candidates)
            )
        if self.rejected_candidate_reasons:
            lines.append(
                "rejected: "
                + "; ".join(f"{r.domain} ({', '.join(r.reasons)})" for r in self.rejected_candidate_reasons)
            )
        return lines


def empty_identity_resolution_result(reason: str = "no candidates were ever proposed") -> IdentityResolutionResult:
    return IdentityResolutionResult(
        identity_candidates=(),
        organization_groups=(),
        organization_relationships=(),
        competition=(),
        conflict_assessment=competition_model.empty_conflict_assessment(),
        provider_reliability={},
        selection_confidence=0.0,
        selection_reason=reason,
        alternative_candidates=(),
        rejected_candidate_reasons=(),
        identity_stability=0.0,
        evidence_diversity=0.0,
    )


class IdentityResolutionEngine:
    """See module docstring. Stateful only through its two injected
    registries (`reliability_registry`, `domain_observations`) -- every
    method below is otherwise a pure function of its arguments, exactly
    like `ConsensusEngine`/`ConfidencePropagationEngine` elsewhere in this
    package. Defaulted, constructor-injectable, same DI pattern as every
    other extension point in this package."""

    def __init__(
        self,
        reliability_registry: Optional["reliability_model.ProviderReliabilityRegistry"] = None,
        domain_observations: Optional["fp_model.DomainObservationRegistry"] = None,
    ):
        self.reliability_registry = reliability_registry or reliability_model.ProviderReliabilityRegistry()
        self.domain_observations = domain_observations or fp_model.DomainObservationRegistry()

    def resolve(
        self,
        business_identity: "identity_model.BusinessIdentity",
        business: BusinessCandidate,
        verification_trace: Any,  # digital_identity.VerificationTrace -- not imported here, see module docstring's layering note
        *,
        consensus: Any = None,  # digital_identity.ConsensusRecord, Sprint 4 -- optional, see module docstring
    ) -> IdentityResolutionResult:
        groups = business_identity.website_candidates
        if not groups:
            return empty_identity_resolution_result("no candidates were ever proposed")

        decision_trace = business_identity.decision_trace
        selected_domain = decision_trace.selected_domain

        # -- 1. Candidate Identity Model -------------------------------------
        identity_candidates = [
            org_model.build_identity_candidate(
                group,
                provider_confidence=self._provider_confidence(group),
                verification_bundle=verification_trace.by_domain(group.domain),
                feature_set=business_identity.feature_store.get(group.domain),
            )
            for group in groups
        ]

        # -- 2. Evidence Corroboration + False Positive Reduction, per candidate --
        # Cross-tenant domain observation happens once per distinct domain
        # per business (never once per contributing provider -- see
        # false_positive.py's own "never double count" discipline).
        for candidate, group in zip(identity_candidates, groups):
            feature_set = candidate.feature_store_entry
            brand = feature_set.by_id(features_model.FeatureId.BRAND_SIMILARITY) if feature_set is not None else None
            brand_strength = brand.normalized_value if brand and brand.normalized_value is not None else grounding.brand_match_strength(business.name, candidate.registrable_domain)
            self.domain_observations.observe(candidate.registrable_domain, business.name, brand_strength)

        finalized_candidates = []
        for candidate, group in zip(identity_candidates, groups):
            signals = fp_model.evaluate_candidate(candidate, self.domain_observations)
            reasons: List[str] = [s.code for s in signals]
            if group.rejection_reason and group.rejection_reason not in reasons:
                reasons.append(group.rejection_reason)
            finalized_candidates.append(
                org_model.with_risk_and_trace(
                    candidate,
                    risk=tuple(s.code for s in signals),
                    decision_trace={"structural_signals": [s.to_dict() for s in signals]},
                )
            )
        identity_candidates = finalized_candidates

        # -- 3. Provider Reliability -------------------------------------------
        # Sprint 4: `peer_status` derives "did this provider's proposal
        # converge with an independent second provider" purely from the
        # already-computed ProviderAgreementRecord (Sprint 2A, untouched)
        # -- no new data source. Only ever populated for "agreement"/
        # "disagreement" outcomes; "single_source"/"no_candidates" leave a
        # provider's peer status unset (None), meaning "no peer to agree
        # or disagree with" rather than a fabricated True/False.
        provider_agreement = decision_trace.provider_agreement
        peer_status: Dict[str, bool] = {}
        if provider_agreement.status == "agreement":
            for provider in provider_agreement.agreeing_providers:
                peer_status[provider] = True
        elif provider_agreement.status == "disagreement":
            for disagreement in provider_agreement.disagreements:
                peer_status[disagreement["provider"]] = False

        for group in groups:
            for provider in group.contributing_providers:
                self.reliability_registry.observe_proposal(
                    provider,
                    verified=group.verified,
                    agreed_with_selection=(group.domain == selected_domain),
                    agreed_with_peers=peer_status.get(provider),
                )
        involved_providers = provider_agreement.agreeing_providers + [
            d["provider"] for d in provider_agreement.disagreements
        ]
        provider_reliability = {
            provider: self.reliability_registry.score(provider) for provider in involved_providers
        }
        provider_operational_metrics = {
            provider: self.reliability_registry.record(provider).operational_metrics()
            for provider in involved_providers
        }

        # -- 4. Candidate Consolidation + Organization Resolution --------------
        organization_groups = org_model.consolidate(identity_candidates)
        relationships = org_model.resolve_relationships(organization_groups)

        # -- 5. Candidate Competition --------------------------------------------
        competition = competition_model.compete(identity_candidates)
        by_domain_competition = {c.domain: c for c in competition}
        ordered_candidates = sorted(
            identity_candidates, key=lambda c: (-c.confidence, -c.identity_quality, -len(c.provider))
        )

        # -- 6. Conflict Resolution ------------------------------------------------
        conflicts = list(
            competition_model.assess_conflicts(
                provider_agreement=decision_trace.provider_agreement,
                organization_groups=organization_groups,
                relationships=relationships,
                competition=competition,
            ).conflicts
        )
        quality_gap = competition_model.identity_quality_gap_conflict(ordered_candidates)
        if quality_gap is not None:
            conflicts.append(quality_gap)
        conflict_assessment = competition_model.build_conflict_assessment(conflicts)

        # -- 7. Selection confidence / reason, alternatives, rejections --------
        selected_candidate = next((c for c in identity_candidates if c.domain == selected_domain), None)
        selected_competition = by_domain_competition.get(selected_domain) if selected_domain else None

        if selected_competition is not None:
            selection_confidence = selected_competition.relative_confidence
            selection_reason = selected_competition.reason
        elif selected_domain:
            selection_confidence = selected_candidate.confidence if selected_candidate else 0.0
            selection_reason = f"{selected_domain} selected"
        else:
            selection_confidence = 0.0
            selection_reason = "no candidate was selected"

        alternative_candidates = tuple(
            AlternativeCandidateReport(
                domain=c.domain, rank=by_domain_competition[c.domain].rank, confidence=by_domain_competition[c.domain].relative_confidence, reason=by_domain_competition[c.domain].reason
            )
            for c in identity_candidates
            if c.domain != selected_domain and c.domain in by_domain_competition
        )

        rejected_candidate_reasons = tuple(
            RejectedCandidateReport(domain=c.domain, reasons=tuple(dict.fromkeys(list(c.risk) + ([g.rejection_reason] if g.rejection_reason else []))))
            for c, g in zip(identity_candidates, groups)
            if c.domain != selected_domain and (c.risk or g.rejection_reason)
        )

        # -- 8. Identity stability + evidence diversity (selected candidate) ----
        if selected_candidate is not None:
            evidence_diversity_score = competition_model.evidence_diversity(selected_candidate)
        else:
            evidence_diversity_score = 0.0

        if competition:
            leader_margin = _clamp(competition[0].margin, 0.0, 1.0)
        else:
            leader_margin = 0.0

        if not selected_domain:
            identity_stability = 0.0
        elif consensus is not None:
            # Sprint 4: four already-computed signals instead of two --
            # see module docstring's "Sprint 4" section and
            # _STABILITY_QUARTER_WEIGHT's own comment for why equal
            # quartering.
            overall_consensus = _clamp(getattr(consensus, "overall_consensus", 0.0), 0.0, 1.0)
            identity_stability = round(
                _clamp(
                    _STABILITY_QUARTER_WEIGHT * leader_margin
                    + _STABILITY_QUARTER_WEIGHT * (1.0 - conflict_assessment.conflict_score)
                    + _STABILITY_QUARTER_WEIGHT * overall_consensus
                    + _STABILITY_QUARTER_WEIGHT * evidence_diversity_score,
                    0.0,
                    1.0,
                ),
                3,
            )
        else:
            # Pre-Sprint-4 formula, unchanged -- every existing caller
            # that doesn't pass `consensus` gets back exactly this.
            identity_stability = round(
                _clamp(
                    _STABILITY_MARGIN_WEIGHT * leader_margin
                    + _STABILITY_CONFLICT_WEIGHT * (1.0 - conflict_assessment.conflict_score),
                    0.0,
                    1.0,
                ),
                3,
            )

        return IdentityResolutionResult(
            identity_candidates=tuple(identity_candidates),
            organization_groups=tuple(organization_groups),
            organization_relationships=tuple(relationships),
            competition=tuple(competition),
            conflict_assessment=conflict_assessment,
            provider_reliability=provider_reliability,
            selection_confidence=selection_confidence,
            selection_reason=selection_reason,
            alternative_candidates=alternative_candidates,
            rejected_candidate_reasons=rejected_candidate_reasons,
            identity_stability=identity_stability,
            evidence_diversity=evidence_diversity_score,
            provider_operational_metrics=provider_operational_metrics,
        )

    def _provider_confidence(self, group: "identity_model.WebsiteCandidateGroup") -> float:
        """Reliability-weighted provider signal for one candidate group --
        the mean current reliability score of every provider that
        contributed to it (before this business's own observations are
        recorded, so a candidate is judged on history, not on itself)."""
        providers = group.contributing_providers
        if not providers:
            return 0.0
        return round(sum(self.reliability_registry.score(p) for p in providers) / len(providers), 3)
