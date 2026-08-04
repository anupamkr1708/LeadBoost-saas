"""
Verification Engine (Sprint 2B).

Replaces the *identity*-verification half of Sprint 2A's
`identity.verify_identity_features` -- not Sprint 1's
`evidence.VerificationEngine` (that engine turns a ValidationOutcome into
Evidence; it stays exactly as-is, still the RAW DATA -> EVIDENCE step, and
is not what this module's brief means by "verification"). This module is
the Sprint 2B "Verification" stage: FeatureSet -> VerificationBundle.

Strict layering, enforced structurally, not just by convention: this
module imports only `application.discovery.features` (for FeatureId/
FeatureSet) plus the standard library. It does not import
`application.discovery.evidence` or `application.discovery.identity` --
a Verifier physically cannot inspect a ValidationOutcome, a raw provider
payload, or an EvidenceBundle, because this module never even imports
their types. "Verification should operate only on Features. Never
inspect provider output directly" is therefore not just a rule Verifiers
are asked to follow -- it's the only thing they're able to do.

Strategy pattern, extensible without modification (per the brief's
"Extensibility" section): `Verifier` is the interface, each concrete
verifier is an independent, single-purpose strategy, and
`VerificationPipeline` runs a *list* of them, defaulted but overridable
via the constructor, with `register()` for adding more later. A future
Sprint 3 verifier is `pipeline.register(NewVerifier())` -- no existing
verifier's code changes.

No verifier makes the final decision (per the brief): each Verifier
returns its own opinion (status/confidence_contribution/reason/
supporting_features) about one narrow question, and `VerificationBundle`
is just the collected set of those opinions. Rolling that bundle up into
one pass/fail verdict is `VerificationBundle.overall_passed` -- a simple,
transparent "did every *critical* verifier pass" rule, computed from
whatever verifiers actually ran (each result carries its own producing
verifier's `critical` flag, so a custom or Sprint-3-registered pipeline
is rolled up correctly too, never against a stale hardcoded set).
Combining verification into a single confidence number is
`application.discovery.confidence`'s job, not any individual verifier's.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from application.discovery.features import FeatureId, FeatureSet


class VerificationStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    # Distinct from FAILED: there wasn't enough underlying feature data to
    # decide either way (e.g. WEBSITE_STRUCTURE is still reserved in
    # Sprint 2B) -- never silently reported as a pass or a fail when the
    # honest answer is "unknown".
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class VerificationResult:
    """One verifier's contribution -- an opinion, not a decision. Mirrors
    the shape the brief asks for: status, confidence contribution,
    reason, and the specific features that produced it. `critical` is
    copied from the verifier that produced this result (via
    `Verifier._result`, never set ad hoc) so `VerificationBundle.
    overall_passed` always reflects whichever verifiers actually ran --
    including a custom or Sprint-3-registered set -- never a fixed,
    possibly-stale default."""

    verifier_id: str
    status: VerificationStatus
    confidence_contribution: float
    reason: str
    supporting_features: Tuple[FeatureId, ...]
    critical: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verifier_id": self.verifier_id,
            "status": self.status.value,
            "confidence_contribution": self.confidence_contribution,
            "reason": self.reason,
            "supporting_features": [f.value for f in self.supporting_features],
            "critical": self.critical,
        }


class Verifier(ABC):
    """One independent verification stage. `critical=True` means a FAILED
    result from this verifier blocks `VerificationBundle.overall_passed`
    -- everything else is advisory: it still contributes a confidence
    number and an explanation, it just never gates the pass/fail rollup
    on its own (e.g. WebsiteVerifier is almost always INCONCLUSIVE today,
    since website-structure data doesn't exist yet; it must not be able
    to block verification for that reason)."""

    id: str
    critical: bool = False

    @abstractmethod
    def verify(self, features: FeatureSet) -> VerificationResult:
        """Must read only `features` -- no other argument, no I/O, no
        access to anything features.py didn't already put in the
        FeatureSet."""

    def _result(
        self,
        status: VerificationStatus,
        confidence_contribution: float,
        reason: str,
        supporting_features: Tuple[FeatureId, ...],
    ) -> VerificationResult:
        """Every concrete verifier builds its result through here, not by
        constructing VerificationResult directly -- the one place
        `verifier_id`/`critical` are filled in, so they can never drift
        out of sync with `self.id`/`self.critical`."""
        return VerificationResult(
            verifier_id=self.id,
            status=status,
            confidence_contribution=confidence_contribution,
            reason=reason,
            supporting_features=supporting_features,
            critical=self.critical,
        )


def _feature_value(features: FeatureSet, feature_id: FeatureId) -> Optional[float]:
    feature = features.by_id(feature_id)
    return feature.normalized_value if feature else None


class DomainVerifier(Verifier):
    """"Is this a legitimate, reachable domain?" Critical: a candidate
    that never passed a reachability check cannot be considered a
    verified identity, no matter how well its name matches."""

    id = "domain_verification"
    critical = True

    def verify(self, features: FeatureSet) -> VerificationResult:
        reachability = _feature_value(features, FeatureId.REACHABILITY)
        quality = _feature_value(features, FeatureId.DOMAIN_QUALITY)
        supporting = (FeatureId.REACHABILITY, FeatureId.HTTPS, FeatureId.CANONICAL_DOMAIN, FeatureId.DOMAIN_QUALITY)
        if reachability and reachability > 0.0:
            reason = (
                f"domain passed reachability with quality {quality:.2f}"
                if quality is not None
                else "domain passed reachability"
            )
            return self._result(VerificationStatus.PASSED, quality or 0.0, reason, supporting)
        return self._result(
            VerificationStatus.FAILED, 0.0, "domain did not pass a reachability check", supporting
        )


class BusinessVerifier(Verifier):
    """"Is the underlying business record itself well-formed?" Advisory:
    a thin business record (name only) shouldn't block verification of an
    otherwise well-matched, reachable site -- it should just lower
    confidence a little and say why."""

    id = "business_verification"
    critical = False

    def verify(self, features: FeatureSet) -> VerificationResult:
        completeness = _feature_value(features, FeatureId.BUSINESS_COMPLETENESS)
        supporting = (FeatureId.BUSINESS_COMPLETENESS, FeatureId.CONTACT_COMPLETENESS)
        if completeness and completeness >= 0.5:
            return self._result(
                VerificationStatus.PASSED, completeness, f"business record is {completeness:.0%} complete", supporting
            )
        return self._result(
            VerificationStatus.INCONCLUSIVE,
            completeness or 0.0,
            f"business record is only {(completeness or 0.0):.0%} complete",
            supporting,
        )


class LocationVerifier(Verifier):
    """"Does the queried location line up with this candidate?" Advisory:
    plenty of legitimately-matched businesses have no location signal at
    all (a candidate resolved purely by an exact brand-name match), so
    absence here must not fail verification outright."""

    id = "location_verification"
    critical = False

    def verify(self, features: FeatureSet) -> VerificationResult:
        location = _feature_value(features, FeatureId.LOCATION_CONSISTENCY)
        supporting = (FeatureId.LOCATION_CONSISTENCY,)
        if location and location > 0.0:
            return self._result(VerificationStatus.PASSED, location, "queried location is corroborated", supporting)
        return self._result(
            VerificationStatus.INCONCLUSIVE,
            0.0,
            "nothing available corroborates the queried location",
            supporting,
        )


class WebsiteVerifier(Verifier):
    """"Does the site's own structure corroborate the business?" Always
    INCONCLUSIVE today, honestly -- WEBSITE_STRUCTURE is still reserved
    (see features.py); this verifier exists so a future engine that does
    populate it (Sprint 2C+) only has to change what WEBSITE_STRUCTURE
    contains, not add a new verifier or touch any other verifier."""

    id = "website_verification"
    critical = False

    def verify(self, features: FeatureSet) -> VerificationResult:
        structure = features.by_id(FeatureId.WEBSITE_STRUCTURE)
        reason = structure.explanation if structure else "no website-structure data available"
        return self._result(VerificationStatus.INCONCLUSIVE, 0.0, reason, (FeatureId.WEBSITE_STRUCTURE,))


class IdentityVerifier(Verifier):
    """"Does the matched identity actually look like the business?"
    Critical, same as Sprint 2A's `identity.verify_identity_features` --
    formalized here as a proper strategy in the new pipeline. Same
    threshold logic (brand match, else location match, else cross
    -provider agreement, else fail) -- see identity.py's module docstring
    for why that function is kept as a separate, parallel implementation
    rather than delegating here."""

    id = "identity_verification"
    critical = True

    def verify(self, features: FeatureSet) -> VerificationResult:
        brand = _feature_value(features, FeatureId.BRAND_SIMILARITY) or 0.0
        location = _feature_value(features, FeatureId.LOCATION_CONSISTENCY) or 0.0
        agreement = _feature_value(features, FeatureId.PROVIDER_AGREEMENT) or 0.0
        supporting = (FeatureId.BRAND_SIMILARITY, FeatureId.LOCATION_CONSISTENCY, FeatureId.PROVIDER_AGREEMENT)

        if brand > 0.0:
            return self._result(
                VerificationStatus.PASSED,
                brand,
                f"business name matches the domain (strength {brand:.2f})",
                supporting,
            )
        if location > 0.0:
            return self._result(
                VerificationStatus.PASSED,
                location,
                "queried location is corroborated, even without a brand-name match",
                supporting,
            )
        if agreement > 0.0:
            return self._result(
                VerificationStatus.PASSED,
                agreement,
                "multiple independent providers converged on this candidate",
                supporting,
            )
        return self._result(
            VerificationStatus.FAILED,
            0.0,
            "no brand, location, or cross-provider signal supports this identity",
            supporting,
        )


@dataclass(frozen=True)
class VerificationBundle:
    """Every verifier's opinion about one FeatureSet, collected. Never a
    single verdict on its own -- `overall_passed` is a transparent rollup
    rule (every *critical* verifier passed), not a new, separate
    judgment."""

    results: Tuple[VerificationResult, ...]

    def by_verifier(self, verifier_id: str) -> Optional[VerificationResult]:
        return next((r for r in self.results if r.verifier_id == verifier_id), None)

    @property
    def overall_passed(self) -> bool:
        critical_results = [r for r in self.results if r.critical]
        return bool(critical_results) and all(r.status is VerificationStatus.PASSED for r in critical_results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_passed": self.overall_passed,
            "results": [r.to_dict() for r in self.results],
        }


def _default_verifiers() -> List[Verifier]:
    return [DomainVerifier(), BusinessVerifier(), LocationVerifier(), WebsiteVerifier(), IdentityVerifier()]


class VerificationPipeline:
    """Feature Set -> Verifier A -> Verifier B -> ... -> Verification
    Bundle. Verifiers are independent (each only ever reads `features`,
    never another verifier's result), so they can run in any order --
    listed here in a fixed order purely for deterministic, readable
    output."""

    def __init__(self, verifiers: Optional[List[Verifier]] = None):
        self.verifiers: List[Verifier] = verifiers if verifiers is not None else _default_verifiers()

    def register(self, verifier: Verifier) -> None:
        """Extension point for Sprint 3: register another verifier
        without modifying any existing one."""
        self.verifiers.append(verifier)

    def run(self, features: FeatureSet) -> VerificationBundle:
        return VerificationBundle(results=tuple(v.verify(features) for v in self.verifiers))
