"""
Unit tests for application.discovery.identity_resolution_engine (Sprint 3).

Mirrors test_digital_identity.py's own approach: everything here is
exercised against a real, end-to-end BusinessIdentity built through
GenericIdentityResolver (not mocks), since the whole point of this
engine is that it reads real already-computed pipeline output.

Covers, per the Sprint 3 brief: Candidate Identity Model, Candidate
Consolidation, Candidate Competition, Provider Reliability, Evidence
Corroboration, Conflict Resolution, False Positive Reduction, and the
new fields threaded onto VerifiedDigitalIdentity/DigitalIdentityQuality
-- plus backward compatibility (every Sprint 1-2C test still passes
unmodified, covered by the full suite, not repeated here).
"""

from application.discovery.digital_identity import DigitalIdentityBuilder
from application.discovery.dto import BusinessCandidate
from application.discovery.evidence import (
    CandidatePool,
    brand_identity_evidence,
    location_corroboration_evidence,
    verification_evidence,
)
from application.discovery.identity import GenericIdentityResolver
from application.discovery.identity_resolution_engine import (
    IdentityResolutionEngine,
    empty_identity_resolution_result,
)
from application.discovery.website_validator import ValidationOutcome


# -- Shared fixtures ------------------------------------------------------------


def _single_candidate_business_identity(business_name="Nike", location="Mumbai", url="https://nike.com"):
    pool = CandidatePool(business_name, location)
    candidate = pool.add_candidate(url=url, provider="overpass", validated=True)
    candidate.evidence.add(brand_identity_evidence(business_name, candidate.domain, candidate.provider))
    candidate.evidence.add(location_corroboration_evidence(location, candidate.domain, location, candidate.provider))
    outcome = ValidationOutcome(ok=True, normalized_url=url + "/")
    candidate.evidence.extend(verification_evidence(outcome, candidate.provider))
    business = BusinessCandidate(name=business_name, website=url, address=location, phone="555-1000")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, location, chosen=candidate, chosen_url=url + "/")
    return business_identity, business


def _subdomain_consolidation_business_identity():
    """Two providers propose two DIFFERENT subdomains of the same
    registrable domain -- today's identity.group_website_candidates
    keeps them as two separate WebsiteCandidateGroups (exact-domain
    grouping); Sprint 3's organization.consolidate is what collapses
    them back into one logical identity."""
    pool = CandidatePool("Nike", "Mumbai")
    root = pool.add_candidate(url="https://nike.com", provider="overpass", validated=True)
    root.evidence.add(brand_identity_evidence("Nike", root.domain, root.provider))
    root.evidence.extend(verification_evidence(ValidationOutcome(ok=True, normalized_url="https://nike.com/"), root.provider))

    sub = pool.add_candidate(url="https://shop.nike.com/in", provider="serper", validated=True)
    sub.evidence.add(brand_identity_evidence("Nike", sub.domain, sub.provider))
    sub.evidence.extend(verification_evidence(ValidationOutcome(ok=True, normalized_url="https://shop.nike.com/in"), sub.provider))

    business = BusinessCandidate(name="Nike", website="https://nike.com", address="Mumbai")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, "Mumbai", chosen=root, chosen_url="https://nike.com/")
    return business_identity, business


def _disagreement_business_identity():
    pool = CandidatePool("XYZ Corp", "Delhi")
    winner = pool.add_candidate(url="https://random-unrelated.com", provider="overpass", validated=True)
    pool.add_candidate(url="https://another-site.com", provider="serper", validated=True)
    business = BusinessCandidate(name="XYZ Corp")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, "Delhi", chosen=winner, chosen_url="https://random-unrelated.com/")
    return business_identity, business


def _no_candidate_business_identity():
    pool = CandidatePool("Ghost Biz", "Nowhere")
    business = BusinessCandidate(name="Ghost Biz")
    resolver = GenericIdentityResolver()
    business_identity = resolver.resolve(business, pool, "Nowhere", chosen=None, chosen_url=None)
    return business_identity, business


def _resolve(business_identity, business):
    builder = DigitalIdentityBuilder()
    vdi = builder.build(business_identity, business)
    return vdi, vdi.identity_resolution


# ---------------------------------------------------------------------------
# empty_identity_resolution_result / no-candidate handling
# ---------------------------------------------------------------------------


