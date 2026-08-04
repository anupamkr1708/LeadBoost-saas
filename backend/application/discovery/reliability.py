"""
Provider Reliability (Sprint 3; extended Sprint 4).

"Discovery currently trusts providers equally. Introduce generic provider
reliability. Not fixed weights. Provider reliability should evolve from
observed agreement. Provider confidence must remain explainable."

Every other engine in this package (`ConfidencePropagationEngine`,
`VerificationPipeline`, `ConsensusEngine`) is deliberately stateless and
pure -- this module is the one, explicit exception, and the statefulness
is the entire point: a reliability score that reset to the same neutral
prior on every single call could never "evolve from observed agreement."
`ProviderReliabilityRegistry` is still deterministic and still fully
explainable (see `ProviderReliabilityRecord.explain`) -- it just has
memory, the same way a cache does, and like a cache, identical inputs
observed in the same order always produce the same resulting score.

Scope, by construction: `WebsiteResolver.__init__` builds exactly one
`DigitalIdentityBuilder` for its own lifetime, and `DiscoveryService`
reuses one `WebsiteResolver` across every business in one discovery run
(see discovery_service.py's `_resolve_and_validate`, which calls
`self.resolver.resolve_with_digital_identity(...)` once per candidate
against the same `self.resolver`). A registry created as
`DigitalIdentityBuilder`'s default therefore naturally accumulates
observations across an entire discovery run -- e.g. "Serper agreed with
the final selection in 11 of the last 12 businesses in this run" --
without any change to `WebsiteResolver` or `DiscoveryService` (both
remain exactly as Sprint 2C left them). A caller that wants reliability
to persist across separate discovery runs can construct one
`ProviderReliabilityRegistry` and inject it into every
`WebsiteResolver`/`DigitalIdentityBuilder` it creates; the default
(a fresh registry per `WebsiteResolver`) keeps today's behavior
unchanged for anyone who doesn't.

No ML, no learned weights: `reliability_score` is a plain Beta-smoothed
ratio (Laplace/Beta smoothing with a fixed, documented, symmetric prior)
-- the same kind of fixed, explainable arithmetic every other confidence
number in this package already uses, just applied across calls instead
of within one.

Sprint 4: deterministic operational metrics
-----------------------------------------------
Four distinct, plainly-named, always-computable-from-existing-counters
questions about one provider's track record within a registry's scope --
none of them new I/O, none of them learned, all four already implicit in
data this module (or `identity.ProviderAgreementRecord`, Sprint 2A,
unchanged) already had:

  - `selection_success_rate` -- of every proposal this provider made, what
    fraction became the domain actually selected for that business. The
    same raw ratio `reliability_score` already Beta-smooths, now also
    exposed unsmoothed for a caller that wants the plain observed rate
    rather than a confidence-shrunk one.
  - `verification_success_rate` -- of every proposal this provider made,
    what fraction independently passed validation (selected or not).
    Renamed-and-kept alias of the existing `verified_rate` (unchanged,
    still present) -- same number, clearer name matching the brief's own
    terminology.
  - `failure_rate` -- the complement of the above: how often this
    provider's own proposal failed to validate at all (unreachable,
    timed out, or otherwise rejected by `website_validator.py`, which
    collapses all of those into one pass/fail outcome -- see that
    module's own `ValidationOutcome.ok`; this package has no finer
    -grained timeout-vs-4xx signal available without touching validator
    internals, which is out of this sprint's scope).
  - `agreement_rate` -- of every proposal this provider made *for a
    business where at least one other, independent provider also
    proposed something* (i.e. excluding businesses where this was the
    only source), what fraction converged with that other provider on
    the same domain (`identity.ProviderAgreementRecord.status ==
    "agreement"`) rather than disagreeing. A provider that's rarely
    corroborated by an independent second source, when one exists, is a
    materially different (and useful) fact from one that's simply never
    selected.

All four are plain ratios over counters already being incremented (or, in
`agreement_rate`'s case, one new, optional, backward-compatible counter --
see `observe_proposal`'s `agreed_with_peers` parameter) -- no model, no
training, no new data source.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


# A symmetric Beta(2, 2) prior: a provider with zero observed history
# starts at exactly 0.5 (neutral -- neither trusted nor distrusted), and
# a single observation moves the score by a bounded, predictable amount
# rather than snapping straight to 0.0 or 1.0. Fixed and documented, in
# the same style as digital_identity.py's own _CRITICAL_QUALITY_WEIGHT/
# _ADVISORY_QUALITY_WEIGHT -- not a calibrated or learned value.
_PRIOR_AGREEMENTS = 2.0
_PRIOR_DISAGREEMENTS = 2.0


@dataclass
class ProviderReliabilityRecord:
    """One provider's evolving track record within a registry's scope.
    `proposals_agreed` counts proposals whose domain matched whatever
    ended up selected for that business; `proposals_verified` counts
    proposals that independently passed validation, regardless of
    whether they were the one ultimately selected -- two different,
    both-useful questions ("does this provider tend to be RIGHT" vs
    "does this provider tend to find something REAL"). `proposals_with_peers`/
    `proposals_in_agreement` (Sprint 4) add a third: "when this provider
    wasn't the only source, did it tend to agree with the other one" --
    see module docstring's `agreement_rate`."""

    provider: str
    proposals_total: int = 0
    proposals_agreed: int = 0
    proposals_verified: int = 0
    proposals_with_peers: int = 0
    proposals_in_agreement: int = 0

    @property
    def reliability_score(self) -> float:
        return round(
            (self.proposals_agreed + _PRIOR_AGREEMENTS)
            / (self.proposals_total + _PRIOR_AGREEMENTS + _PRIOR_DISAGREEMENTS),
            3,
        )

    @property
    def verified_rate(self) -> float:
        return round(self.proposals_verified / self.proposals_total, 3) if self.proposals_total else 0.0

    # -- Sprint 4: deterministic operational metrics -- see module
    # docstring for exactly what each of these four means and why none of
    # them require a new data source.

    @property
    def selection_success_rate(self) -> float:
        """Plain, unsmoothed version of the ratio `reliability_score`
        Beta-smooths -- the raw observed rate, not shrunk toward the
        neutral prior."""
        return round(self.proposals_agreed / self.proposals_total, 3) if self.proposals_total else 0.0

    @property
    def verification_success_rate(self) -> float:
        """Alias of `verified_rate`, kept under both names: `verified_rate`
        for backward compatibility with every existing caller/test,
        `verification_success_rate` matching the Sprint 4 brief's own
        terminology. Always the same number."""
        return self.verified_rate

    @property
    def failure_rate(self) -> float:
        return round(1.0 - self.verified_rate, 3) if self.proposals_total else 0.0

    @property
    def agreement_rate(self) -> float:
        """Of proposals where an independent second provider also proposed
        something for the same business, what fraction converged with it.
        0.0 (not fabricated as "perfect" or "unknown") when this provider
        has never had a peer to agree or disagree with."""
        return (
            round(self.proposals_in_agreement / self.proposals_with_peers, 3)
            if self.proposals_with_peers
            else 0.0
        )

    def operational_metrics(self) -> Dict[str, float]:
        """All four Sprint 4 operational metrics in one bundle -- what a
        caller (e.g. `identity_resolution_engine.py`) actually reads,
        rather than pulling each property individually."""
        return {
            "selection_success_rate": self.selection_success_rate,
            "verification_success_rate": self.verification_success_rate,
            "failure_rate": self.failure_rate,
            "agreement_rate": self.agreement_rate,
        }

    def explain(self) -> str:
        return (
            f"{self.provider}: {self.proposals_agreed}/{self.proposals_total} proposal(s) agreed with the "
            f"final selection (reliability={self.reliability_score:.2f}, Beta({_PRIOR_AGREEMENTS:.0f},"
            f"{_PRIOR_DISAGREEMENTS:.0f}) prior applied); verification_success_rate="
            f"{self.verification_success_rate:.2f}, failure_rate={self.failure_rate:.2f}, "
            f"agreement_rate={self.agreement_rate:.2f} ({self.proposals_with_peers} proposal(s) had a peer)"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "proposals_total": self.proposals_total,
            "proposals_agreed": self.proposals_agreed,
            "proposals_verified": self.proposals_verified,
            "proposals_with_peers": self.proposals_with_peers,
            "proposals_in_agreement": self.proposals_in_agreement,
            "reliability_score": self.reliability_score,
            "verified_rate": self.verified_rate,
            "selection_success_rate": self.selection_success_rate,
            "verification_success_rate": self.verification_success_rate,
            "failure_rate": self.failure_rate,
            "agreement_rate": self.agreement_rate,
            "explanation": self.explain(),
        }


