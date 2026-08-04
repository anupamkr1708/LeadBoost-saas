"""
Candidate Competition, Evidence Corroboration & Conflict Resolution
(Sprint 3).

Three components that all answer variations of one question -- "how much
should knowing about the OTHER candidates change what I think of THIS
one?" -- so they live together:

  - Candidate Competition (component 5): candidates are scored relative
    to each other, not only independently.
  - Evidence Corroboration (component 7): avoids treating several
    same-type observations as if they were as informative as several
    genuinely different KINDS of observation.
  - Conflict Resolution (component 8): contradictions across candidates
    are represented explicitly, as their own explainable object, rather
    than silently folded into a single number.

Layering discipline: every function here reads already-computed
`organization.IdentityCandidate`/`OrganizationGroup`/
`OrganizationRelationship` and `identity.ProviderAgreementRecord` --
no I/O, no new evidence invented. Every adjustment is a small, fixed,
documented, bounded nudge (mirroring evidence.py's own HTTPS_WEIGHT/
CANONICAL_URL_WEIGHT philosophy: "kept small on purpose so it can never
be decisive on its own") -- this module can re-rank *how confident* the
system is about the pool it already has, it can never invent a new
candidate or flip a verification pass/fail.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from application.discovery import features as features_model
from application.discovery import identity as identity_model
from application.discovery.organization import IdentityCandidate, OrganizationGroup, OrganizationRelationship, RelationshipType


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ---------------------------------------------------------------------------
# Candidate Competition
# ---------------------------------------------------------------------------

# A decisive lead over every rival is genuine extra certainty; a razor
# -thin lead is genuine extra doubt -- but per every other weight in this
# package, the adjustment stays small enough that it can only ever
# refine an already-computed confidence, never override it (a candidate
# at 0.0 base confidence can never be pushed to a competitive win by this
# alone, and a candidate at 1.0 can lose at most this much for having
# rivals).
_COMPETITION_ADJUSTMENT_WEIGHT = 0.15


@dataclass(frozen=True)
class CompetitionResult:
    """One candidate's outcome from being compared against every other
    candidate in the same pool. `relative_confidence` is the number a
    caller should actually treat as "how much to trust this pick", not
    `base_confidence` alone -- see `compete`'s own docstring.

    Sprint 4 additions -- explicit winning/losing margin and evidence
    support, per that sprint's "improve candidate competition" goal:
    `margin`'s sign already carried this information (positive for the
    leader, non-positive for everyone else), but a caller had to check
    `rank`/the sign itself to know which question it was answering.
    `winning_margin`/`losing_margin` are the same underlying number,
    split into two always-non-negative, unambiguously-named fields (only
    one of the two is ever nonzero for a given candidate) so "how
    decisively did it win" and "how far did it lose by" never require
    inspecting a sign. `evidence_support` is `evidence_diversity`
    (below) computed for THIS specific candidate -- previously only ever
    computed once, for the selected candidate, at the
    `IdentityResolutionResult` level; now available per-candidate,
    right alongside the rest of its competitive standing."""

    domain: str
    rank: int
    base_confidence: float
    relative_confidence: float
    margin: float
    adjustment: float
    reason: str
    winning_margin: float = 0.0
    losing_margin: float = 0.0
    evidence_support: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "rank": self.rank,
            "base_confidence": self.base_confidence,
            "relative_confidence": self.relative_confidence,
            "margin": self.margin,
            "adjustment": self.adjustment,
            "reason": self.reason,
            "winning_margin": self.winning_margin,
            "losing_margin": self.losing_margin,
            "evidence_support": self.evidence_support,
        }


def compete(candidates: List[IdentityCandidate]) -> List[CompetitionResult]:
    """Deterministic comparison, per the brief's "Instead of assigning
    one confidence independently... every candidate should gain or lose
    confidence relative to competing candidates. Explain WHY.":

      - ranks the pool best-first by (confidence, identity_quality,
        provider count) -- a fixed, documented tie-break, never random
      - the leader's `margin` is its lead over the runner-up (>= 0); a
        decisive lead earns a small, bounded confidence bonus
      - every other candidate's `margin` is how far it trails the leader
        (<= 0); trailing a strong leader earns a small, bounded penalty
      - a SOLE candidate has no rival to be compared against at all --
        its relative_confidence is its own base_confidence, unchanged,
        and this is reported explicitly rather than silently treated
        like a zero-margin win
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: (-c.confidence, -c.identity_quality, -len(c.provider)))
    n = len(ordered)
    results: List[CompetitionResult] = []
    for i, candidate in enumerate(ordered):
        rank = i + 1
        support = evidence_diversity(candidate)
        if n == 1:
            results.append(
                CompetitionResult(
                    domain=candidate.domain,
                    rank=rank,
                    base_confidence=candidate.confidence,
                    relative_confidence=candidate.confidence,
                    margin=0.0,
                    adjustment=0.0,
                    reason=f"{candidate.domain} is the only candidate proposed -- no rival to compete against",
                    winning_margin=0.0,
                    losing_margin=0.0,
                    evidence_support=support,
                )
            )
            continue
        if rank == 1:
            margin = round(candidate.confidence - ordered[1].confidence, 3)
            adjustment = round(margin * _COMPETITION_ADJUSTMENT_WEIGHT, 4)
            reason = f"leads by a margin of {margin:.2f} confidence over the next-best candidate ({ordered[1].domain})"
            winning_margin, losing_margin = round(max(margin, 0.0), 3), 0.0
        else:
            margin = round(candidate.confidence - ordered[0].confidence, 3)
            adjustment = round(margin * _COMPETITION_ADJUSTMENT_WEIGHT, 4)
            reason = f"trails the leading candidate ({ordered[0].domain}) by {abs(margin):.2f} confidence"
            winning_margin, losing_margin = 0.0, round(abs(margin), 3)
        relative = round(_clamp(candidate.confidence + adjustment, 0.0, 1.0), 3)
        results.append(
            CompetitionResult(
                domain=candidate.domain,
                rank=rank,
                base_confidence=candidate.confidence,
                relative_confidence=relative,
                margin=margin,
                adjustment=adjustment,
                reason=reason,
                winning_margin=winning_margin,
                losing_margin=losing_margin,
                evidence_support=support,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Evidence Corroboration (diversity, not volume)
# ---------------------------------------------------------------------------


def evidence_diversity(candidate: IdentityCandidate) -> float:
    """How many DISTINCT kinds of observation support this candidate,
    normalized against the same documented ceiling
    `features._MAX_OBSERVABLE_EVIDENCE_TYPES` already defines --
    deliberately different from `features.EVIDENCE_DENSITY`, which counts
    raw evidence *items*. The brief's own example is exactly this gap:
    "Three providers returning exactly the same domain != three
    independent pieces of evidence" -- three agreeing providers add
    (up to) three PROVIDER_AGREEMENT evidence items (one per additional
    agreeing provider, see evidence.provider_agreement_evidence), all of
    the SAME EvidenceType, describing one underlying fact (these
    providers converged). Counting distinct *types* instead of raw item
    count means that kind of repetition can never, by itself, read as
    more diverse than it actually is -- diversity can only rise by
    observing a genuinely different KIND of signal (brand match,
    location match, reachability, ...), never by observing the same kind
    more than once."""
    if candidate.supporting_evidence is None:
        return 0.0
    distinct_types = {item.type for item in candidate.supporting_evidence.items}
    ceiling = features_model._MAX_OBSERVABLE_EVIDENCE_TYPES
    return round(min(len(distinct_types) / ceiling, 1.0), 3) if ceiling else 0.0


# ---------------------------------------------------------------------------
# Conflict Resolution
# ---------------------------------------------------------------------------

# Fixed, documented severity weights -- summed and capped at 1.0, in the
# same "point budget" style evidence.py's own confidence weights use.
# None of these gate a decision; conflict_score is purely diagnostic,
# exactly like digital_identity.RiskAssessment.risk_level.
_CONFLICT_WEIGHTS = {
    "provider_disagreement": 0.40,
    "multiple_organizations_proposed": 0.20,
    "close_competitive_margin": 0.25,
    "differing_identity_quality": 0.15,
    "conflicting_redirect": 0.30,
}

# A leader/runner-up margin below this (on the same 0.0-1.0 confidence
# scale every other threshold in this package uses) is "close" -- close
# enough that the pool contains a genuinely plausible alternative to the
# one actually selected.
_CLOSE_MARGIN_THRESHOLD = 0.10

# A gap this large between the top two candidates' identity_quality is
# worth flagging on its own -- the leader may be winning on raw
# confidence (e.g. reachability/HTTPS) while a rival's identity actually
# matches the business better.
_IDENTITY_QUALITY_GAP_THRESHOLD = 0.25


@dataclass(frozen=True)
class ConflictRecord:
    code: str
    description: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "description": self.description, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class ConflictAssessment:
    """The complete set of conflicts detected across one business's
    candidate pool, plus a rolled-up `conflict_score`. An empty
    `conflicts` tuple (`conflict_score=0.0`) is itself meaningful -- a
    clean bill, not an omission, exactly like `RiskAssessment` before
    it."""

    conflicts: Tuple[ConflictRecord, ...]
    conflict_score: float

    def to_dict(self) -> Dict[str, Any]:
        return {"conflict_score": self.conflict_score, "conflicts": [c.to_dict() for c in self.conflicts]}


def empty_conflict_assessment() -> ConflictAssessment:
    return ConflictAssessment(conflicts=(), conflict_score=0.0)


def build_conflict_assessment(conflicts: List[ConflictRecord]) -> ConflictAssessment:
    """The one place a list of already-detected ConflictRecords is rolled
    up into a `conflict_score` -- shared by `assess_conflicts` and by any
    caller (see `identity_resolution_engine.py`) that also folds in
    `identity_quality_gap_conflict`'s result, so both code paths use
    exactly the same scoring arithmetic."""
    if not conflicts:
        return empty_conflict_assessment()
    score = round(min(1.0, sum(_CONFLICT_WEIGHTS.get(c.code, 0.10) for c in conflicts)), 3)
    return ConflictAssessment(conflicts=tuple(conflicts), conflict_score=score)


def assess_conflicts(

    *,
    provider_agreement: "identity_model.ProviderAgreementRecord",
    organization_groups: List[OrganizationGroup],
    relationships: List[OrganizationRelationship],
    competition: List[CompetitionResult],
) -> ConflictAssessment:
    """Pure function covering every contradiction example the brief
    names: provider disagreement, different organizations proposed,
    different canonical domains among top competitors (a close
    competitive margin), conflicting redirects, and different identity
    quality across the leading candidates. Never discards a conflict
    silently -- every one detected becomes its own `ConflictRecord`."""
    conflicts: List[ConflictRecord] = []

    if provider_agreement.status == "disagreement":
        conflicts.append(
            ConflictRecord(
                code="provider_disagreement",
                description="independent providers proposed different domains for this business",
                evidence=(provider_agreement.explain(),),
            )
        )

    if len(organization_groups) > 1:
        conflicts.append(
            ConflictRecord(
                code="multiple_organizations_proposed",
                description=f"{len(organization_groups)} distinct organizations (by registrable domain) were proposed as candidates",
                evidence=tuple(g.registrable_domain for g in organization_groups),
            )
        )

    if len(competition) > 1:
        leader, runner_up = competition[0], competition[1]
        if abs(leader.margin) < _CLOSE_MARGIN_THRESHOLD:
            conflicts.append(
                ConflictRecord(
                    code="close_competitive_margin",
                    description=(
                        f"the leading candidate ({leader.domain}) only leads the runner-up "
                        f"({runner_up.domain}) by {leader.margin:.2f} confidence"
                    ),
                    evidence=(leader.reason, runner_up.reason),
                )
            )

    for relationship in relationships:
        if relationship.relationship_type is RelationshipType.REDIRECT:
            conflicts.append(
                ConflictRecord(
                    code="conflicting_redirect",
                    description=f"{relationship.from_domain} and {relationship.to_domain} conflict via a redirect relationship",
                    evidence=relationship.evidence,
                )
            )

    return build_conflict_assessment(conflicts)


def identity_quality_gap_conflict(candidates_by_rank: List[IdentityCandidate]) -> Optional[ConflictRecord]:
    """Separate from `assess_conflicts` because it needs the original
    IdentityCandidates (for `identity_quality`), not just their
    CompetitionResults -- callers that have both should include this in
    the list passed to a second `assess_conflicts`-style rollup, or check
    it directly. Kept as its own function rather than folded silently
    into `assess_conflicts` so the "different identity quality" example
    from the brief is independently visible and independently testable."""
    if len(candidates_by_rank) < 2:
        return None
    leader, runner_up = candidates_by_rank[0], candidates_by_rank[1]
    gap = round(leader.identity_quality - runner_up.identity_quality, 3)
    if abs(gap) < _IDENTITY_QUALITY_GAP_THRESHOLD:
        return None
    return ConflictRecord(
        code="differing_identity_quality",
        description=(
            f"the leading candidate ({leader.domain}, identity_quality={leader.identity_quality:.2f}) and the "
            f"runner-up ({runner_up.domain}, identity_quality={runner_up.identity_quality:.2f}) differ by {abs(gap):.2f}"
        ),
        evidence=(f"leader identity_quality={leader.identity_quality:.3f}", f"runner_up identity_quality={runner_up.identity_quality:.3f}"),
    )
