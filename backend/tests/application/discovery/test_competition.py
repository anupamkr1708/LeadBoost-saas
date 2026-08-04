"""
Unit tests for application.discovery.competition (Sprint 3).
"""

from application.discovery import canonicalization as canon
from application.discovery.competition import (
    ConflictRecord,
    assess_conflicts,
    build_conflict_assessment,
    compete,
    empty_conflict_assessment,
    evidence_diversity,
    identity_quality_gap_conflict,
)
from application.discovery.evidence import Evidence, EvidenceBundle, EvidenceType
from application.discovery.identity import ProviderAgreementRecord
from application.discovery.organization import IdentityCandidate, OrganizationGroup, OrganizationRelationship, RelationshipType


def _candidate(domain, confidence, identity_quality=0.3, provider=("overpass",), evidence_types=()):
    bundle = EvidenceBundle()
    for t in evidence_types:
        bundle.add(Evidence(type=t, provider="overpass", confidence_delta=0.1, reason="r"))
    return IdentityCandidate(
        provider=provider,
        normalized_domain=domain,
        canonical_domain=canon.canonicalize(domain),
        website=f"https://{domain}",
        supporting_evidence=bundle,
        provider_confidence=0.5,
        verification_results=None,
        feature_store_entry=None,
        confidence=confidence,
        identity_quality=identity_quality,
    )


# -- compete (Candidate Competition) -------------------------------------------


def test_compete_empty_pool_returns_empty_list():
    assert compete([]) == []


def test_compete_sole_candidate_keeps_its_own_confidence_unchanged():
    candidate = _candidate("nike.com", 0.6)
    [result] = compete([candidate])
    assert result.relative_confidence == 0.6
    assert result.margin == 0.0
    assert result.rank == 1
    assert "only candidate" in result.reason


def test_compete_ranks_best_confidence_first():
    weak = _candidate("weak.com", 0.3)
    strong = _candidate("strong.com", 0.8)
    results = compete([weak, strong])
    assert results[0].domain == "strong.com"
    assert results[0].rank == 1
    assert results[1].domain == "weak.com"
    assert results[1].rank == 2


def test_compete_leader_gets_a_small_confidence_bonus_for_a_decisive_margin():
    leader = _candidate("leader.com", 0.9)
    trailing = _candidate("trailing.com", 0.2)
    results = compete([leader, trailing])
    leader_result = next(r for r in results if r.domain == "leader.com")
    assert leader_result.relative_confidence > leader_result.base_confidence
    assert leader_result.margin > 0


def test_compete_runner_up_gets_a_small_penalty_for_trailing():
    leader = _candidate("leader.com", 0.9)
    trailing = _candidate("trailing.com", 0.2)
    results = compete([leader, trailing])
    trailing_result = next(r for r in results if r.domain == "trailing.com")
    assert trailing_result.relative_confidence < trailing_result.base_confidence
    assert trailing_result.margin < 0


def test_compete_adjustment_never_flips_the_ranking():
    # Even at the maximum possible margin, the adjustment (bounded, see
    # _COMPETITION_ADJUSTMENT_WEIGHT) can never push the runner-up's
    # relative_confidence above the leader's.
    leader = _candidate("leader.com", 1.0)
    trailing = _candidate("trailing.com", 0.0)
    results = compete([leader, trailing])
    assert results[0].relative_confidence >= results[1].relative_confidence


def test_compete_relative_confidence_always_clamped_to_valid_range():
    candidates = [_candidate("a.com", 1.0), _candidate("b.com", 0.99), _candidate("c.com", 0.0)]
    for result in compete(candidates):
        assert 0.0 <= result.relative_confidence <= 1.0


def test_compete_ties_broken_deterministically_by_identity_quality():
    a = _candidate("a.com", 0.5, identity_quality=0.2)
    b = _candidate("b.com", 0.5, identity_quality=0.8)
    results = compete([a, b])
    assert results[0].domain == "b.com"


# -- Sprint 4: explicit winning/losing margin & evidence support ----------------


def test_compete_sole_candidate_has_zero_winning_and_losing_margin():
    [result] = compete([_candidate("nike.com", 0.6)])
    assert result.winning_margin == 0.0
    assert result.losing_margin == 0.0


def test_compete_leader_has_nonzero_winning_margin_and_zero_losing_margin():
    leader = _candidate("leader.com", 0.9)
    trailing = _candidate("trailing.com", 0.2)
    leader_result = next(r for r in compete([leader, trailing]) if r.domain == "leader.com")
    assert leader_result.winning_margin == round(0.9 - 0.2, 3)
    assert leader_result.losing_margin == 0.0