class ProviderReliabilityRegistry:
    """In-memory, evolving, per-scope registry of `ProviderReliabilityRecord`s
    -- see module docstring for the deliberate, documented reason this is
    the one stateful engine in the package."""

    def __init__(self) -> None:
        self._records: Dict[str, ProviderReliabilityRecord] = {}

    def _record_for(self, provider: str) -> ProviderReliabilityRecord:
        record = self._records.get(provider)
        if record is None:
            record = ProviderReliabilityRecord(provider=provider)
            self._records[provider] = record
        return record

    def observe_proposal(
        self,
        provider: str,
        *,
        verified: bool,
        agreed_with_selection: bool,
        agreed_with_peers: Optional[bool] = None,
    ) -> None:
        """Records one observation: `provider` proposed a domain for one
        business; `verified` is whether that specific proposal passed
        validation; `agreed_with_selection` is whether that proposal's
        domain matched whatever domain ended up selected for that
        business (by any provider). `agreed_with_peers` (Sprint 4,
        optional, defaulted to None for full backward compatibility with
        every pre-Sprint-4 call) is whether it converged with an
        independent second provider that also proposed something for this
        business -- pass None (the default) when there was no such peer to
        agree or disagree with (a single-source business), True/False
        otherwise. See module docstring's `agreement_rate`."""
        if not provider:
            return
        record = self._record_for(provider)
        record.proposals_total += 1
        if verified:
            record.proposals_verified += 1
        if agreed_with_selection:
            record.proposals_agreed += 1
        if agreed_with_peers is not None:
            record.proposals_with_peers += 1
            if agreed_with_peers:
                record.proposals_in_agreement += 1

    def score(self, provider: str) -> float:
        """Current reliability score for `provider` -- 0.5 (the neutral
        prior) if nothing has ever been observed for it, never a
        fabricated 0.0/1.0."""
        if not provider:
            return round(_PRIOR_AGREEMENTS / (_PRIOR_AGREEMENTS + _PRIOR_DISAGREEMENTS), 3)
        return self._record_for(provider).reliability_score

    def record(self, provider: str) -> ProviderReliabilityRecord:
        return self._record_for(provider)

    def snapshot(self) -> Dict[str, ProviderReliabilityRecord]:
        return dict(self._records)

    def to_dict(self) -> Dict[str, Any]:
        return {provider: record.to_dict() for provider, record in self._records.items()}
