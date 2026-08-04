"""
Unit tests for application.discovery.reliability (Sprint 3).
"""

from application.discovery.reliability import ProviderReliabilityRegistry


def test_unknown_provider_starts_at_neutral_prior():
    registry = ProviderReliabilityRegistry()
    assert registry.score("overpass") == 0.5


def test_score_moves_up_toward_agreement_rate_after_observations():
    registry = ProviderReliabilityRegistry()
    for _ in range(20):
        registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    assert registry.score("overpass") > 0.8


def test_score_moves_down_after_repeated_disagreement():
    registry = ProviderReliabilityRegistry()
    for _ in range(20):
        registry.observe_proposal("shady_provider", verified=True, agreed_with_selection=False)
    assert registry.score("shady_provider") < 0.2


def test_score_never_reaches_exact_zero_or_one():
    registry = ProviderReliabilityRegistry()
    for _ in range(1000):
        registry.observe_proposal("always_right", verified=True, agreed_with_selection=True)
        registry.observe_proposal("always_wrong", verified=True, agreed_with_selection=False)
    assert 0.0 < registry.score("always_wrong") < 0.01
    assert 0.99 < registry.score("always_right") < 1.0


def test_single_observation_moves_score_by_a_bounded_amount():
    registry = ProviderReliabilityRegistry()
    baseline = registry.score("overpass")
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    assert registry.score("overpass") > baseline
    # A single observation is a small nudge, not a snap to 1.0 -- the
    # Beta(2,2) prior means one agreement moves it from 0.5 to 0.6.
    assert registry.score("overpass") < 0.7


def test_empty_provider_name_returns_neutral_prior_and_is_ignored():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("", verified=True, agreed_with_selection=True)
    assert registry.score("") == 0.5
    assert registry.snapshot() == {}


def test_record_creates_and_returns_stable_record():
    registry = ProviderReliabilityRegistry()
    record_one = registry.record("serper")
    registry.observe_proposal("serper", verified=True, agreed_with_selection=True)
    record_two = registry.record("serper")
    assert record_one is record_two
    assert record_one.proposals_total == 1


def test_verified_rate_tracks_reachability_independent_of_agreement():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=False)
    registry.observe_proposal("overpass", verified=False, agreed_with_selection=False)
    record = registry.record("overpass")
    assert record.proposals_verified == 1
    assert record.proposals_total == 2
    assert record.verified_rate == 0.5


def test_explain_cites_actual_observed_counts():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=False)
    explanation = registry.record("overpass").explain()
    assert "1/2" in explanation
    assert "overpass" in explanation


def test_snapshot_reflects_every_observed_provider():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    registry.observe_proposal("serper", verified=True, agreed_with_selection=False)
    snapshot = registry.snapshot()
    assert set(snapshot.keys()) == {"overpass", "serper"}


def test_to_dict_has_expected_shape():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    result = registry.to_dict()
    assert "overpass" in result
    assert set(result["overpass"].keys()) == {
        "provider", "proposals_total", "proposals_agreed", "proposals_verified",
        "proposals_with_peers", "proposals_in_agreement",
        "reliability_score", "verified_rate",
        "selection_success_rate", "verification_success_rate", "failure_rate", "agreement_rate",
        "explanation",
    }


def test_two_providers_tracked_independently():
    registry = ProviderReliabilityRegistry()
    for _ in range(10):
        registry.observe_proposal("good", verified=True, agreed_with_selection=True)
        registry.observe_proposal("bad", verified=True, agreed_with_selection=False)
    assert registry.score("good") > registry.score("bad")


# -- Sprint 4: deterministic operational metrics --------------------------------


def test_selection_success_rate_is_unsmoothed_raw_ratio():
    registry = ProviderReliabilityRegistry()
    for _ in range(3):
        registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=False)
    record = registry.record("overpass")
    assert record.selection_success_rate == 0.75
    # Unlike reliability_score, selection_success_rate is NOT Beta-smoothed
    # -- (3 + 2) / (4 + 4) = 0.625 vs the raw 3/4 = 0.75.
    assert record.selection_success_rate != record.reliability_score


def test_selection_success_rate_zero_with_no_observations():
    registry = ProviderReliabilityRegistry()
    assert registry.record("overpass").selection_success_rate == 0.0


def test_verification_success_rate_is_an_alias_of_verified_rate():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=False)
    registry.observe_proposal("overpass", verified=False, agreed_with_selection=False)
    record = registry.record("overpass")
    assert record.verification_success_rate == record.verified_rate == 0.5


def test_failure_rate_is_the_complement_of_verification_success_rate():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=False)
    registry.observe_proposal("overpass", verified=False, agreed_with_selection=False)
    registry.observe_proposal("overpass", verified=False, agreed_with_selection=False)
    record = registry.record("overpass")
    assert record.failure_rate == round(1.0 - record.verification_success_rate, 3)
    assert record.failure_rate == round(2 / 3, 3)


def test_failure_rate_zero_with_no_observations():
    registry = ProviderReliabilityRegistry()
    assert registry.record("overpass").failure_rate == 0.0


def test_agreement_rate_none_without_a_peer_observation():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    assert registry.record("overpass").agreement_rate == 0.0
    assert registry.record("overpass").proposals_with_peers == 0


def test_agreement_rate_tracks_only_proposals_with_a_peer():
    registry = ProviderReliabilityRegistry()
    # One single-source proposal (no peer) -- must not count toward
    # agreement_rate's denominator at all.
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    # Two proposals with a peer present, one agreeing, one not.
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True, agreed_with_peers=True)
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=False, agreed_with_peers=False)
    record = registry.record("overpass")
    assert record.proposals_with_peers == 2
    assert record.proposals_in_agreement == 1
    assert record.agreement_rate == 0.5
    assert record.proposals_total == 3


def test_operational_metrics_bundle_has_all_four_keys():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True, agreed_with_peers=True)
    metrics = registry.record("overpass").operational_metrics()
    assert set(metrics.keys()) == {
        "selection_success_rate", "verification_success_rate", "failure_rate", "agreement_rate",
    }


def test_observe_proposal_without_agreed_with_peers_is_fully_backward_compatible():
    """The exact pre-Sprint-4 call shape (no agreed_with_peers at all)
    must leave proposals_with_peers/proposals_in_agreement untouched."""
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True)
    record = registry.record("overpass")
    assert record.proposals_with_peers == 0
    assert record.proposals_in_agreement == 0


def test_explain_mentions_the_new_operational_metrics():
    registry = ProviderReliabilityRegistry()
    registry.observe_proposal("overpass", verified=True, agreed_with_selection=True, agreed_with_peers=True)
    explanation = registry.record("overpass").explain()
    assert "verification_success_rate" in explanation
    assert "failure_rate" in explanation
    assert "agreement_rate" in explanation