def test_compete_runner_up_has_nonzero_losing_margin_and_zero_winning_margin():
    leader = _candidate("leader.com", 0.9)
    trailing = _candidate("trailing.com", 0.2)
    trailing_result = next(r for r in compete([leader, trailing]) if r.domain == "trailing.com")
    assert trailing_result.losing_margin == round(0.9 - 0.2, 3)
    assert trailing_result.winning_margin == 0.0


def test_compete_winning_and_losing_margins_are_always_non_negative():
    candidates = [_candidate("a.com", 0.9), _candidate("b.com", 0.5), _candidate("c.com", 0.1)]
    for result in compete(candidates):
        assert result.winning_margin >= 0.0
        assert result.losing_margin >= 0.0


def test_compete_evidence_support_matches_standalone_evidence_diversity():
    candidate = _candidate("nike.com", 0.7, evidence_types=(EvidenceType.BRAND_MATCH, EvidenceType.LOCATION_MATCH))
    [result] = compete([candidate])
    assert result.evidence_support == evidence_diversity(candidate)


def test_compete_evidence_support_computed_per_candidate_not_only_for_leader():
    weak_evidence = _candidate("weak.com", 0.9, evidence_types=(EvidenceType.BRAND_MATCH,))
    rich_evidence = _candidate(
        "rich.com", 0.2, evidence_types=(EvidenceType.BRAND_MATCH, EvidenceType.LOCATION_MATCH, EvidenceType.REACHABILITY)
    )
    results = compete([weak_evidence, rich_evidence])
    by_domain = {r.domain: r for r in results}
    # The runner-up (lower confidence) can still have richer evidence
    # support than the leader -- each candidate's own support is
    # independent of its rank.
    assert by_domain["rich.com"].evidence_support > by_domain["weak.com"].evidence_support


def test_competition_result_to_dict_includes_sprint4_fields():
    [result] = compete([_candidate("nike.com", 0.5)])
    payload = result.to_dict()
    assert "winning_margin" in payload
    assert "losing_margin" in payload
    assert "evidence_support" in payload


# -- evidence_diversity (Evidence Corroboration) -------------------------------


def test_evidence_diversity_zero_for_no_evidence():
    candidate = _candidate("nike.com", 0.5, evidence_types=())
    assert evidence_diversity(candidate) == 0.0


def test_evidence_diversity_counts_distinct_types_not_raw_items():
    # Three PROVIDER_AGREEMENT items (e.g. three agreeing providers) is
    # still just ONE distinct type -- diversity must not read this as
    # more diverse than a single BRAND_MATCH observation.
    many_same_type = _candidate(
        "nike.com", 0.5, evidence_types=(EvidenceType.PROVIDER_AGREEMENT, EvidenceType.PROVIDER_AGREEMENT, EvidenceType.PROVIDER_AGREEMENT)
    )
    one_type = _candidate("adidas.com", 0.5, evidence_types=(EvidenceType.BRAND_MATCH,))
    assert evidence_diversity(many_same_type) == evidence_diversity(one_type)


def test_evidence_diversity_increases_with_distinct_kinds_of_evidence():
    narrow = _candidate("a.com", 0.5, evidence_types=(EvidenceType.BRAND_MATCH,))
    broad = _candidate(
        "b.com",
        0.5,
        evidence_types=(EvidenceType.BRAND_MATCH, EvidenceType.LOCATION_MATCH, EvidenceType.REACHABILITY),
    )
    assert evidence_diversity(broad) > evidence_diversity(narrow)


def test_evidence_diversity_capped_at_one():
    all_types = list(EvidenceType)
    candidate = _candidate("nike.com", 0.5, evidence_types=all_types)
    assert evidence_diversity(candidate) == 1.0


# -- Conflict Resolution --------------------------------------------------------


def test_empty_conflict_assessment_has_zero_score():
    result = empty_conflict_assessment()
    assert result.conflicts == ()
    assert result.conflict_score == 0.0


def test_assess_conflicts_flags_provider_disagreement():
    disagreement = ProviderAgreementRecord(
        status="disagreement", agreeing_domain=None, agreeing_providers=[],
        disagreements=[{"provider": "overpass", "domain": "a.com"}, {"provider": "serper", "domain": "b.com"}],
    )
    result = assess_conflicts(
        provider_agreement=disagreement, organization_groups=[], relationships=[], competition=[]
    )
    assert any(c.code == "provider_disagreement" for c in result.conflicts)
    assert result.conflict_score > 0.0


