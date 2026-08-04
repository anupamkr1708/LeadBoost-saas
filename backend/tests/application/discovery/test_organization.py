"""
Unit tests for application.discovery.organization (Sprint 3).

Uses plain, minimal stand-ins for identity.WebsiteCandidateGroup (only
the attributes build_identity_candidate actually reads) rather than
running the full pipeline -- same "construct the minimal input" approach
test_identity.py already uses for its own pure-function tests.
"""

from application.discovery.evidence import EvidenceBundle
from application.discovery.organization import (
    OrganizationGroup,
    RelationshipType,
    build_identity_candidate,
    consolidate,
    resolve_relationships,
    with_risk_and_trace,
)


class _FakeGroup:
    def __init__(self, domain, urls, providers, confidence=0.5, identity_confidence=0.3):
        self.domain = domain
        self.urls = urls
        self.contributing_providers = providers
        self.evidence = EvidenceBundle()
        self.confidence = confidence
        self.identity_confidence = identity_confidence


def _candidate(domain, urls=None, providers=("overpass",), confidence=0.5, identity_confidence=0.3):
    group = _FakeGroup(domain, urls or [f"https://{domain}"], list(providers), confidence, identity_confidence)
    return build_identity_candidate(group, provider_confidence=0.5, verification_bundle=None, feature_set=None)


# -- build_identity_candidate --------------------------------------------------


def test_build_identity_candidate_carries_over_group_fields():
    candidate = _candidate("nike.com", confidence=0.7, identity_confidence=0.4)
    assert candidate.normalized_domain == "nike.com"
    assert candidate.confidence == 0.7
    assert candidate.identity_quality == 0.4
    assert candidate.website == "https://nike.com"
    assert candidate.provider == ("overpass",)


def test_build_identity_candidate_computes_canonical_domain():
    candidate = _candidate("shop.nike.com")
    assert candidate.canonical_domain.registrable_domain == "nike.com"
    assert candidate.canonical_domain.subdomain == "shop"


def test_build_identity_candidate_starts_with_empty_risk_and_trace():
    candidate = _candidate("nike.com")
    assert candidate.risk == ()
    assert candidate.decision_trace == {}


def test_identity_candidate_registrable_domain_property():
    candidate = _candidate("shop.nike.com")
    assert candidate.registrable_domain == "nike.com"


def test_identity_candidate_to_dict_has_expected_keys():
    result = _candidate("nike.com").to_dict()
    assert set(result.keys()) == {
        "provider", "normalized_domain", "canonical_domain", "website", "provider_confidence",
        "verification_results", "decision_trace", "risk", "confidence", "identity_quality",
    }


# -- with_risk_and_trace (immutability) ----------------------------------------


def test_with_risk_and_trace_returns_new_instance_never_mutates():
    original = _candidate("nike.com")
    updated = with_risk_and_trace(original, risk=("unsupported_candidate",), decision_trace={"rank": 1})
    assert original.risk == ()
    assert original.decision_trace == {}
    assert updated.risk == ("unsupported_candidate",)
    assert updated.decision_trace == {"rank": 1}
    assert updated is not original


# -- consolidate (Candidate Consolidation) -------------------------------------


def test_consolidate_merges_subdomains_sharing_a_registrable_domain():
    candidates = [
        _candidate("company.com", providers=("overpass",)),
        _candidate("shop.company.com", providers=("serper",)),
        _candidate("support.company.com", providers=("brave",)),
    ]
    groups = consolidate(candidates)
    assert len(groups) == 1
    assert groups[0].registrable_domain == "company.com"
    assert len(groups[0].candidates) == 3


def test_consolidate_keeps_distinct_registrable_domains_separate():
    candidates = [_candidate("company.com"), _candidate("unrelated-brand.com")]
    groups = consolidate(candidates)
    assert len(groups) == 2
    assert {g.registrable_domain for g in groups} == {"company.com", "unrelated-brand.com"}


def test_consolidate_www_and_bare_domain_collapse_together():
    candidates = [_candidate("www.company.com"), _candidate("company.com")]
    groups = consolidate(candidates)
    assert len(groups) == 1


def test_consolidate_empty_input_returns_empty_list():
    assert consolidate([]) == []


def test_organization_group_best_picks_highest_confidence_candidate():
    weak = _candidate("shop.company.com", providers=("serper",), confidence=0.3, identity_confidence=0.1)
    strong = _candidate("company.com", providers=("overpass",), confidence=0.9, identity_confidence=0.5)
    group = OrganizationGroup(registrable_domain="company.com", candidates=[weak, strong])
    assert group.best is strong


def test_organization_group_best_is_none_for_empty_group():
    assert OrganizationGroup(registrable_domain="company.com").best is None


def test_organization_group_all_providers_deduplicates_across_candidates():
    candidates = [
        _candidate("company.com", providers=("overpass", "serper")),
        _candidate("shop.company.com", providers=("serper", "brave")),
    ]
    group = OrganizationGroup(registrable_domain="company.com", candidates=candidates)
    assert group.all_providers == ("overpass", "serper", "brave")


def test_organization_group_to_dict_has_expected_keys():
    group = OrganizationGroup(registrable_domain="company.com", candidates=[_candidate("company.com")])
    result = group.to_dict()
    assert set(result.keys()) == {"registrable_domain", "candidates", "providers"}


# -- resolve_relationships (Organization Resolution) ---------------------------


def test_resolve_relationships_flags_cross_tld_root_label_match_as_regional():
    groups = consolidate([_candidate("company.com"), _candidate("company.co.uk")])
    relationships = resolve_relationships(groups)
    assert len(relationships) == 1
    assert relationships[0].relationship_type is RelationshipType.REGIONAL
    assert relationships[0].from_domain == "company.com"
    assert relationships[0].to_domain == "company.co.uk"


def test_resolve_relationships_flags_two_simple_tlds_as_unknown():
    groups = consolidate([_candidate("company.com"), _candidate("company.ai")])
    relationships = resolve_relationships(groups)
    assert len(relationships) == 1
    assert relationships[0].relationship_type is RelationshipType.UNKNOWN


def test_resolve_relationships_never_relates_unrelated_domains():
    groups = consolidate([_candidate("nike.com"), _candidate("adidas.com")])
    assert resolve_relationships(groups) == []


def test_resolve_relationships_never_fabricates_a_relationship_for_a_single_group():
    groups = consolidate([_candidate("nike.com")])
    assert resolve_relationships(groups) == []


def test_resolve_relationships_carries_real_evidence_string():
    groups = consolidate([_candidate("company.com"), _candidate("company.co.uk")])
    relationship = resolve_relationships(groups)[0]
    assert "company" in relationship.evidence[0]
    assert "co.uk" in relationship.evidence[0]


def test_organization_relationship_to_dict_has_expected_keys():
    groups = consolidate([_candidate("company.com"), _candidate("company.ai")])
    result = resolve_relationships(groups)[0].to_dict()
    assert set(result.keys()) == {"from_domain", "to_domain", "relationship_type", "evidence"}
