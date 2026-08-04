"""
Verified Digital Identity (Sprint 2C).

Sprint 1 gave every website candidate explainable, evidence-derived
confidence. Sprint 2A turned "a business has a website" into "a business
has an identity, of which a website is one attribute" (BusinessIdentity).
Sprint 2B replaced flat, additive confidence with a genuine Evidence ->
Features -> Verification -> Confidence pipeline, but the *terminal* object
Discovery produced was still BusinessIdentity -- a rich record of *how*
one candidate was selected, not a first-class judgment of *how good* the
resulting digital identity actually is.

This module is that judgment layer, and the new terminal object:

    Evidence -> Features -> Verification -> Confidence -> Identity
             -> Verified Digital Identity

BusinessIdentity does not disappear and is not redesigned -- every one of
its fields keeps exactly the meaning Sprint 2A/2B gave it (see
identity.py's own module docstring). It becomes the *internal* record
this module wraps: `VerifiedDigitalIdentity.business_identity` is a plain
reference to it, never a copy. Nothing in identity.py, features.py,
verification.py, confidence.py, or evidence.py changes in this sprint --
this module is purely additive, reading their already-computed output.

Position in the pipeline / layering discipline
-----------------------------------------------
This is the LAST stage. It imports `application.discovery.identity`,
`.features`, `.verification`, `.confidence`, and `.dto` -- all of which
are upstream of it -- and nothing downstream of it exists to import back,
so there is no cycle to guard against (unlike, say, features.py, which
had to avoid importing identity.py precisely because identity.py imports
features.py). See each of those modules' own docstrings for the
one-way-pipeline discipline this module continues.

What "Verified Digital Identity" aggregates
---------------------------------------------
Per the Sprint 2C brief, `VerifiedDigitalIdentity` aggregates:

  - Business Identity       -- `business_identity` (BusinessIdentity, ref)
  - Selected Website         -- `selected_website`
  - Provider Consensus       -- `consensus.provider_consensus`, one
                                 dimension of the broader `consensus`
                                 (see "Consensus Engine" below -- the
                                 brief is explicit that consensus is not
                                 only providers, so there is one
                                 `ConsensusRecord`, not a separate
                                 provider-only field)
  - Verification Trace       -- `verification_trace` (every candidate
                                 group's own VerificationBundle, not just
                                 the selected one -- distinct from...)
  - Verification Bundle      -- `verification_bundle` (...the *selected*
                                 candidate's own bundle, reused as-is from
                                 BusinessIdentity.decision_trace)
  - Confidence Trace          -- `confidence_trace` (reused as-is)
  - Feature Store             -- `feature_store` (reused as-is, same
                                 object BusinessIdentity already holds)
  - Evidence Bundle           -- `evidence_bundle` (reused as-is)
  - Identity Completeness     -- `identity_completeness`
  - Verification Quality      -- `verification_quality`
  - Risk Indicators            -- `risk_assessment.indicators`
  - Missing Evidence          -- `missing_evidence` (union of
                                 DecisionTrace's own list and
                                 IdentityCompleteness's own gaps)
  - Digital Identity Status   -- `status`
  - Verification Timestamp    -- `verified_at`

Never fabricated, never blocking
-----------------------------------
Every assessment below (`IdentityCompleteness`, `VerificationQuality`,
`ConsensusRecord`, `RiskAssessment`, `DigitalIdentityQuality`) is a pure,
deterministic function of data BusinessIdentity/FeatureStore/
VerificationBundle/ConfidenceTrace already computed -- no new evidence is
invented, no network I/O, no ML/embeddings/LLMs/graph databases. Risk in
particular is explicitly diagnostic, never gating: nothing in this module
feeds back into BusinessIdentity, WebsiteCandidateGroup.confidence,
VerificationBundle.overall_passed, or ConfidenceTrace.final -- a
VerifiedDigitalIdentity can carry a "high" risk level while still
reporting the exact same selected website and confidence the rest of the
pipeline already decided on. "Risk explains uncertainty; it never blocks"
(per the brief) is enforced structurally here the same way "no verifier
decides alone" is enforced in verification.py: this module only reads
already-finalized upstream results, it has no seam through which it could
change them.

Generic by construction, exactly like every prior sprint's layer: nothing
below branches on business category, and every extracted signal is read
back through `FeatureId`/`VerificationResult`/`ConfidenceStageResult` --
structural properties of however many features/verifiers/stages actually
ran -- rather than a fixed, hardcoded count. A Sprint-3-registered
verifier or feature is picked up automatically, exactly as
verification.py's own `VerificationBundle.overall_passed` already is.

Sprint 3: Identity Resolution Engine
---------------------------------------
Sprint 2C's own notes named the seam Sprint 3 would need
("Wire resolve_with_digital_identity() into DiscoveryService... the
seam already exists and is already tested") and flagged "cross-business
consensus" as the natural next extension point. Sprint 3 arrives here,
additively, exactly through that seam: `DigitalIdentityBuilder` gains one
more defaulted, constructor-injectable collaborator
(`identity_resolution_engine`, see `application.discovery.
identity_resolution_engine.IdentityResolutionEngine`), and `build()`
threads its result onto two, purely additive places:

  - `VerifiedDigitalIdentity.identity_resolution` -- the full
    `IdentityResolutionResult` (consolidated organizations, candidate
    competition, conflict assessment, provider reliability, false
    -positive signals); `selection_confidence`/`selection_reason`/
    `alternative_candidates`/`rejected_candidate_reasons` are exposed as
    read-only properties delegating to it (single source of truth,
    never duplicated storage).
  - `DigitalIdentityQuality.identity_stability` /`.evidence_diversity`/
    `.conflict_score` -- three more already-computed 0.0-1.0 dimensions
    folded into the same quality rollup `assess_digital_identity_quality`
    already produced, via one new, trailing, defaulted parameter so
    every pre-Sprint-3 call site (and test) keeps working unchanged.

Every one of the "never fabricated, never blocking" guarantees this
module's own docstring already makes above continue to hold: Sprint 3's
new signals are read the same way every existing one is -- computed once,
from data `BusinessIdentity`/`FeatureStore`/`VerificationTrace` already
produced, then only ever read back, never fed back into
`WebsiteCandidateGroup.confidence`/`VerificationBundle.overall_passed`/
`ConfidenceTrace.final`/`BusinessIdentity.selected_website`. See
`identity_resolution_engine.py`'s own module docstring for the full
Sprint 3 design.
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from application.discovery import confidence as confidence_model
from application.discovery import evidence as evidence_model
from application.discovery import features as features_model
from application.discovery import identity as identity_model
from application.discovery import identity_resolution_engine as identity_resolution_model
from application.discovery import verification as verification_model
from application.discovery.dto import BusinessCandidate


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _dedupe(items: List[str]) -> Tuple[str, ...]:
    """Order-preserving de-duplication -- used everywhere below that
    merges two already-independently-computed "missing"/"weak signal"
    lists, so a signal reported by two different assessors appears once,
    not twice."""
    return tuple(dict.fromkeys(items))


# ---------------------------------------------------------------------------
# Shared, documented thresholds
# ---------------------------------------------------------------------------
#
# Every threshold below is a fixed, documented cut point on an already
#-normalized 0.0-1.0 scale -- exactly the same kind of constant
# evidence.py/features.py/confidence.py already define (BRAND_MATCH_MAX_
# WEIGHT, _MAX_OBSERVABLE_EVIDENCE_TYPES, the 0.05 business-completeness
# cap, ...). None of these are business-category-specific and none of
# them gate a decision -- they only decide which bucket a diagnostic
# label falls into.

# A brand-similarity/location-consistency value below this is "weak" --
# meaningfully below a confident match, not merely imperfect. Chosen to
# sit below grounding.brand_match_strength's own 0.6-coverage floor for
# its weakest ("appears somewhere inside") match tier, so a match that
# only just clears that floor is still flagged as weak here.
_WEAK_BRAND_MATCH_THRESHOLD = 0.3
_WEAK_LOCATION_MATCH_THRESHOLD = 0.5  # location is 1.0 (address) / 0.5 (domain text) / 0.0 in practice -- 0.5 catches only the unconfirmed domain-text tier

# Evidence density below this ratio (fewer than 2 of the 6 observable
# EvidenceType members features._MAX_OBSERVABLE_EVIDENCE_TYPES already
# documents) is "thin" -- not enough independent observation to trust a
# candidate's confidence number at face value.
_THIN_EVIDENCE_DENSITY_THRESHOLD = 0.34

# Below this, IdentityCompleteness.overall_score reflects a materially
# incomplete identity (missing more than half of what completeness
# measures across its 8 equally-weighted dimensions -- see
# assess_identity_completeness).
_INCOMPLETE_IDENTITY_THRESHOLD = 0.5

# Below this, VerificationQuality.strength is "weak" even when the
# candidate formally passed verification -- i.e. the "passed but with
# weak evidence" case the brief calls out explicitly.
_LOW_VERIFICATION_STRENGTH_THRESHOLD = 0.4

# Consensus/quality bucket boundaries -- generic, fixed cut points on a
# 0.0-1.0 scale, not calibrated per category or per deployment.
_STRONG_THRESHOLD = 0.75
_MODERATE_THRESHOLD = 0.45
_WEAK_THRESHOLD = 0.15

# DigitalIdentityStatus grading thresholds for a verified identity --
# deliberately distinct constants from the consensus bucket boundaries
# above (status also folds in risk_level, consensus does not), kept
# separate so a future change to one never silently moves the other.
_STATUS_STRONG_THRESHOLD = 0.7
_STATUS_MODERATE_THRESHOLD = 0.4

# Verification-quality weighting: critical verifiers (the ones that gate
# VerificationBundle.overall_passed) should also dominate the *quality*
# signal, since they are the strongest evidence available; advisory
# verifiers refine that signal without being able to dominate it. Chosen
# to sum to 1.0 so `strength` stays on the same 0.0-1.0 scale as every
# other score in this module.
_CRITICAL_QUALITY_WEIGHT = 0.7
_ADVISORY_QUALITY_WEIGHT = 0.3

# A "high" risk level must always visibly dampen the top-line quality
# score -- otherwise a high-risk identity could still report a
# deceptively strong overall_score. Fixed, documented caps, not a learned
# calibration.
_HIGH_RISK_QUALITY_CAP = 0.5
_MEDIUM_RISK_QUALITY_CAP = 0.75


def _bucket(value: float) -> str:
    """Shared bucketing for both ConsensusRecord.level and any other
    0.0-1.0 score in this module that wants a human-readable label."""
    if value >= _STRONG_THRESHOLD:
        return "strong"
    if value >= _MODERATE_THRESHOLD:
        return "moderate"
    if value >= _WEAK_THRESHOLD:
        return "weak"
    return "none"


# ---------------------------------------------------------------------------
# Digital Identity Status
# ---------------------------------------------------------------------------


class DigitalIdentityStatus(Enum):
    """The top-line, human-facing verdict for one VerifiedDigitalIdentity.
    Deliberately coarser than the numeric scores it is derived from --
    see _derive_status below -- so a log line or dashboard can filter on
    one field without re-deriving the grading logic itself."""

    NO_IDENTITY = "no_identity"            # no candidate website was ever proposed
    UNVERIFIED = "unverified"               # a candidate existed but never passed verification
    VERIFIED_WEAK = "verified_weak"          # verified, but low quality and/or elevated risk
    VERIFIED_MODERATE = "verified_moderate"  # verified, with a moderate quality score
    VERIFIED_STRONG = "verified_strong"      # verified, high quality, no more than low risk


def _derive_status(
    verification_state: str, quality: "DigitalIdentityQuality", risk: "RiskAssessment"
) -> DigitalIdentityStatus:
    if verification_state == "no_candidates":
        return DigitalIdentityStatus.NO_IDENTITY
    if verification_state != "verified":
        return DigitalIdentityStatus.UNVERIFIED
    if quality.overall_score >= _STATUS_STRONG_THRESHOLD and risk.risk_level in ("none", "low"):
        return DigitalIdentityStatus.VERIFIED_STRONG
    if quality.overall_score >= _STATUS_MODERATE_THRESHOLD:
        return DigitalIdentityStatus.VERIFIED_MODERATE
    return DigitalIdentityStatus.VERIFIED_WEAK


# ---------------------------------------------------------------------------
# Verification Trace (every candidate's own VerificationBundle)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationTrace:
    """Every WebsiteCandidateGroup's own VerificationBundle, in candidate
    -pool order -- distinct from `VerifiedDigitalIdentity.verification_
    bundle`, which is only the *selected* candidate's bundle (reused
    as-is from BusinessIdentity.decision_trace). This is what lets a
    caller answer "why did every OTHER candidate fail verification too,"
    not just the one that won -- the same "never hide conflicting
    evidence" principle ProviderAgreementRecord already applies to
    provider disagreement, extended to verification.
    """

    selected_domain: Optional[str]
    bundles: Tuple[Tuple[str, Optional["verification_model.VerificationBundle"]], ...]

    def by_domain(self, domain: str) -> Optional["verification_model.VerificationBundle"]:
        return next((bundle for d, bundle in self.bundles if d == domain), None)

    def explain(self) -> List[str]:
        lines = []
        for domain, bundle in self.bundles:
            marker = " (selected)" if domain == self.selected_domain else ""
            if bundle is None:
                lines.append(f"{domain}{marker}: no verification data")
                continue
            lines.append(f"{domain}{marker}: overall_passed={bundle.overall_passed}")
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_domain": self.selected_domain,
            "bundles": {domain: (bundle.to_dict() if bundle else None) for domain, bundle in self.bundles},
        }


# ---------------------------------------------------------------------------
# Identity Completeness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityCompleteness:
    """Measures how much of a complete digital identity is actually
    present -- website / phone / address / coordinates / provider
    support / verification coverage / evidence coverage / confidence
    coverage -- without inventing anything for what's missing (per the
    brief). Every boolean/ratio here is read directly off already
    -collected data; `missing_information` names the gaps explicitly
    rather than silently reporting a low score with no explanation.
    """

    has_website: bool
    has_phone: bool
    has_address: bool
    has_coordinates: bool
    provider_support_ratio: float   # selected group's providers / all distinct providers seen in the pool
    verification_coverage: float    # fraction of verifiers that returned a decisive (non-INCONCLUSIVE) result
    evidence_coverage: float        # EVIDENCE_DENSITY's own normalized_value, reused (never recomputed)
    confidence_coverage: float      # fraction of pre-final confidence stages that had genuine contribution
    missing_information: Tuple[str, ...]
    overall_score: float            # unweighted mean of the 8 dimensions above

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_website": self.has_website,
            "has_phone": self.has_phone,
            "has_address": self.has_address,
            "has_coordinates": self.has_coordinates,
            "provider_support_ratio": self.provider_support_ratio,
            "verification_coverage": self.verification_coverage,
            "evidence_coverage": self.evidence_coverage,
            "confidence_coverage": self.confidence_coverage,
            "missing_information": list(self.missing_information),
            "overall_score": self.overall_score,
        }


def assess_identity_completeness(
    business: BusinessCandidate,
    selected_group: Optional["identity_model.WebsiteCandidateGroup"],
    groups: List["identity_model.WebsiteCandidateGroup"],
    feature_set: Optional["features_model.FeatureSet"],
    verification_bundle: Optional["verification_model.VerificationBundle"],
    confidence_trace: "confidence_model.ConfidenceTrace",
) -> IdentityCompleteness:
    """Pure function: reads only its arguments, no I/O. Never crashes on
    missing data -- a `None` selected_group / feature_set / verification_
    bundle degrades every dependent dimension to 0.0 with an explicit
    `missing_information` entry, exactly like every prior sprint's "no
    candidates" handling.
    """
    has_website = bool(selected_group and selected_group.domain)
    has_phone = bool(business.phone)
    has_address = bool(business.address)
    has_coordinates = business.latitude is not None and business.longitude is not None

    all_providers = set()
    for group in groups:
        all_providers.update(group.contributing_providers)
    total_providers_seen = len(all_providers)
    selected_provider_count = len(selected_group.contributing_providers) if selected_group else 0
    provider_support_ratio = (
        round(selected_provider_count / total_providers_seen, 3) if total_providers_seen else 0.0
    )

    if verification_bundle is not None and verification_bundle.results:
        decisive = [
            r for r in verification_bundle.results if r.status is not verification_model.VerificationStatus.INCONCLUSIVE
        ]
        verification_coverage = round(len(decisive) / len(verification_bundle.results), 3)
        inconclusive_count = len(verification_bundle.results) - len(decisive)
        total_verifiers = len(verification_bundle.results)
    else:
        verification_coverage = 0.0
        inconclusive_count = 0
        total_verifiers = 0

    evidence_coverage = 0.0
    if feature_set is not None:
        density = feature_set.by_id(features_model.FeatureId.EVIDENCE_DENSITY)
        if density is not None and density.normalized_value is not None:
            evidence_coverage = density.normalized_value

    pre_final_stages = confidence_trace.stages[:-1] if len(confidence_trace.stages) > 1 else confidence_trace.stages
    if pre_final_stages:
        contributing = sum(1 for stage in pre_final_stages if stage.contributed)
        confidence_coverage = round(contributing / len(pre_final_stages), 3)
    else:
        confidence_coverage = 0.0

    missing: List[str] = []
    if not has_website:
        missing.append("no verified website was selected")
    if not has_phone:
        missing.append("no phone number available")
    if not has_address:
        missing.append("no address available")
    if not has_coordinates:
        missing.append("no coordinates available")
    if total_providers_seen and provider_support_ratio < 1.0:
        missing.append(
            f"only {selected_provider_count} of {total_providers_seen} provider(s) that returned "
            f"a candidate support the selected domain"
        )
    if total_verifiers and inconclusive_count:
        missing.append(f"{inconclusive_count} of {total_verifiers} verifier(s) returned an inconclusive result")

    components = [
        1.0 if has_website else 0.0,
        1.0 if has_phone else 0.0,
        1.0 if has_address else 0.0,
        1.0 if has_coordinates else 0.0,
        provider_support_ratio,
        verification_coverage,
        evidence_coverage,
        confidence_coverage,
    ]
    overall_score = round(sum(components) / len(components), 3)

    return IdentityCompleteness(
        has_website=has_website,
        has_phone=has_phone,
        has_address=has_address,
        has_coordinates=has_coordinates,
        provider_support_ratio=provider_support_ratio,
        verification_coverage=verification_coverage,
        evidence_coverage=evidence_coverage,
        confidence_coverage=confidence_coverage,
        missing_information=tuple(missing),
        overall_score=overall_score,
    )


# ---------------------------------------------------------------------------
# Verification Quality (distinct from Verification Passed)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerificationQuality:
    """"Verification Passed" (VerificationBundle.overall_passed) and
    "Verification Quality" are deliberately different questions, per the
    brief: a business may pass verification (every critical verifier
    PASSED) while still resting on thin, mostly-INCONCLUSIVE advisory
    evidence. `passed` mirrors overall_passed as-is (never recomputed);
    `strength` is the continuous signal that exposes the "passed but
    weak" case `passed` alone cannot.
    """

    passed: bool
    strength: float               # 0.0-1.0, critical-weighted mean of every verifier's confidence_contribution
    inconclusive_ratio: float      # fraction of verifiers that returned INCONCLUSIVE
    weak_signals: Tuple[str, ...]  # reasons from every INCONCLUSIVE verifier, never fabricated
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "strength": self.strength,
            "inconclusive_ratio": self.inconclusive_ratio,
            "weak_signals": list(self.weak_signals),
            "explanation": self.explanation,
        }


def assess_verification_quality(
    verification_bundle: Optional["verification_model.VerificationBundle"],
) -> VerificationQuality:
    """Pure function. `None`/empty bundle (no candidate selected) is not
    an error -- it is honestly reported as zero strength, not fabricated
    as a pass or a fail."""
    if verification_bundle is None or not verification_bundle.results:
        return VerificationQuality(
            passed=False,
            strength=0.0,
            inconclusive_ratio=0.0,
            weak_signals=(),
            explanation="no verification was performed -- no candidate was selected",
        )

    results = verification_bundle.results
    critical = [r for r in results if r.critical]
    advisory = [r for r in results if not r.critical]

    def _avg(rs) -> float:
        return sum(r.confidence_contribution for r in rs) / len(rs) if rs else 0.0

    critical_avg = _avg(critical)
    advisory_avg = _avg(advisory)
    if critical and advisory:
        strength = critical_avg * _CRITICAL_QUALITY_WEIGHT + advisory_avg * _ADVISORY_QUALITY_WEIGHT
    elif critical:
        strength = critical_avg
    else:
        strength = advisory_avg
    strength = round(_clamp(strength, 0.0, 1.0), 3)

    inconclusive = [r for r in results if r.status is verification_model.VerificationStatus.INCONCLUSIVE]
    inconclusive_ratio = round(len(inconclusive) / len(results), 3)
    weak_signals = tuple(r.reason for r in inconclusive)

    passed = verification_bundle.overall_passed
    if passed and inconclusive:
        explanation = (
            f"verification passed, but {len(inconclusive)}/{len(results)} verifier(s) had "
            f"inconclusive/weak supporting evidence"
        )
    elif passed:
        explanation = "verification passed with decisive supporting evidence from every verifier"
    else:
        failed_reasons = [r.reason for r in results if r.critical and r.status is verification_model.VerificationStatus.FAILED]
        explanation = "verification did not pass: " + "; ".join(failed_reasons) if failed_reasons else "verification did not pass"

    return VerificationQuality(
        passed=passed,
        strength=strength,
        inconclusive_ratio=inconclusive_ratio,
        weak_signals=weak_signals,
        explanation=explanation,
    )


# ---------------------------------------------------------------------------
# Risk Model (never blocks -- explains uncertainty)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RiskIndicator:
    """One deterministic, evidence-backed risk observation. `evidence` is
    always the actual explanation string(s) already produced by an
    upstream feature/verifier/provider-agreement record -- never a
    fabricated justification."""

    code: str        # e.g. "conflicting_providers", "weak_brand_match", "no_website"
    severity: str      # "low" | "medium" | "high"
    description: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "description": self.description,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class RiskAssessment:
    """The complete set of risk indicators for one VerifiedDigitalIdentity
    plus a rolled-up `risk_level`. Purely diagnostic -- see module
    docstring's "never fabricated, never blocking" section. An empty
    `indicators` tuple (risk_level="none") is itself meaningful: a clean
    bill, not an omission.
    """

    indicators: Tuple[RiskIndicator, ...]
    risk_level: str  # "none" | "low" | "medium" | "high"

    def to_dict(self) -> Dict[str, Any]:
        return {"risk_level": self.risk_level, "indicators": [i.to_dict() for i in self.indicators]}


def assess_risk(
    provider_agreement: "identity_model.ProviderAgreementRecord",
    feature_set: Optional["features_model.FeatureSet"],
    verification_bundle: Optional["verification_model.VerificationBundle"],
    completeness: IdentityCompleteness,
    verification_quality: VerificationQuality,
    selected_group: Optional["identity_model.WebsiteCandidateGroup"],
) -> RiskAssessment:
    """Pure function covering every example risk the Sprint 2C brief
    names (Conflicting Providers, Weak Brand Match, Weak Location Match,
    Thin Evidence, Incomplete Identity, No Website, Low Verification
    Confidence). Each indicator only appears when the underlying,
    already-computed signal genuinely supports it -- nothing here invents
    a risk that isn't backed by real data, and nothing here changes
    `selected_group`/`verification_bundle`/anything the rest of the
    pipeline already decided.
    """
    indicators: List[RiskIndicator] = []

    if provider_agreement.status == "disagreement":
        indicators.append(
            RiskIndicator(
                code="conflicting_providers",
                severity="medium",
                description="independent providers proposed different websites for this business",
                evidence=(provider_agreement.explain(),),
            )
        )

    if selected_group is None:
        indicators.append(
            RiskIndicator(
                code="no_website",
                severity="high",
                description="no verified website could be selected for this business",
                evidence=(),
            )
        )

    if feature_set is not None:
        brand = feature_set.by_id(features_model.FeatureId.BRAND_SIMILARITY)
        if brand is not None and brand.normalized_value is not None and brand.normalized_value < _WEAK_BRAND_MATCH_THRESHOLD:
            severity = "medium" if brand.normalized_value == 0.0 else "low"
            indicators.append(
                RiskIndicator(
                    code="weak_brand_match", severity=severity, description=brand.explanation, evidence=(brand.explanation,)
                )
            )
        location = feature_set.by_id(features_model.FeatureId.LOCATION_CONSISTENCY)
        if (
            location is not None
            and location.normalized_value is not None
            and location.normalized_value < _WEAK_LOCATION_MATCH_THRESHOLD
        ):
            severity = "medium" if location.normalized_value == 0.0 else "low"
            indicators.append(
                RiskIndicator(
                    code="weak_location_match",
                    severity=severity,
                    description=location.explanation,
                    evidence=(location.explanation,),
                )
            )
        density = feature_set.by_id(features_model.FeatureId.EVIDENCE_DENSITY)
        if density is not None and density.normalized_value is not None and density.normalized_value < _THIN_EVIDENCE_DENSITY_THRESHOLD:
            indicators.append(
                RiskIndicator(
                    code="thin_evidence", severity="medium", description=density.explanation, evidence=(density.explanation,)
                )
            )

    if completeness.overall_score < _INCOMPLETE_IDENTITY_THRESHOLD:
        indicators.append(
            RiskIndicator(
                code="incomplete_identity",
                severity="high" if completeness.overall_score < _INCOMPLETE_IDENTITY_THRESHOLD / 2 else "medium",
                description=f"identity completeness score is {completeness.overall_score:.2f}, below the {_INCOMPLETE_IDENTITY_THRESHOLD:.2f} threshold",
                evidence=tuple(completeness.missing_information),
            )
        )

    if verification_bundle is None or not verification_bundle.overall_passed:
        critical_failures = (
            tuple(r.reason for r in verification_bundle.results if r.critical and r.status is verification_model.VerificationStatus.FAILED)
            if verification_bundle is not None
            else ()
        )
        indicators.append(
            RiskIndicator(
                code="low_verification_confidence",
                severity="high",
                description=("verification did not pass" if verification_bundle is not None else "no verification was performed"),
                evidence=critical_failures,
            )
        )
    elif verification_quality.strength < _LOW_VERIFICATION_STRENGTH_THRESHOLD:
        indicators.append(
            RiskIndicator(
                code="low_verification_confidence",
                severity="low",
                description=f"verification passed but with weak supporting strength ({verification_quality.strength:.2f})",
                evidence=verification_quality.weak_signals,
            )
        )

    if not indicators:
        risk_level = "none"
    else:
        severities = {i.severity for i in indicators}
        risk_level = "high" if "high" in severities else ("medium" if "medium" in severities else "low")

    return RiskAssessment(indicators=tuple(indicators), risk_level=risk_level)


# ---------------------------------------------------------------------------
# Consensus Engine
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsensusRecord:
    """Extends provider agreement (ProviderAgreementRecord, Sprint 2A)
    into full consensus, per the brief: "how strongly do ALL observations
    support this digital identity" -- providers, verification, features,
    identity, and confidence, each as their own dimension so a caller can
    see exactly which kind of agreement is (or isn't) present, not just
    one blended number.
    """

    provider_consensus: float      # independent providers converging on the same domain
    verification_consensus: float   # verifiers agreeing the candidate is legitimate
    feature_consensus: float        # fraction of extractable features showing a positive signal
    identity_consensus: float       # the identity-verification signal specifically
    confidence_consensus: float     # structural agreement across the confidence-propagation stages
    overall_consensus: float        # unweighted mean of the five dimensions above
    level: str                      # "none" | "weak" | "moderate" | "strong"
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider_consensus": self.provider_consensus,
            "verification_consensus": self.verification_consensus,
            "feature_consensus": self.feature_consensus,
            "identity_consensus": self.identity_consensus,
            "confidence_consensus": self.confidence_consensus,
            "overall_consensus": self.overall_consensus,
            "level": self.level,
            "explanation": self.explanation,
        }


def empty_consensus_record(reason: str = "no candidate was selected") -> ConsensusRecord:
    return ConsensusRecord(
        provider_consensus=0.0,
        verification_consensus=0.0,
        feature_consensus=0.0,
        identity_consensus=0.0,
        confidence_consensus=0.0,
        overall_consensus=0.0,
        level="none",
        explanation=reason,
    )


class ConsensusEngine:
    """Stateless, pure, exactly like ConfidencePropagationEngine/
    VerificationPipeline before it. `compute()` reads only its arguments
    -- no I/O, no randomness."""

    def compute(
        self,
        provider_agreement: "identity_model.ProviderAgreementRecord",
        verification_bundle: Optional["verification_model.VerificationBundle"],
        feature_set: Optional["features_model.FeatureSet"],
        confidence_trace: "confidence_model.ConfidenceTrace",
    ) -> ConsensusRecord:
        provider_consensus = self._provider_consensus(provider_agreement)
        verification_consensus = self._verification_consensus(verification_bundle)
        feature_consensus = self._feature_consensus(feature_set)
        identity_consensus = self._identity_consensus(verification_bundle)
        confidence_consensus = self._confidence_consensus(confidence_trace)

        components = [provider_consensus, verification_consensus, feature_consensus, identity_consensus, confidence_consensus]
        overall = round(sum(components) / len(components), 3)
        level = _bucket(overall)

        parts = []
        if provider_agreement.status == "agreement":
            parts.append("providers agree")
        elif provider_agreement.status == "disagreement":
            parts.append("providers disagree")
        if verification_bundle is not None and verification_bundle.overall_passed:
            parts.append("verification passed")
        explanation = "; ".join(parts) if parts else "insufficient data to establish consensus"

        return ConsensusRecord(
            provider_consensus=provider_consensus,
            verification_consensus=verification_consensus,
            feature_consensus=feature_consensus,
            identity_consensus=identity_consensus,
            confidence_consensus=confidence_consensus,
            overall_consensus=overall,
            level=level,
            explanation=explanation,
        )

    @staticmethod
    def _provider_consensus(record: "identity_model.ProviderAgreementRecord") -> float:
        if record.status in ("no_candidates", "disagreement"):
            return 0.0
        if record.status == "single_source":
            # Half credit: real signal (one provider found something) but
            # not yet independently corroborated -- mirrors the same
            # full/half convention evidence.py's LOCATION_MATCH_WEAK_WEIGHT
            # already uses for an unconfirmed-but-real signal.
            return 0.5
        distinct_providers = set(record.agreeing_providers)
        for d in record.disagreements:
            distinct_providers.add(d["provider"])
        total = len(distinct_providers)
        return round(len(record.agreeing_providers) / total, 3) if total else 0.0

    @staticmethod
    def _verification_consensus(bundle: Optional["verification_model.VerificationBundle"]) -> float:
        if bundle is None or not bundle.results:
            return 0.0
        total = len(bundle.results)
        passed = sum(1 for r in bundle.results if r.status is verification_model.VerificationStatus.PASSED)
        inconclusive = sum(1 for r in bundle.results if r.status is verification_model.VerificationStatus.INCONCLUSIVE)
        # INCONCLUSIVE is neither agreement nor disagreement -- counted at
        # half weight so missing data (e.g. WEBSITE_STRUCTURE, still
        # reserved) never reads as either full support or full
        # contradiction.
        return round((passed + 0.5 * inconclusive) / total, 3)

    @staticmethod
    def _feature_consensus(feature_set: Optional["features_model.FeatureSet"]) -> float:
        if feature_set is None:
            return 0.0
        measurable = [f for f in feature_set.features if f.normalized_value is not None]
        if not measurable:
            return 0.0
        positive = sum(1 for f in measurable if f.normalized_value > 0.0)
        return round(positive / len(measurable), 3)

    @staticmethod
    def _identity_consensus(bundle: Optional["verification_model.VerificationBundle"]) -> float:
        if bundle is None:
            return 0.0
        result = bundle.by_verifier("identity_verification")
        if result is None:
            return 0.0
        if result.status is verification_model.VerificationStatus.PASSED:
            return round(_clamp(result.confidence_contribution, 0.0, 1.0), 3)
        return 0.0

    @staticmethod
    def _confidence_consensus(trace: "confidence_model.ConfidenceTrace") -> float:
        pre_final = trace.stages[:-1] if len(trace.stages) > 1 else trace.stages
        if not pre_final:
            return 0.0
        agreeing = sum(1 for stage in pre_final if len(stage.contributed) > len(stage.reduced))
        return round(agreeing / len(pre_final), 3)


# ---------------------------------------------------------------------------
# Digital Identity Quality (reusable, generic quality rollup)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DigitalIdentityQuality:
    """The reusable quality assessment the brief asks for: Identity
    Completeness, Verification Strength, Evidence Density, Provider
    Consensus, Verification Coverage, Risk Level, and Missing Signals,
    combined into one `overall_score`. Generic by construction -- every
    input is itself already category-agnostic (see each assessor's own
    docstring), so this rollup never branches on business type either.
    """

    identity_completeness: float
    verification_strength: float
    evidence_density: float
    provider_consensus: float
    verification_coverage: float
    risk_level: str
    missing_signals: Tuple[str, ...]
    overall_score: float
    # Sprint 3 additions -- defaulted so every pre-Sprint-3 construction
    # site (direct or via assess_digital_identity_quality's own,
    # similarly-defaulted new parameter) keeps working unchanged. See
    # digital_identity.py's own "Sprint 3: Identity Resolution Engine"
    # docstring section and identity_resolution_engine.py for how these
    # are actually computed.
    identity_stability: float = 0.0     # from IdentityResolutionResult.identity_stability
    evidence_diversity: float = 0.0     # from IdentityResolutionResult.evidence_diversity (distinct evidence TYPES, not raw item count)
    conflict_score: float = 0.0         # from IdentityResolutionResult.conflict_assessment.conflict_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "identity_completeness": self.identity_completeness,
            "verification_strength": self.verification_strength,
            "evidence_density": self.evidence_density,
            "provider_consensus": self.provider_consensus,
            "verification_coverage": self.verification_coverage,
            "risk_level": self.risk_level,
            "missing_signals": list(self.missing_signals),
            "overall_score": self.overall_score,
            "identity_stability": self.identity_stability,
            "evidence_diversity": self.evidence_diversity,
            "conflict_score": self.conflict_score,
        }


def assess_digital_identity_quality(
    completeness: IdentityCompleteness,
    verification_quality: VerificationQuality,
    consensus: ConsensusRecord,
    risk: RiskAssessment,
    feature_set: Optional["features_model.FeatureSet"],
    identity_resolution: Optional["identity_resolution_model.IdentityResolutionResult"] = None,
) -> DigitalIdentityQuality:
    evidence_density = 0.0
    if feature_set is not None:
        density = feature_set.by_id(features_model.FeatureId.EVIDENCE_DENSITY)
        if density is not None and density.normalized_value is not None:
            evidence_density = density.normalized_value

    components = [
        completeness.overall_score,
        verification_quality.strength,
        evidence_density,
        consensus.provider_consensus,
        completeness.verification_coverage,
    ]
    base_score = sum(components) / len(components)

    # A high (or medium) risk level must visibly dampen the top-line
    # score -- otherwise risk would be reported next to the score without
    # ever actually affecting it, which defeats the point of surfacing it
    # at all. This is the ONLY place risk touches a number in this
    # module, and it only ever lowers this one rollup score -- it still
    # never touches BusinessIdentity/VerificationBundle/ConfidenceTrace.
    if risk.risk_level == "high":
        overall = min(base_score, _HIGH_RISK_QUALITY_CAP)
    elif risk.risk_level == "medium":
        overall = min(base_score, _MEDIUM_RISK_QUALITY_CAP)
    else:
        overall = base_score

    missing_signals = _dedupe(list(completeness.missing_information) + list(verification_quality.weak_signals))

    identity_stability = identity_resolution.identity_stability if identity_resolution is not None else 0.0
    evidence_diversity = identity_resolution.evidence_diversity if identity_resolution is not None else 0.0
    conflict_score = (
        identity_resolution.conflict_assessment.conflict_score if identity_resolution is not None else 0.0
    )

    return DigitalIdentityQuality(
        identity_completeness=completeness.overall_score,
        verification_strength=verification_quality.strength,
        evidence_density=evidence_density,
        provider_consensus=consensus.provider_consensus,
        verification_coverage=completeness.verification_coverage,
        risk_level=risk.risk_level,
        missing_signals=missing_signals,
        overall_score=round(_clamp(overall, 0.0, 1.0), 3),
        identity_stability=identity_stability,
        evidence_diversity=evidence_diversity,
        conflict_score=conflict_score,
    )


# ---------------------------------------------------------------------------
# Verified Digital Identity (the canonical object)
# ---------------------------------------------------------------------------


@dataclass
class VerifiedDigitalIdentity:
    """The canonical, internal-only Discovery result. INTERNAL ONLY --
    never place an instance of this on application.discovery.dto or any
    provider interface, exactly like BusinessIdentity before it (see
    identity.py's own docstring for that rule; it applies here
    identically, one layer up).

    This is what a future Sprint 3 Scraper / Company Intelligence / AI /
    Enrichment stage should consume instead of raw provider output or a
    bare WebsiteResolution -- everything a downstream stage would need to
    decide how much to trust this business's identity is here, in one
    place, with an explicit quality/risk assessment rather than a single
    opaque confidence float.
    """

    business_identity: "identity_model.BusinessIdentity"
    selected_website: Optional[str]
    consensus: ConsensusRecord
    verification_trace: VerificationTrace
    confidence_trace: "confidence_model.ConfidenceTrace"
    feature_store: "features_model.FeatureStore"
    verification_bundle: Optional["verification_model.VerificationBundle"]
    evidence_bundle: "evidence_model.EvidenceBundle"
    identity_completeness: IdentityCompleteness
    verification_quality: VerificationQuality
    risk_assessment: RiskAssessment
    quality: DigitalIdentityQuality
    missing_evidence: Tuple[str, ...]
    status: DigitalIdentityStatus
    verified_at: float = field(default_factory=time.time)
    # Sprint 3 -- defaulted (see this module's own "Sprint 3: Identity
    # Resolution Engine" docstring section) so every pre-Sprint-3
    # construction site keeps working unchanged. `None` only when no
    # candidate was ever ever proposed for this business (or when a
    # caller builds a VerifiedDigitalIdentity by hand without running the
    # engine) -- every real DigitalIdentityBuilder.build() call populates
    # this via `identity_resolution_engine.empty_identity_resolution_result`
    # at worst, never leaves it fabricated.
    identity_resolution: Optional["identity_resolution_model.IdentityResolutionResult"] = None

    # -- Sprint 3 convenience properties -- single source of truth, never
    # duplicated storage: each of these simply reads the corresponding
    # field off `self.identity_resolution`, falling back to an honest,
    # zero/empty default when it's absent (e.g. a hand-built instance
    # from before Sprint 3) rather than raising.

    @property
    def selection_confidence(self) -> float:
        return self.identity_resolution.selection_confidence if self.identity_resolution else self.confidence_trace.final

    @property
    def selection_reason(self) -> str:
        return self.identity_resolution.selection_reason if self.identity_resolution else "no identity resolution data available"

    @property
    def alternative_candidates(self) -> Tuple[Any, ...]:
        return self.identity_resolution.alternative_candidates if self.identity_resolution else ()

    @property
    def rejected_candidate_reasons(self) -> Tuple[Any, ...]:
        return self.identity_resolution.rejected_candidate_reasons if self.identity_resolution else ()

    def explain(self) -> List[str]:
        lines = [
            f"status={self.status.value}",
            f"selected_website={self.selected_website}",
            f"quality={self.quality.overall_score:.3f}",
            f"risk_level={self.risk_assessment.risk_level}",
            f"consensus={self.consensus.overall_consensus:.3f} ({self.consensus.level})",
            self.business_identity.decision_trace.explain(),
        ]
        if self.risk_assessment.indicators:
            lines.append("Risks: " + "; ".join(f"{i.code} ({i.severity})" for i in self.risk_assessment.indicators))
        if self.missing_evidence:
            lines.append("Missing: " + "; ".join(self.missing_evidence))
        # Sprint 4, goal #6: append (never replace or reorder) the
        # Identity Resolution Engine's own explanation -- see
        # identity_resolution_engine.IdentityResolutionResult.explain().
        if self.identity_resolution is not None:
            lines.extend(self.identity_resolution.explain())
        return lines

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "selected_website": self.selected_website,
            "business_identity": self.business_identity.to_dict(),
            "consensus": self.consensus.to_dict(),
            "verification_trace": self.verification_trace.to_dict(),
            "confidence_trace": self.confidence_trace.to_dict(),
            "feature_store": self.feature_store.to_dict(),
            "verification_bundle": self.verification_bundle.to_dict() if self.verification_bundle else None,
            "evidence_bundle": self.evidence_bundle.to_dict(),
            "identity_completeness": self.identity_completeness.to_dict(),
            "verification_quality": self.verification_quality.to_dict(),
            "risk_assessment": self.risk_assessment.to_dict(),
            "quality": self.quality.to_dict(),
            "missing_evidence": list(self.missing_evidence),
            "verified_at": self.verified_at,
            "identity_resolution": self.identity_resolution.to_dict() if self.identity_resolution else None,
            "explanation": self.explain(),
        }


# ---------------------------------------------------------------------------
# Digital Identity Builder (assembles the canonical object)
# ---------------------------------------------------------------------------


class DigitalIdentityBuilder:
    """Builds one VerifiedDigitalIdentity from an already-fully-computed
    BusinessIdentity -- no re-resolution, no new provider/HTTP calls, no
    re-extraction of Features (`FeatureStore` is reused as-is). The only
    recomputation performed here is re-running the (pure, stateless,
    cheap) VerificationPipeline for candidate groups OTHER than the
    selected one, to build the full `VerificationTrace` -- the selected
    group's own bundle is reused as-is from `BusinessIdentity.decision_
    trace.verification_bundle`, never recomputed.

    DI note: pass the same `VerificationPipeline` instance your
    IdentityResolver is configured with (if any) so VerificationTrace
    entries for rejected candidates use the same verifier set as the
    selected one. WebsiteResolver's default wiring does this
    automatically (see its own docstring); a bespoke IdentityResolver
    that doesn't expose `.verification_pipeline` degrades gracefully to
    a default `VerificationPipeline()` for those entries only -- this can
    never affect the selected candidate's own (reused) bundle, so it
    never changes BusinessIdentity's own output, only the diagnostic
    detail available for candidates that were already rejected.
    """

    def __init__(
        self,
        verification_pipeline: Optional["verification_model.VerificationPipeline"] = None,
        consensus_engine: Optional[ConsensusEngine] = None,
        identity_resolution_engine: Optional["identity_resolution_model.IdentityResolutionEngine"] = None,
    ):
        self.verification_pipeline = verification_pipeline or verification_model.VerificationPipeline()
        self.consensus_engine = consensus_engine or ConsensusEngine()
        # Sprint 3: same defaulted, constructor-injectable DI pattern as
        # the two collaborators above. Unlike them, the default engine is
        # STATEFUL across calls to this same DigitalIdentityBuilder
        # instance (see identity_resolution_engine.IdentityResolutionEngine's
        # own docstring for why, and WebsiteResolver's docstring for the
        # scope this naturally lands at: one discovery run's worth of
        # businesses, with zero changes needed to WebsiteResolver or
        # DiscoveryService).
        self.identity_resolution_engine = identity_resolution_engine or identity_resolution_model.IdentityResolutionEngine()

    def build(
        self, business_identity: "identity_model.BusinessIdentity", business: BusinessCandidate
    ) -> VerifiedDigitalIdentity:
        groups = business_identity.website_candidates
        decision_trace = business_identity.decision_trace
        selected_domain = decision_trace.selected_domain
        selected_group = next((g for g in groups if g.domain == selected_domain), None) if selected_domain else None
        selected_feature_set = business_identity.feature_store.get(selected_domain) if selected_domain else None
        selected_verification = decision_trace.verification_bundle

        bundles: List[Tuple[str, Optional["verification_model.VerificationBundle"]]] = []
        for group in groups:
            if group.domain == selected_domain and selected_verification is not None:
                bundles.append((group.domain, selected_verification))
                continue
            feature_set = business_identity.feature_store.get(group.domain)
            bundles.append((group.domain, self.verification_pipeline.run(feature_set) if feature_set is not None else None))
        verification_trace = VerificationTrace(selected_domain=selected_domain, bundles=tuple(bundles))

        verification_quality = assess_verification_quality(selected_verification)
        completeness = assess_identity_completeness(
            business=business,
            selected_group=selected_group,
            groups=groups,
            feature_set=selected_feature_set,
            verification_bundle=selected_verification,
            confidence_trace=decision_trace.confidence_trace,
        )
        consensus = (
            self.consensus_engine.compute(
                provider_agreement=decision_trace.provider_agreement,
                verification_bundle=selected_verification,
                feature_set=selected_feature_set,
                confidence_trace=decision_trace.confidence_trace,
            )
            if groups
            else empty_consensus_record("no candidates were ever proposed")
        )
        risk = assess_risk(
            provider_agreement=decision_trace.provider_agreement,
            feature_set=selected_feature_set,
            verification_bundle=selected_verification,
            completeness=completeness,
            verification_quality=verification_quality,
            selected_group=selected_group,
        )

        # Sprint 3: Candidate Consolidation, Competition, Provider
        # Reliability, Conflict Resolution and False Positive Reduction --
        # reads `business_identity`/`business`/`verification_trace`
        # (already built above), performs no new I/O, and never changes
        # `business_identity.selected_website`/`decision_trace`/anything
        # computed above this line -- see identity_resolution_engine.py's
        # own docstring.
        identity_resolution = self.identity_resolution_engine.resolve(
            business_identity, business, verification_trace, consensus=consensus
        )

        quality = assess_digital_identity_quality(
            completeness, verification_quality, consensus, risk, selected_feature_set, identity_resolution
        )

        missing_evidence = _dedupe(list(decision_trace.missing_evidence) + list(completeness.missing_information))
        status = _derive_status(business_identity.verification_state, quality, risk)

        return VerifiedDigitalIdentity(
            business_identity=business_identity,
            selected_website=business_identity.selected_website,
            consensus=consensus,
            verification_trace=verification_trace,
            confidence_trace=decision_trace.confidence_trace,
            feature_store=business_identity.feature_store,
            verification_bundle=selected_verification,
            evidence_bundle=business_identity.evidence_bundle,
            identity_completeness=completeness,
            verification_quality=verification_quality,
            risk_assessment=risk,
            quality=quality,
            missing_evidence=missing_evidence,
            status=status,
            verified_at=time.time(),
            identity_resolution=identity_resolution,
        )