def test_assess_conflicts_flags_multiple_organizations():
    agreement = ProviderAgreementRecord(
        status="single_source", agreeing_domain="a.com", agreeing_providers=["overpass"], disagreements=[]
    )
    groups = [OrganizationGroup(registrable_domain="a.com"), OrganizationGroup(registrable_domain="b.com")]
    result = assess_conflicts(provider_agreement=agreement, organization_groups=groups, relationships=[], competition=[])
    assert any(c.code == "multiple_organizations_proposed" for c in result.conflicts)


def test_assess_conflicts_flags_close_competitive_margin():
    agreement = ProviderAgreementRecord(status="single_source", agreeing_domain="a.com", agreeing_providers=["overpass"], disagreements=[])
    a, b = _candidate("a.com", 0.51), _candidate("b.com", 0.50)
    competition = compete([a, b])
    result = assess_conflicts(provider_agreement=agreement, organization_groups=[], relationships=[], competition=competition)
    assert any(c.code == "close_competitive_margin" for c in result.conflicts)


def test_assess_conflicts_flags_conflicting_redirect_relationships():
    agreement = ProviderAgreementRecord(status="single_source", agreeing_domain="a.com", agreeing_providers=["overpass"], disagreements=[])
    relationship = OrganizationRelationship(
        from_domain="a.com", to_domain="b.com", relationship_type=RelationshipType.REDIRECT, evidence=("redirected",)
    )
    result = assess_conflicts(provider_agreement=agreement, organization_groups=[], relationships=[relationship], competition=[])
    assert any(c.code == "conflicting_redirect" for c in result.conflicts)


def test_assess_conflicts_clean_pool_has_no_conflicts():
    agreement = ProviderAgreementRecord(status="single_source", agreeing_domain="a.com", agreeing_providers=["overpass"], disagreements=[])
    result = assess_conflicts(provider_agreement=agreement, organization_groups=[OrganizationGroup(registrable_domain="a.com")], relationships=[], competition=[])
    assert result.conflicts == ()
    assert result.conflict_score == 0.0


def test_assess_conflicts_score_never_exceeds_one():
    disagreement = ProviderAgreementRecord(
        status="disagreement", agreeing_domain=None, agreeing_providers=[],
        disagreements=[{"provider": "overpass", "domain": "a.com"}, {"provider": "serper", "domain": "b.com"}],
    )
    groups = [OrganizationGroup(registrable_domain="a.com"), OrganizationGroup(registrable_domain="b.com"), OrganizationGroup(registrable_domain="c.com")]
    relationship = OrganizationRelationship(from_domain="a.com", to_domain="b.com", relationship_type=RelationshipType.REDIRECT, evidence=("x",))
    a, b = _candidate("a.com", 0.51), _candidate("b.com", 0.50)
    result = assess_conflicts(
        provider_agreement=disagreement, organization_groups=groups, relationships=[relationship], competition=compete([a, b])
    )
    assert result.conflict_score <= 1.0


def test_identity_quality_gap_conflict_fires_on_large_gap():
    leader = _candidate("a.com", 0.9, identity_quality=0.9)
    runner_up = _candidate("b.com", 0.8, identity_quality=0.1)
    conflict = identity_quality_gap_conflict([leader, runner_up])
    assert conflict is not None
    assert conflict.code == "differing_identity_quality"


def test_identity_quality_gap_conflict_none_for_small_gap():
    leader = _candidate("a.com", 0.9, identity_quality=0.5)
    runner_up = _candidate("b.com", 0.8, identity_quality=0.45)
    assert identity_quality_gap_conflict([leader, runner_up]) is None


def test_identity_quality_gap_conflict_none_with_fewer_than_two_candidates():
    assert identity_quality_gap_conflict([_candidate("a.com", 0.9)]) is None
    assert identity_quality_gap_conflict([]) is None


def test_build_conflict_assessment_combines_records_from_multiple_sources():
    record_one = ConflictRecord(code="provider_disagreement", description="d", evidence=())
    record_two = ConflictRecord(code="differing_identity_quality", description="d", evidence=())
    result = build_conflict_assessment([record_one, record_two])
    assert len(result.conflicts) == 2
    assert result.conflict_score == round(0.40 + 0.15, 3)


def test_conflict_record_and_assessment_to_dict_have_expected_keys():
    record = ConflictRecord(code="x", description="d", evidence=("e",))
    assert set(record.to_dict().keys()) == {"code", "description", "evidence"}
    assessment = build_conflict_assessment([record])
    assert set(assessment.to_dict().keys()) == {"conflict_score", "conflicts"}