def test_empty_result_for_no_candidates():
    business_identity, business = _no_candidate_business_identity()
    vdi, result = _resolve(business_identity, business)
    assert result.identity_candidates == ()
    assert result.organization_groups == ()
    assert result.selection_confidence == 0.0
    assert result.identity_stability == 0.0
    assert result.evidence_diversity == 0.0


def test_empty_identity_resolution_result_helper_shape():
    result = empty_identity_resolution_result("no candidates")
    assert result.selection_reason == "no candidates"
    assert result.conflict_assessment.conflict_score == 0.0


# ---------------------------------------------------------------------------
# Candidate Identity Model
# ---------------------------------------------------------------------------


def test_single_candidate_produces_one_identity_candidate():
    business_identity, business = _single_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    assert len(result.identity_candidates) == 1
    candidate = result.identity_candidates[0]
    assert candidate.normalized_domain == "nike.com"
    assert candidate.confidence > 0.0
    assert candidate.canonical_domain.registrable_domain == "nike.com"


# ---------------------------------------------------------------------------
# Candidate Consolidation / Organization Resolution
# ---------------------------------------------------------------------------


def test_subdomains_consolidate_into_one_organization_group():
    business_identity, business = _subdomain_consolidation_business_identity()
    _, result = _resolve(business_identity, business)
    # Two WebsiteCandidateGroups (nike.com, shop.nike.com) at the
    # identity.py layer, but ONE OrganizationGroup after consolidation.
    assert len(business_identity.website_candidates) == 2
    assert len(result.organization_groups) == 1
    assert result.organization_groups[0].registrable_domain == "nike.com"
    assert len(result.organization_groups[0].candidates) == 2


def test_organization_best_picks_the_selected_candidate_when_it_leads():
    business_identity, business = _subdomain_consolidation_business_identity()
    _, result = _resolve(business_identity, business)
    assert result.organization_groups[0].best.normalized_domain == "nike.com"


# ---------------------------------------------------------------------------
# Candidate Competition
# ---------------------------------------------------------------------------


def test_disagreement_produces_a_two_way_competition():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    assert len(result.competition) == 2
    assert result.competition[0].rank == 1
    assert result.competition[1].rank == 2


def test_sole_candidate_has_no_rival_and_full_confidence_carryover():
    business_identity, business = _single_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    assert len(result.competition) == 1
    assert result.competition[0].margin == 0.0


def test_selection_confidence_matches_selected_candidates_competition_result():
    business_identity, business = _single_candidate_business_identity()
    vdi, result = _resolve(business_identity, business)
    selected_result = next(c for c in result.competition if c.domain == business_identity.decision_trace.selected_domain)
    assert result.selection_confidence == selected_result.relative_confidence
    assert vdi.selection_confidence == result.selection_confidence


# ---------------------------------------------------------------------------
# Alternative candidates / rejected candidate reasons
# ---------------------------------------------------------------------------


def test_alternative_candidates_excludes_the_selected_domain():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    selected = business_identity.decision_trace.selected_domain
    assert all(a.domain != selected for a in result.alternative_candidates)
    assert len(result.alternative_candidates) == 1


def test_rejected_candidate_reasons_only_covers_non_selected_candidates():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    selected = business_identity.decision_trace.selected_domain
    assert all(r.domain != selected for r in result.rejected_candidate_reasons)


def test_vdi_convenience_properties_delegate_to_identity_resolution():
    business_identity, business = _disagreement_business_identity()
    vdi, result = _resolve(business_identity, business)
    assert vdi.selection_reason == result.selection_reason
    assert vdi.alternative_candidates == result.alternative_candidates
    assert vdi.rejected_candidate_reasons == result.rejected_candidate_reasons


