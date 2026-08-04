"""
Confidence Propagation Engine (Sprint 2B).

Sprint 1/2A confidence was flat and additive: `EvidenceBundle.confidence`
(and, on top of it, `WebsiteCandidateGroup.confidence`) is simply "sum
every evidence delta this candidate has, clamp to [0, 1]" -- one number,
with no record of which layer of trust it came from. The brief calls this
out specifically: "Current confidence is mostly additive. Replace this
with confidence propagation."

This module is that replacement for `application.discovery.identity.
BusinessIdentity.confidence`'s *source* (see identity.py -- the field
itself is unchanged, only what computes it is new): confidence is derived
in ordered stages, each one explicitly building on the last, each one
reporting what contributed, what reduced it, and what evidence was
missing that could have raised it further:

    Provider Confidence -> Website Confidence -> Business Confidence
                         -> Identity Confidence -> Final Confidence

Layering, enforced structurally: this module imports only
`application.discovery.features` (for FeatureId/FeatureSet) and
`application.discovery.verification` (for VerificationBundle) -- not
`application.discovery.evidence` and not `application.discovery.identity`.
Confidence propagation reads Features and verification results only,
never raw Evidence directly, for the same reason Verification does (see
verification.py's module docstring).

No new weights invented: every stage below sums `ExtractedFeature.
confidence_impact` / `VerificationResult.confidence_contribution` values
that features.py/verification.py already computed (which themselves,
where non-zero, are read back from evidence.py's own constants -- see
features.py's module docstring). Each stage draws on a disjoint subset of
features, so nothing is summed twice across stages.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from application.discovery.features import FeatureId, FeatureSet
from application.discovery.verification import VerificationBundle, VerificationStatus


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True)
class ConfidenceStageResult:
    """One stage of the propagation chain. Always reports all three of
    what the brief asks for: what contributed, what reduced confidence,
    and what evidence is missing -- even when a list is empty, so a
    caller never has to guess whether "nothing happened" or "we didn't
    check"."""

    stage: str
    value: float
    contributed: Tuple[str, ...]
    reduced: Tuple[str, ...]
    missing: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage,
            "value": self.value,
            "contributed": list(self.contributed),
            "reduced": list(self.reduced),
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class ConfidenceTrace:
    """The full provider -> website -> business -> identity -> final
    chain for one candidate. Immutable and complete -- every stage that
    ran is present, in order, even for a zero-evidence candidate (each
    stage just reports 0.0 with an explanatory `missing` entry rather
    than being omitted)."""

    stages: Tuple[ConfidenceStageResult, ...]

    @property
    def final(self) -> float:
        return self.stages[-1].value if self.stages else 0.0

    def by_stage(self, name: str) -> Optional[ConfidenceStageResult]:
        return next((s for s in self.stages if s.stage == name), None)

    def explain(self) -> List[str]:
        lines = []
        for stage in self.stages:
            line = f"[{stage.stage}] {stage.value:.3f}"
            if stage.contributed:
                line += " -- contributed: " + "; ".join(stage.contributed)
            if stage.reduced:
                line += " -- reduced: " + "; ".join(stage.reduced)
            if stage.missing:
                line += " -- missing: " + "; ".join(stage.missing)
            lines.append(line)
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {"stages": [s.to_dict() for s in self.stages], "final": self.final}


_EMPTY_TRACE_STAGES = ("provider", "website", "business", "identity", "final")


def empty_confidence_trace(reason: str = "no candidate was selected") -> ConfidenceTrace:
    """A complete, zero-valued trace for when there is nothing to
    propagate confidence through (e.g. no selected group) -- every stage
    is still present and explicit about why, rather than the trace being
    entirely absent."""
    return ConfidenceTrace(
        stages=tuple(
            ConfidenceStageResult(stage=name, value=0.0, contributed=(), reduced=(), missing=(reason,))
            for name in _EMPTY_TRACE_STAGES
        )
    )


