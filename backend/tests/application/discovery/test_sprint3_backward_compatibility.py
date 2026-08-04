"""
Sprint 3 backward-compatibility / layering-discipline regression tests.

Mirrors the pattern every prior sprint's own test suite already
established (see test_identity.py's `test_identity_module_does_not_
import_digital_identity`, test_website_resolver_evidence.py's constructor
-argument checks): source-level guarantees that Sprint 3 extended the
pipeline strictly additively, without redesigning or bypassing any
existing layer.
"""

import inspect
import re

from application.discovery import identity as identity_model
from application.discovery import website_resolver as website_resolver_model
from application.discovery.digital_identity import (
    DigitalIdentityBuilder,
    DigitalIdentityQuality,
    assess_digital_identity_quality,
    assess_identity_completeness,
    assess_risk,
    assess_verification_quality,
    empty_consensus_record,
)
from application.discovery.confidence import empty_confidence_trace
from application.discovery.dto import BusinessCandidate
from application.discovery.evidence import CandidatePool


# ---------------------------------------------------------------------------
# Layering discipline -- Sprint 3 modules never leak upstream
# ---------------------------------------------------------------------------


_SPRINT3_MODULES = (
    "organization", "competition", "false_positive", "reliability",
    "canonicalization", "identity_resolution_engine",
)


def _imports(source: str, module_name: str) -> bool:
    """True only for an actual import statement referencing
    `application.discovery.<module_name>` -- not any incidental
    substring match against unrelated prose (identity.py's own docstring
    happens to contain the word "organization" inside
    "organization-metadata similarity", which is not an import)."""
    pattern = rf"^\s*(from\s+application\.discovery\s+import\s+.*\b{module_name}\b|import\s+application\.discovery\.{module_name}\b)"
    return re.search(pattern, source, flags=re.MULTILINE) is not None


def test_identity_module_still_does_not_import_any_sprint3_module():
    """identity.py (Sprint 2A) must remain completely unaware that Sprint
    3 exists -- exactly the same guarantee test_identity.py already
    checks for digital_identity.py, extended to every new Sprint 3
    module. A source-level check, not just "it happened to work"."""
    source = inspect.getsource(identity_model)
    for forbidden in _SPRINT3_MODULES + ("digital_identity",):
        assert not _imports(source, forbidden), f"identity.py must not import {forbidden}"


def test_website_resolver_module_still_does_not_import_sprint3_modules_directly():
    """WebsiteResolver only ever talks to digital_identity.py (via
    DigitalIdentityBuilder, already the case since Sprint 2C) -- it has
    no direct dependency on any new Sprint 3 module, so Sprint 3 could
    not have needed to touch website_resolver.py at all."""
    source = inspect.getsource(website_resolver_model)
    for forbidden in _SPRINT3_MODULES:
        assert not _imports(source, forbidden), f"website_resolver.py must not import {forbidden}"


def test_website_resolver_constructor_gained_no_new_parameters_in_sprint_3():
    """WebsiteResolver.__init__'s own parameter list is byte-for-byte
    identical to what Sprint 2C left it -- Sprint 3's new engine is wired
    in entirely through DigitalIdentityBuilder's own (already Sprint
    -2C-extensible) constructor, one layer downstream."""
    params = list(inspect.signature(website_resolver_model.WebsiteResolver.__init__).parameters)
    assert params == [
        "self", "validator", "fallback_provider", "verification_engine",
        "identity_resolver", "digital_identity_builder",
    ]


def test_generic_identity_resolver_constructor_unchanged_in_sprint_3():
    params = list(inspect.signature(identity_model.GenericIdentityResolver.__init__).parameters)
    assert params == ["self", "feature_engine", "verification_pipeline", "confidence_engine"]


# ---------------------------------------------------------------------------
# DigitalIdentityBuilder / VerifiedDigitalIdentity additive-only changes
# ---------------------------------------------------------------------------


def test_digital_identity_builder_new_parameter_is_defaulted():
    params = inspect.signature(DigitalIdentityBuilder.__init__).parameters
    assert "identity_resolution_engine" in params
    assert params["identity_resolution_engine"].default is None


def test_assess_digital_identity_quality_still_works_with_original_five_arguments():
    """The exact call test_digital_identity.py's own
    test_quality_high_risk_caps_the_overall_score makes, with no
    identity_resolution argument at all -- must keep working unchanged,
    and the three new dimensions must default to 0.0 rather than error."""
    completeness = assess_identity_completeness(
        business=BusinessCandidate(name="X"),
        selected_group=None, groups=[], feature_set=None, verification_bundle=None,
        confidence_trace=empty_confidence_trace(),
    )
    verification_quality = assess_verification_quality(None)
    consensus = empty_consensus_record()
    risk = assess_risk(
        provider_agreement=identity_model.compute_provider_agreement(CandidatePool("X", "Y")),
        feature_set=None, verification_bundle=None, completeness=completeness,
        verification_quality=verification_quality, selected_group=None,
    )
    quality = assess_digital_identity_quality(completeness, verification_quality, consensus, risk, None)
    assert quality.identity_stability == 0.0
    assert quality.evidence_diversity == 0.0
    assert quality.conflict_score == 0.0


def test_digital_identity_quality_can_still_be_constructed_with_only_original_fields():
    """The exact 8-field construction that was valid before Sprint 3
    still works, with the 3 new fields defaulting rather than becoming
    required."""
    quality = DigitalIdentityQuality(
        identity_completeness=0.5, verification_strength=0.5, evidence_density=0.5,
        provider_consensus=0.5, verification_coverage=0.5, risk_level="none",
        missing_signals=(), overall_score=0.5,
    )
    assert quality.identity_stability == 0.0
    assert quality.evidence_diversity == 0.0
    assert quality.conflict_score == 0.0
    assert set(quality.to_dict().keys()) == {
        "identity_completeness", "verification_strength", "evidence_density", "provider_consensus",
        "verification_coverage", "risk_level", "missing_signals", "overall_score",
        "identity_stability", "evidence_diversity", "conflict_score",
    }