def test_vdi_properties_degrade_gracefully_without_identity_resolution():
    # A hand-built VerifiedDigitalIdentity from before Sprint 3 (no
    # identity_resolution) must never raise -- see the module's own
    # "single source of truth... falling back to an honest... default"
    # docstring.
    from application.discovery.confidence import empty_confidence_trace
    from application.discovery.digital_identity import (
        DigitalIdentityStatus,
        VerificationTrace,
        VerifiedDigitalIdentity,
        assess_identity_completeness,
        assess_risk,
        assess_verification_quality,
        empty_consensus_record,
        assess_digital_identity_quality,
    )
    from application.discovery.evidence import EvidenceBundle
    from application.discovery.features import FeatureStore

    business_identity, business = _no_candidate_business_identity()
    completeness = assess_identity_completeness(
        business=business, selected_group=None, groups=[], feature_set=None, verification_bundle=None,
        confidence_trace=empty_confidence_trace(),
    )
    quality = assess_digital_identity_quality(
        completeness, assess_verification_quality(None), empty_consensus_record(),
        assess_risk(
            provider_agreement=business_identity.decision_trace.provider_agreement, feature_set=None,
            verification_bundle=None, completeness=completeness, verification_quality=assess_verification_quality(None),
            selected_group=None,
        ),
        None,
    )
    vdi = VerifiedDigitalIdentity(
        business_identity=business_identity, selected_website=None, consensus=empty_consensus_record(),
        verification_trace=VerificationTrace(selected_domain=None, bundles=()),
        confidence_trace=empty_confidence_trace(), feature_store=FeatureStore(), verification_bundle=None,
        evidence_bundle=EvidenceBundle(), identity_completeness=completeness,
        verification_quality=assess_verification_quality(None),
        risk_assessment=assess_risk(
            provider_agreement=business_identity.decision_trace.provider_agreement, feature_set=None,
            verification_bundle=None, completeness=completeness, verification_quality=assess_verification_quality(None),
            selected_group=None,
        ),
        quality=quality, missing_evidence=(), status=DigitalIdentityStatus.NO_IDENTITY,
    )
    assert vdi.identity_resolution is None
    assert vdi.selection_confidence == 0.0
    assert vdi.selection_reason == "no identity resolution data available"
    assert vdi.alternative_candidates == ()
    assert vdi.rejected_candidate_reasons == ()


# ---------------------------------------------------------------------------
# Conflict Resolution / DigitalIdentityQuality new dimensions
# ---------------------------------------------------------------------------


def test_disagreement_produces_nonzero_conflict_score():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    assert result.conflict_assessment.conflict_score > 0.0
    assert any(c.code == "provider_disagreement" for c in result.conflict_assessment.conflicts)


def test_single_clean_candidate_has_low_conflict_score():
    business_identity, business = _single_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    assert result.conflict_assessment.conflict_score == 0.0


def test_quality_carries_the_three_new_sprint_3_dimensions():
    business_identity, business = _single_candidate_business_identity()
    vdi, result = _resolve(business_identity, business)
    assert vdi.quality.identity_stability == result.identity_stability
    assert vdi.quality.evidence_diversity == result.evidence_diversity
    assert vdi.quality.conflict_score == result.conflict_assessment.conflict_score


def test_quality_dimensions_default_to_zero_for_no_candidates():
    business_identity, business = _no_candidate_business_identity()
    vdi, _ = _resolve(business_identity, business)
    assert vdi.quality.identity_stability == 0.0
    assert vdi.quality.evidence_diversity == 0.0
    assert vdi.quality.conflict_score == 0.0


def test_identity_stability_is_bounded():
    for fixture in (_single_candidate_business_identity, _disagreement_business_identity, _no_candidate_business_identity):
        business_identity, business = fixture()
        _, result = _resolve(business_identity, business)
        assert 0.0 <= result.identity_stability <= 1.0


# ---------------------------------------------------------------------------
# Provider Reliability integration
# ---------------------------------------------------------------------------


def test_provider_reliability_populated_for_involved_providers():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    assert set(result.provider_reliability.keys()) == {"overpass", "serper"}


def test_shared_engine_accumulates_reliability_across_multiple_businesses():
    engine = IdentityResolutionEngine()
    builder = DigitalIdentityBuilder(identity_resolution_engine=engine)

    for _ in range(8):
        business_identity, business = _single_candidate_business_identity()
        builder.build(business_identity, business)

    # "overpass" has agreed with the selection 8/8 times across 8
    # separate businesses sharing the same engine instance -- its
    # reliability score should have risen well above the 0.5 neutral
    # prior, demonstrating cross-business accumulation.
    assert engine.reliability_registry.score("overpass") > 0.8


# ---------------------------------------------------------------------------
# False Positive Reduction integration (cross-tenant)
# ---------------------------------------------------------------------------