class ConfidencePropagationEngine:
    """Stateless, pure: `propagate()` reads only `features` and
    `verification` -- no I/O, no randomness."""

    def propagate(self, features: FeatureSet, verification: VerificationBundle) -> ConfidenceTrace:
        provider_stage = self._provider_stage(features)
        website_stage = self._website_stage(features, verification, provider_stage)
        business_stage = self._business_stage(features, verification, website_stage)
        identity_stage = self._identity_stage(features, verification, business_stage)
        final_stage = self._final_stage(website_stage, identity_stage, verification)
        return ConfidenceTrace(stages=(provider_stage, website_stage, business_stage, identity_stage, final_stage))

    # -- Stage 1: Provider Confidence ---------------------------------------

    @staticmethod
    def _provider_stage(features: FeatureSet) -> ConfidenceStageResult:
        """"How much do independent providers corroborate this
        candidate?" -- PROVIDER_AGREEMENT only."""
        agreement = features.by_id(FeatureId.PROVIDER_AGREEMENT)
        value = _clamp(agreement.confidence_impact if agreement else 0.0, 0.0, 1.0)
        contributed = (agreement.explanation,) if agreement and agreement.confidence_impact > 0 else ()
        missing = () if contributed else ("no independent provider corroboration",)
        return ConfidenceStageResult(stage="provider", value=round(value, 3), contributed=contributed, reduced=(), missing=missing)

    # -- Stage 2: Website Confidence -----------------------------------------

    @staticmethod
    def _website_stage(
        features: FeatureSet, verification: VerificationBundle, previous: ConfidenceStageResult
    ) -> ConfidenceStageResult:
        """"Is this a legitimate, reachable site, on top of what the
        providers already told us?" -- adds REACHABILITY/HTTPS/
        CANONICAL_DOMAIN to the provider stage's value."""
        domain_result = verification.by_verifier("domain_verification")
        contributed: List[str] = []
        reduced: List[str] = []
        missing: List[str] = []
        delta = 0.0
        for feature_id in (FeatureId.REACHABILITY, FeatureId.HTTPS, FeatureId.CANONICAL_DOMAIN):
            feature = features.by_id(feature_id)
            if feature is None:
                continue
            if feature.confidence_impact > 0:
                delta += feature.confidence_impact
                contributed.append(feature.explanation)
            elif feature.normalized_value == 0.0:
                reduced.append(feature.explanation)
        if domain_result is not None and domain_result.status is not VerificationStatus.PASSED:
            reduced.append(domain_result.reason)
        structure = features.by_id(FeatureId.WEBSITE_STRUCTURE)
        if structure is not None and structure.normalized_value is None:
            missing.append(structure.explanation)
        value = _clamp(previous.value + delta, 0.0, 1.0)
        return ConfidenceStageResult(
            stage="website", value=round(value, 3), contributed=tuple(contributed), reduced=tuple(reduced), missing=tuple(missing)
        )

    # -- Stage 3: Business Confidence ----------------------------------------

    @staticmethod
    def _business_stage(
        features: FeatureSet, verification: VerificationBundle, previous: ConfidenceStageResult
    ) -> ConfidenceStageResult:
        """"How complete/trustworthy is the underlying business record?"
        -- adds a small, bounded bonus from BUSINESS_COMPLETENESS/
        CONTACT_COMPLETENESS on top of the website stage."""
        business_result = verification.by_verifier("business_verification")
        completeness = features.by_id(FeatureId.BUSINESS_COMPLETENESS)
        contact = features.by_id(FeatureId.CONTACT_COMPLETENESS)
        contributed: List[str] = []
        reduced: List[str] = []
        missing: List[str] = []
        delta = 0.0
        # Completeness is informational (confidence_impact=0.0 on the
        # feature itself, see features.py) -- the bounded bonus it earns
        # here is a deliberately small, capped nudge (at most 0.05),
        # never large enough to substitute for real identity/verification
        # evidence, exactly so an empty-but-plausible record can't
        # outrank a thin-but-verified one.
        if completeness is not None and completeness.normalized_value is not None:
            if completeness.normalized_value >= 0.5:
                delta += round(completeness.normalized_value * 0.05, 4)
                contributed.append(completeness.explanation)
            else:
                reduced.append(completeness.explanation)
        if contact is not None and contact.normalized_value is not None and contact.normalized_value < 1.0:
            missing.append("email is never available as a contact channel in this pipeline")
        if business_result is not None and business_result.status is VerificationStatus.INCONCLUSIVE:
            missing.append(business_result.reason)
        value = _clamp(previous.value + delta, 0.0, 1.0)
        return ConfidenceStageResult(
            stage="business", value=round(value, 3), contributed=tuple(contributed), reduced=tuple(reduced), missing=tuple(missing)
        )

    # -- Stage 4: Identity Confidence ----------------------------------------

    @staticmethod
    def _identity_stage(
        features: FeatureSet, verification: VerificationBundle, previous: ConfidenceStageResult
    ) -> ConfidenceStageResult:
        """"Does the matched identity actually look like the business?"
        -- adds BRAND_SIMILARITY/LOCATION_CONSISTENCY on top of the
        business stage. This is the stage a fast, HTTPS, root-URL page
        with zero relation to the business name would fail to gain
        anything from, no matter how high the earlier stages ran."""
        identity_result = verification.by_verifier("identity_verification")
        brand = features.by_id(FeatureId.BRAND_SIMILARITY)
        location = features.by_id(FeatureId.LOCATION_CONSISTENCY)
        contributed: List[str] = []
        reduced: List[str] = []
        missing: List[str] = []
        delta = 0.0
        for feature in (brand, location):
            if feature is None:
                continue
            if feature.confidence_impact > 0:
                delta += feature.confidence_impact
                contributed.append(feature.explanation)
            else:
                missing.append(feature.explanation)
        if identity_result is not None and identity_result.status is VerificationStatus.FAILED:
            reduced.append(identity_result.reason)
        value = _clamp(previous.value + delta, 0.0, 1.0)
        return ConfidenceStageResult(
            stage="identity", value=round(value, 3), contributed=tuple(contributed), reduced=tuple(reduced), missing=tuple(missing)
        )

    # -- Stage 5: Final Confidence --------------------------------------------

    @staticmethod
    def _final_stage(
        website_stage: ConfidenceStageResult,
        identity_stage: ConfidenceStageResult,
        verification: VerificationBundle,
    ) -> ConfidenceStageResult:
        """Verification gates the final number, deterministically: if any
        *critical* verifier (site reachability, identity match) failed,
        final confidence is 0 regardless of what the earlier stages
        summed to -- a live-but-wrong site, or a well-matched name on an
        unreachable domain, must never report a confident final number.
        Otherwise, final confidence is the identity stage's value as-is
        -- identity is the last, most specific stage in the chain, so
        nothing further is added; this stage exists to apply the
        verification gate, not to introduce a sixth number."""
        if not verification.overall_passed:
            failed = [r.reason for r in verification.results if r.critical and r.status is not VerificationStatus.PASSED]
            return ConfidenceStageResult(
                stage="final", value=0.0, contributed=(), reduced=tuple(failed), missing=()
            )
        return ConfidenceStageResult(
            stage="final",
            value=identity_stage.value,
            contributed=("all critical verifiers passed",),
            reduced=(),
            missing=(),
        )
