"""
Discovery Evaluation — Metrics.

Everything in this module READS values Discovery has already computed
(WebsiteResolution, VerifiedDigitalIdentity, IdentityResolutionResult,
CompetitionResult, DigitalIdentityQuality, ConsensusRecord, ...) and
never recomputes a signal Discovery itself owns. Where a field's exact
source is non-obvious, the extractor function's docstring says which
attribute path it read it from.

Two record shapes:
  - QueryRecord    -- one row per benchmark query
  - BusinessRecord -- one row per business Discovery considered for that
                      query (selected, not-selected, or rejected)

Plus the automatic-audit rules and the run-level aggregate metrics the
report/CSV/chart layers consume. Every threshold below is a fixed,
documented constant (never learned, never tuned on this run's own data)
-- exactly the discipline the Discovery package itself follows for its
own scoring weights.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

# -- Fixed, documented audit thresholds (never learned, never tuned on
# this run's own data) --------------------------------------------------
WEAK_CONFIDENCE_THRESHOLD = 0.5
LOW_STABILITY_THRESHOLD = 0.4
THIN_MARGIN_THRESHOLD = 0.05
WEAK_PROVIDER_AGREEMENT_THRESHOLD = 0.5
CLOSE_RANK_SCORE_DELTA = 5.0  # rank_score points; score_business's own budget is 0-135ish

# Independent, hand-curated list of common aggregator/directory/
# marketplace/social domains, kept DELIBERATELY SEPARATE from
# application.discovery.website_validator.REJECTED_DOMAINS. This is an
# audit safety net, not a duplicate of Discovery's own list: Discovery's
# list is itself the thing being audited (it was previously missing
# tradeindia.com in production -- see website_validator.py's own
# comment), so checking the *final selected website* against a second,
# independently-maintained list is what catches the next gap, rather
# than trusting the same list to grade itself.
_AUDIT_DIRECTORY_MARKETPLACE_DOMAINS = (
    "justdial.com", "indiamart.com", "tradeindia.com", "exportersindia.com",
    "sulekha.com", "alibaba.com", "amazon.", "flipkart.com", "meesho.com",
    "olx.in", "quikr.com", "yellowpages.", "tripadvisor.com", "practo.com",
    "urbancompany.com", "housing.com", "magicbricks.com", "99acres.com",
    "zomato.com", "swiggy.com", "makemytrip.com", "goibibo.com", "agoda.com",
    "booking.com", "trivago.com", "naukri.com", "glassdoor.",
)
_AUDIT_SOCIAL_DOMAINS = (
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "pinterest.com", "threads.net", "tiktok.com",
)


def _domain_of(url: Optional[str]) -> str:
    """Local, dependency-free fallback for extracting a bare domain.
    Prefers `application.discovery.website_validator.domain_of` (the
    real, canonical implementation Discovery itself uses) when the
    application package is importable; falls back to this three-line
    equivalent only so this module's own unit tests can exercise the
    business-record extractors without the full application package on
    the path. Never used to make a Discovery *decision* -- only to read
    a domain string off a URL Discovery already selected."""
    if not url:
        return ""
    try:
        from application.discovery.website_validator import domain_of as _real_domain_of

        return _real_domain_of(url)
    except Exception:
        try:
            netloc = urlparse(url).netloc.lower()
            return netloc[4:] if netloc.startswith("www.") else netloc
        except Exception:
            return ""


def _matches_any(domain: str, patterns) -> bool:
    return bool(domain) and any(p in domain for p in patterns)


# ---------------------------------------------------------------------------
# Record shapes
# ---------------------------------------------------------------------------


@dataclass
class QueryRecord:
    id: str
    query: str
    domain_tag: str
    difficulty_tag: str
    parsed_category: Optional[str] = None
    parsed_location: Optional[str] = None
    requested_limit: Optional[int] = None
    error: Optional[str] = None

    execution_time_ms: int = 0
    total_discovery_time_ms: int = 0
    search_time_ms: int = 0
    resolve_time_ms: int = 0
    dedup_time_ms: int = 0
    ranking_time_ms: int = 0

    provider_count: int = 0
    providers_used: str = ""
    businesses_found: int = 0
    validated_businesses: int = 0
    rejected_businesses: int = 0
    duplicate_businesses: int = 0
    no_website_businesses: int = 0
    validation_failures: int = 0
    resolved_via_fallback_count: int = 0

    winner_confidence: Optional[float] = None
    identity_stability: Optional[float] = None
    competition_margin: Optional[float] = None
    competition_rival_count: int = 0
    provider_agreement: Optional[float] = None
    verification_strength: Optional[float] = None
    final_selected_website: Optional[str] = None

    audit_flags: List[str] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        row = asdict(self)
        row["audit_flags"] = "; ".join(self.audit_flags)
        return row


@dataclass
class BusinessRecord:
    query_id: str
    query: str
    business_name: str
    provider: str
    address: Optional[str]
    category: Optional[str]
    website_candidate: Optional[str]
    final_website: Optional[str]
    website_validated: bool
    validation_reason: Optional[str]
    evidence_count: int
    feature_count: int
    verification_count: int
    confidence: Optional[float]
    identity_stability: Optional[float]
    provider_agreement: Optional[float]
    competition_score: Optional[float]
    rank_score: float
    selection_reason: Optional[str]
    rejected_reason: Optional[str]
    false_positive_flags: str
    outcome: str  # "selected" | "not_selected" | "validation_failed" | "no_website" | "duplicate"
    audit_flags: List[str] = field(default_factory=list)

    def to_row(self) -> Dict[str, Any]:
        row = asdict(self)
        row["audit_flags"] = "; ".join(self.audit_flags)
        return row


# ---------------------------------------------------------------------------
# Business-level extraction (reads DiscoveredBusiness + its optional
# VerifiedDigitalIdentity -- never recomputes anything)
# ---------------------------------------------------------------------------


def _selected_identity_candidate(identity: Any):
    """The IdentityCandidate (application.discovery.organization) that
    corresponds to identity.selected_website, found by domain match
    against identity.identity_resolution.identity_candidates -- this is
    where the structural false-positive risk codes
    (application.discovery.false_positive.py's signals, folded into
    IdentityCandidate.risk during resolution) actually live. Returns
    None whenever there's nothing to match (no identity, no selection,
    or -- defensively -- no matching candidate)."""
    if identity is None or not getattr(identity, "selected_website", None):
        return None
    resolution = getattr(identity, "identity_resolution", None)
    if resolution is None:
        return None
    domain = _domain_of(identity.selected_website)
    for candidate in getattr(resolution, "identity_candidates", ()):
        if getattr(candidate, "normalized_domain", None) == domain or getattr(
            candidate, "registrable_domain", None
        ) == domain:
            return candidate
    return None


def _selected_competition_result(identity: Any):
    """The CompetitionResult (application.discovery.competition) for the
    selected candidate, found by domain match against
    identity.identity_resolution.competition. Falls back to the first
    entry (rank 1 is always the pool leader -- see compete()'s own
    docstring) when no exact domain match is found, and to None when
    there's no competition data at all (e.g. only one candidate, or no
    identity)."""
    if identity is None:
        return None
    resolution = getattr(identity, "identity_resolution", None)
    if resolution is None:
        return None
    competition = getattr(resolution, "competition", ())
    if not competition:
        return None
    domain = _domain_of(getattr(identity, "selected_website", None))
    for result in competition:
        if getattr(result, "domain", None) == domain:
            return result
    return competition[0]


def extract_business_record(
    query_case, business: Any, outcome: str
) -> BusinessRecord:
    """`business` is an application.discovery.dto.DiscoveredBusiness (or
    anything duck-typing candidate/resolution/identity/is_duplicate/
    duplicate_key/rank_score the same way -- the eval framework's own
    tests use a plain stub for this reason)."""
    candidate = business.candidate
    resolution = business.resolution
    identity = getattr(business, "identity", None)

    evidence_count = 0
    feature_count = 0
    verification_count = 0
    confidence = None
    identity_stability = None
    provider_agreement = None
    competition_score = None
    selection_reason = None
    false_positive_flags = ""

    if identity is not None:
        evidence_bundle = getattr(identity, "evidence_bundle", None)
        if evidence_bundle is not None:
            evidence_count = len(evidence_bundle.items)

        feature_store = getattr(identity, "feature_store", None)
        if feature_store is not None:
            feature_count = sum(len(fs.features) for fs in feature_store.all())

        verification_bundle = getattr(identity, "verification_bundle", None)
        if verification_bundle is not None:
            verification_count = len(verification_bundle.results)

        confidence = getattr(identity, "selection_confidence", None)
        quality = getattr(identity, "quality", None)
        if quality is not None:
            identity_stability = quality.identity_stability
        consensus = getattr(identity, "consensus", None)
        if consensus is not None:
            provider_agreement = consensus.provider_consensus
        selection_reason = getattr(identity, "selection_reason", None)

        competition_result = _selected_competition_result(identity)
        if competition_result is not None:
            competition_score = competition_result.relative_confidence

        matched_candidate = _selected_identity_candidate(identity)
        if matched_candidate is not None and getattr(matched_candidate, "risk", None):
            false_positive_flags = "; ".join(matched_candidate.risk)

    if outcome == "duplicate":
        rejected_reason = f"duplicate of '{business.duplicate_key}'" if business.duplicate_key else "duplicate"
    elif outcome in ("validation_failed", "no_website"):
        rejected_reason = resolution.rejection_reason or "no reason recorded"
    else:
        rejected_reason = None

    record = BusinessRecord(
        query_id=query_case.id,
        query=query_case.query,
        business_name=candidate.name,
        provider=candidate.source,
        address=candidate.address,
        category=candidate.category,
        website_candidate=candidate.website,
        final_website=resolution.website,
        website_validated=resolution.validated,
        validation_reason=resolution.rejection_reason if not resolution.validated else "validated",
        evidence_count=evidence_count,
        feature_count=feature_count,
        verification_count=verification_count,
        confidence=confidence,
        identity_stability=identity_stability,
        provider_agreement=provider_agreement,
        competition_score=competition_score,
        rank_score=business.rank_score,
        selection_reason=selection_reason,
        rejected_reason=rejected_reason,
        false_positive_flags=false_positive_flags,
        outcome=outcome,
    )
    record.audit_flags = audit_business(record)
    return record


def audit_business(record: BusinessRecord) -> List[str]:
    """Structural, evidence-backed observations only -- see module
    docstring. Every flag cites a concrete, already-recorded number on
    `record`; nothing here is inferred from page content Discovery never
    fetched (see website_validator.py's own "Discovery never scrapes"
    rule -- this framework respects the same boundary)."""
    flags: List[str] = []
    domain = _domain_of(record.final_website)

    if record.outcome in ("selected", "not_selected"):
        if _matches_any(domain, _AUDIT_DIRECTORY_MARKETPLACE_DOMAINS):
            flags.append("directory_or_marketplace_selected")
        if _matches_any(domain, _AUDIT_SOCIAL_DOMAINS):
            flags.append("social_profile_selected")
        if record.confidence is not None and record.confidence < WEAK_CONFIDENCE_THRESHOLD:
            flags.append("weak_confidence_winner")
        if record.identity_stability is not None and record.identity_stability < LOW_STABILITY_THRESHOLD:
            flags.append("low_identity_stability")
        if record.provider_agreement is not None and record.provider_agreement < WEAK_PROVIDER_AGREEMENT_THRESHOLD:
            flags.append("provider_disagreement")
        if record.false_positive_flags:
            flags.append("structural_false_positive_signal")
    if record.outcome == "no_website":
        flags.append("no_validated_website")
    if record.outcome == "duplicate":
        flags.append("duplicate_within_run")
    return flags


# ---------------------------------------------------------------------------
# Query-level extraction
# ---------------------------------------------------------------------------


def build_query_record(
    query_case,
    *,
    parsed=None,
    candidates: Optional[List[Any]] = None,
    discovered: Optional[List[Any]] = None,
    eligible: Optional[List[Any]] = None,
    rejected: Optional[List[Any]] = None,
    duplicates_removed: int = 0,
    resolved_via_fallback_count: int = 0,
    selected: Optional[List[Any]] = None,
    stage_times_ms: Optional[Dict[str, int]] = None,
    error: Optional[str] = None,
) -> QueryRecord:
    stage_times_ms = stage_times_ms or {}
    record = QueryRecord(
        id=query_case.id,
        query=query_case.query,
        domain_tag=query_case.domain,
        difficulty_tag=query_case.difficulty,
        error=error,
    )
    record.execution_time_ms = stage_times_ms.get("total_ms", 0)
    record.total_discovery_time_ms = stage_times_ms.get("pipeline_ms", record.execution_time_ms)
    record.search_time_ms = stage_times_ms.get("search_ms", 0)
    record.resolve_time_ms = stage_times_ms.get("resolve_ms", 0)
    record.dedup_time_ms = stage_times_ms.get("dedup_ms", 0)
    record.ranking_time_ms = stage_times_ms.get("rank_ms", 0)

    if error is not None:
        record.audit_flags.append("query_parse_or_provider_error")
        return record

    record.parsed_category = parsed.category
    record.parsed_location = parsed.location
    record.requested_limit = parsed.limit

    candidates = candidates or []
    discovered = discovered or []
    eligible = eligible or []
    rejected = rejected or []
    selected = selected or []

    providers = sorted({c.source for c in candidates})
    record.provider_count = len(providers)
    record.providers_used = ", ".join(providers)
    record.businesses_found = len(candidates)
    record.validated_businesses = len(eligible)
    record.duplicate_businesses = sum(1 for b in discovered if b.is_duplicate)
    record.no_website_businesses = sum(1 for b in discovered if not b.resolution.website)
    record.validation_failures = sum(
        1 for b in discovered if b.resolution.website and not b.resolution.validated
    )
    record.rejected_businesses = len(rejected) - record.duplicate_businesses
    record.resolved_via_fallback_count = resolved_via_fallback_count

    if selected:
        winner = selected[0]
        identity = getattr(winner, "identity", None)
        record.final_selected_website = winner.resolution.website
        if identity is not None:
            record.winner_confidence = getattr(identity, "selection_confidence", None)
            quality = getattr(identity, "quality", None)
            if quality is not None:
                record.identity_stability = quality.identity_stability
            consensus = getattr(identity, "consensus", None)
            if consensus is not None:
                record.provider_agreement = consensus.provider_consensus
            vq = getattr(identity, "verification_quality", None)
            if vq is not None:
                record.verification_strength = vq.strength
            competition_result = _selected_competition_result(identity)
            if competition_result is not None:
                record.competition_margin = competition_result.winning_margin
                # compete()'s own docstring: a SOLE candidate has no rival
                # and its winning_margin is defined as exactly 0.0 by
                # convention ("no rival to compete against"), not because
                # it barely won a real contest. Track how many rivals
                # actually existed so audit_query can tell the two apart.
                resolution = getattr(identity, "identity_resolution", None)
                if resolution is not None:
                    record.competition_rival_count = len(getattr(resolution, "competition", ()))

    record.audit_flags = audit_query(record, eligible)
    return record


def audit_query(record: QueryRecord, eligible: List[Any]) -> List[str]:
    flags: List[str] = []
    if record.businesses_found == 0:
        flags.append("no_results")
    elif record.validated_businesses == 0:
        flags.append("no_validated_results")
    if record.winner_confidence is not None and record.winner_confidence < WEAK_CONFIDENCE_THRESHOLD:
        flags.append("weak_confidence_winner")
    if (
        record.competition_margin is not None
        and record.competition_rival_count > 1
        and record.competition_margin < THIN_MARGIN_THRESHOLD
    ):
        flags.append("thin_competition_margin")
    elif record.competition_margin is not None and record.competition_rival_count <= 1:
        flags.append("no_competing_candidate")
    if record.provider_agreement is not None and record.provider_agreement < WEAK_PROVIDER_AGREEMENT_THRESHOLD:
        flags.append("provider_disagreement")
    if record.identity_stability is not None and record.identity_stability < LOW_STABILITY_THRESHOLD:
        flags.append("low_identity_stability")
    if len(eligible) >= 2:
        scores = sorted((b.rank_score for b in eligible), reverse=True)
        if scores[0] - scores[1] <= CLOSE_RANK_SCORE_DELTA:
            flags.append("multiple_strong_candidates")

    manual_review_triggers = {
        "no_validated_results", "weak_confidence_winner", "thin_competition_margin",
        "provider_disagreement", "low_identity_stability", "multiple_strong_candidates",
    }
    if manual_review_triggers & set(flags):
        flags.append("manual_review_recommended")
    return flags


# ---------------------------------------------------------------------------
# Run-level aggregate metrics
# ---------------------------------------------------------------------------


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def aggregate_metrics(
    query_records: List[QueryRecord], business_records: List[BusinessRecord]
) -> Dict[str, Any]:
    ok_queries = [q for q in query_records if q.error is None]
    failed_queries = [q for q in query_records if q.error is not None]
    latencies = [q.execution_time_ms for q in ok_queries]

    total_found = sum(q.businesses_found for q in ok_queries)
    total_validated = sum(q.validated_businesses for q in ok_queries)
    total_rejected = sum(q.rejected_businesses for q in ok_queries)
    total_duplicates = sum(q.duplicate_businesses for q in ok_queries)
    total_no_website = sum(q.no_website_businesses for q in ok_queries)
    total_validation_failures = sum(q.validation_failures for q in ok_queries)

    confidences = [q.winner_confidence for q in ok_queries if q.winner_confidence is not None]
    stabilities = [q.identity_stability for q in ok_queries if q.identity_stability is not None]
    margins = [q.competition_margin for q in ok_queries if q.competition_margin is not None]
    agreements = [q.provider_agreement for q in ok_queries if q.provider_agreement is not None]

    provider_agreement_queries = [q for q in ok_queries if q.provider_agreement is not None]
    provider_agreement_rate = (
        sum(1 for q in provider_agreement_queries if q.provider_agreement >= WEAK_PROVIDER_AGREEMENT_THRESHOLD)
        / len(provider_agreement_queries)
        if provider_agreement_queries
        else 0.0
    )

    # Provider reliability, per provider, averaged across every business
    # record where a provider contributed a candidate -- counts sourced
    # purely from BusinessRecord.provider (== BusinessCandidate.source),
    # never re-derived.
    provider_candidate_counts: Dict[str, int] = {}
    for b in business_records:
        provider_candidate_counts[b.provider] = provider_candidate_counts.get(b.provider, 0) + 1

    no_competing_candidate_queries = [q.id for q in ok_queries if "no_competing_candidate" in q.audit_flags]
    no_results_queries = [q.id for q in ok_queries if "no_results" in q.audit_flags]
    no_validated_queries = [q.id for q in ok_queries if "no_validated_results" in q.audit_flags]
    multi_strong_queries = [q.id for q in ok_queries if "multiple_strong_candidates" in q.audit_flags]
    manual_review_queries = [q.id for q in ok_queries if "manual_review_recommended" in q.audit_flags]

    directory_flags = [b for b in business_records if "directory_or_marketplace_selected" in b.audit_flags]
    social_flags = [b for b in business_records if "social_profile_selected" in b.audit_flags]
    fp_flags = [b for b in business_records if "structural_false_positive_signal" in b.audit_flags]

    # Discovery health score: unweighted mean of five 0-1 rates, each
    # already computed above -- no new heuristic, just a rollup, exactly
    # like digital_identity.assess_digital_identity_quality's own
    # "unweighted mean of already-computed components" pattern.
    validation_success_rate = total_validated / total_found if total_found else 0.0
    resolution_success_rate = (
        (total_found - total_no_website) / total_found if total_found else 0.0
    )
    avg_confidence = statistics.fmean(confidences) if confidences else 0.0
    avg_stability = statistics.fmean(stabilities) if stabilities else 0.0
    parse_success_rate = len(ok_queries) / len(query_records) if query_records else 0.0
    health_components = [
        validation_success_rate, resolution_success_rate, avg_confidence,
        avg_stability, parse_success_rate,
    ]
    discovery_health_score = round(statistics.fmean(health_components) * 100, 1)

    return {
        "total_queries": len(query_records),
        "ok_queries": len(ok_queries),
        "failed_queries": len(failed_queries),
        "failed_query_ids": [q.id for q in failed_queries],
        "parse_success_rate": round(parse_success_rate, 4),

        "avg_latency_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        "median_latency_ms": round(statistics.median(latencies), 1) if latencies else 0.0,
        "p95_latency_ms": round(_percentile(latencies, 0.95), 1),
        "p99_latency_ms": round(_percentile(latencies, 0.99), 1),

        "total_businesses_found": total_found,
        "total_validated": total_validated,
        "total_rejected": total_rejected,
        "total_duplicates": total_duplicates,
        "total_no_website": total_no_website,
        "total_validation_failures": total_validation_failures,
        "validation_success_rate": round(validation_success_rate, 4),
        "website_resolution_success_rate": round(resolution_success_rate, 4),
        "duplicate_rate": round(total_duplicates / total_found, 4) if total_found else 0.0,
        "validation_failure_rate": round(total_validation_failures / total_found, 4) if total_found else 0.0,

        "provider_agreement_rate": round(provider_agreement_rate, 4),
        "avg_confidence": round(avg_confidence, 4),
        "avg_identity_stability": round(avg_stability, 4),
        "avg_competition_margin": round(statistics.fmean(margins), 4) if margins else 0.0,
        "avg_provider_agreement": round(statistics.fmean(agreements), 4) if agreements else 0.0,

        "provider_candidate_counts": provider_candidate_counts,

        "queries_no_competing_candidate": no_competing_candidate_queries,
        "queries_no_results": no_results_queries,
        "queries_no_validated_results": no_validated_queries,
        "queries_multiple_strong_candidates": multi_strong_queries,
        "queries_manual_review": manual_review_queries,

        "directory_marketplace_selected_count": len(directory_flags),
        "social_profile_selected_count": len(social_flags),
        "structural_false_positive_count": len(fp_flags),

        "discovery_health_score": discovery_health_score,
    }