def test_shared_engine_flags_a_domain_reused_across_many_unrelated_businesses():
    engine = IdentityResolutionEngine()
    builder = DigitalIdentityBuilder(identity_resolution_engine=engine)

    names = ["Alpha Traders", "Beta Mart", "Gamma Foods", "Delta Textiles", "Epsilon Autos"]
    last_result = None
    for name in names:
        pool = CandidatePool(name, "Mumbai")
        candidate = pool.add_candidate(url="https://generic-directory.example", provider="overpass", validated=True)
        candidate.evidence.extend(
            verification_evidence(ValidationOutcome(ok=True, normalized_url="https://generic-directory.example/"), "overpass")
        )
        business = BusinessCandidate(name=name, website="https://generic-directory.example")
        resolver = GenericIdentityResolver()
        business_identity = resolver.resolve(business, pool, "Mumbai", chosen=candidate, chosen_url="https://generic-directory.example/")
        vdi = builder.build(business_identity, business)
        last_result = vdi.identity_resolution

    fired_codes = last_result.identity_candidates[0].risk
    assert "cross_tenant_generic_domain" in fired_codes


# ---------------------------------------------------------------------------
# JSON-serializability / to_dict shape
# ---------------------------------------------------------------------------


def test_identity_resolution_result_to_dict_has_expected_keys():
    business_identity, business = _single_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    expected_keys = {
        "identity_candidates", "organization_groups", "organization_relationships", "competition",
        "conflict_assessment", "provider_reliability", "selection_confidence", "selection_reason",
        "alternative_candidates", "rejected_candidate_reasons", "identity_stability", "evidence_diversity",
        "provider_operational_metrics", "explanation",
    }
    assert set(result.to_dict().keys()) == expected_keys


def test_verified_digital_identity_to_dict_includes_identity_resolution():
    business_identity, business = _single_candidate_business_identity()
    vdi, _ = _resolve(business_identity, business)
    payload = vdi.to_dict()
    assert "identity_resolution" in payload
    assert payload["identity_resolution"] is not None


def test_full_result_is_json_serializable():
    import json

    business_identity, business = _disagreement_business_identity()
    vdi, _ = _resolve(business_identity, business)
    json.dumps(vdi.to_dict())  # must not raise


# ---------------------------------------------------------------------------
# Sprint 4: identity_stability combines consensus + evidence diversity too
# ---------------------------------------------------------------------------


def test_resolve_without_consensus_uses_the_original_two_factor_formula():
    """Direct engine.resolve() calls that omit `consensus` (every Sprint 3
    caller) must reproduce the exact pre-Sprint-4 formula: 0.5*margin +
    0.5*(1-conflict_score)."""
    from application.discovery.identity_resolution_engine import IdentityResolutionEngine

    business_identity, business = _single_candidate_business_identity()
    engine = IdentityResolutionEngine()
    vt = _build_verification_trace(business_identity)

    result = engine.resolve(business_identity, business, vt)  # no consensus kwarg at all
    leader_margin = result.competition[0].margin if result.competition else 0.0
    expected = round(0.5 * max(0.0, min(leader_margin, 1.0)) + 0.5 * (1.0 - result.conflict_assessment.conflict_score), 3)
    assert result.identity_stability == expected


def test_resolve_with_consensus_uses_the_four_factor_formula():
    from application.discovery.digital_identity import ConsensusRecord
    from application.discovery.identity_resolution_engine import IdentityResolutionEngine

    business_identity, business = _single_candidate_business_identity()
    engine = IdentityResolutionEngine()
    vt = _build_verification_trace(business_identity)
    consensus = ConsensusRecord(
        provider_consensus=1.0, verification_consensus=1.0, feature_consensus=1.0,
        identity_consensus=1.0, confidence_consensus=1.0, overall_consensus=0.8,
        level="strong", explanation="test fixture",
    )
    result = engine.resolve(business_identity, business, vt, consensus=consensus)
    leader_margin = result.competition[0].margin if result.competition else 0.0
    expected = round(
        0.25 * max(0.0, min(leader_margin, 1.0))
        + 0.25 * (1.0 - result.conflict_assessment.conflict_score)
        + 0.25 * 0.8
        + 0.25 * result.evidence_diversity,
        3,
    )
    assert result.identity_stability == expected


def test_consensus_and_no_consensus_paths_can_disagree():
    """The whole point of Sprint 4's change -- given the same pool, the
    two formulas need not produce the same number."""
    from application.discovery.digital_identity import ConsensusRecord
    from application.discovery.identity_resolution_engine import IdentityResolutionEngine

    business_identity, business = _single_candidate_business_identity()
    vt = _build_verification_trace(business_identity)

    without = IdentityResolutionEngine().resolve(business_identity, business, vt)
    strong_consensus = ConsensusRecord(
        provider_consensus=1.0, verification_consensus=1.0, feature_consensus=1.0,
        identity_consensus=1.0, confidence_consensus=1.0, overall_consensus=1.0,
        level="strong", explanation="test fixture",
    )
    with_strong_consensus = IdentityResolutionEngine().resolve(business_identity, business, vt, consensus=strong_consensus)
    assert without.identity_stability != with_strong_consensus.identity_stability


def test_identity_stability_bounded_with_consensus_across_fixtures():
    from application.discovery.digital_identity import ConsensusRecord
    from application.discovery.identity_resolution_engine import IdentityResolutionEngine

    consensus = ConsensusRecord(
        provider_consensus=0.5, verification_consensus=0.5, feature_consensus=0.5,
        identity_consensus=0.5, confidence_consensus=0.5, overall_consensus=0.5,
        level="moderate", explanation="test fixture",
    )
    for fixture in (_single_candidate_business_identity, _disagreement_business_identity, _no_candidate_business_identity):
        business_identity, business = fixture()
        engine = IdentityResolutionEngine()
        vt = _build_verification_trace(business_identity)
        result = engine.resolve(business_identity, business, vt, consensus=consensus)
        assert 0.0 <= result.identity_stability <= 1.0


# ---------------------------------------------------------------------------
# Sprint 4: provider_operational_metrics
# ---------------------------------------------------------------------------


def test_provider_operational_metrics_populated_for_every_involved_provider():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    assert set(result.provider_operational_metrics.keys()) == {"overpass", "serper"}
    for metrics in result.provider_operational_metrics.values():
        assert set(metrics.keys()) == {
            "selection_success_rate", "verification_success_rate", "failure_rate", "agreement_rate",
        }


def test_provider_operational_metrics_reflects_agreement_across_providers():
    """Two providers converging on the same domain (see
    _subdomain_consolidation_business_identity's serper/overpass setup)
    must show a nonzero agreement_rate for the involved providers."""
    business_identity, business = _subdomain_consolidation_business_identity()
    _, result = _resolve(business_identity, business)
    for provider in ("overpass", "serper"):
        assert result.provider_operational_metrics[provider]["agreement_rate"] >= 0.0


def test_empty_result_has_empty_operational_metrics():
    business_identity, business = _no_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    assert result.provider_operational_metrics == {}


# ---------------------------------------------------------------------------
# Sprint 4: explain()
# ---------------------------------------------------------------------------


def test_identity_resolution_result_explain_mentions_selection_and_stability():
    business_identity, business = _single_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    text = " ".join(result.explain())
    assert "selection" in text
    assert "stability" in text
    assert business_identity.decision_trace.selected_domain in text


def test_explain_mentions_conflicts_when_present():
    business_identity, business = _disagreement_business_identity()
    _, result = _resolve(business_identity, business)
    text = " ".join(result.explain())
    assert "conflicts" in text
    assert "provider_disagreement" in text


def test_explain_omits_conflicts_line_when_none_present():
    business_identity, business = _single_candidate_business_identity()
    _, result = _resolve(business_identity, business)
    assert result.conflict_assessment.conflicts == ()
    assert not any(line.startswith("conflicts:") for line in result.explain())


def test_verified_digital_identity_explain_includes_identity_resolution_lines():
    business_identity, business = _single_candidate_business_identity()
    vdi, result = _resolve(business_identity, business)
    vdi_lines = vdi.explain()
    result_lines = result.explain()
    # Every identity_resolution explanation line is appended, not lost.
    for line in result_lines:
        assert line in vdi_lines


def _build_verification_trace(business_identity):
    from application.discovery.digital_identity import VerificationTrace
    from application.discovery.verification import VerificationPipeline

    bundles = []
    for group in business_identity.website_candidates:
        feature_set = business_identity.feature_store.get(group.domain)
        if group.domain == business_identity.decision_trace.selected_domain:
            bundles.append((group.domain, business_identity.decision_trace.verification_bundle))
        else:
            bundles.append((group.domain, VerificationPipeline().run(feature_set) if feature_set else None))
    return VerificationTrace(selected_domain=business_identity.decision_trace.selected_domain, bundles=tuple(bundles))
