"""
Lead scoring service with configurable criteria
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import json

from application.dto.models import CompanyIntelligenceOutput
from core.domain.models.lead import Lead
from core.infrastructure.logging import get_logger

logger = get_logger(__name__)


class ScoringModelType(str, Enum):
    WEIGHTED_LINEAR = "weighted_linear"
    PREDICTIVE = "predictive"
    RULE_BASED = "rule_based"


@dataclass
class ScoringCriteria:
    name: str
    weight: float  # 0.0 to 1.0
    threshold: float  # minimum value to get points
    max_score: float  # maximum score for this criteria
    description: str


@dataclass
class ScoreResult:
    total_score: float  # 0-100
    criteria_scores: Dict[str, float]
    qualification_label: str
    breakdown: Dict[str, Any]


class LeadScoringService:
    """
    Lead scoring service with configurable criteria and multiple scoring models
    """

    def __init__(self):
        # Ordered so "distance" between bands is meaningful (see
        # _evaluate_company_size) -- must match the band labels
        # WaterfallEnricher._bucket_employee_count actually produces.
        self.employee_band_order = ["1-10", "11-50", "51-200", "201-500", "500+"]
        # Configurable, not hardcoded into the evaluation method itself:
        # which bands are the ideal fit. Bands outside this range still
        # earn partial credit based on how far they are from it, instead
        # of the previous all-or-nothing membership check.
        self.preferred_employee_bands = ["11-50", "51-200", "201-500"]

        # Default scoring configuration
        self.default_criteria = [
            ScoringCriteria(
                name="industry_match",
                weight=0.25,
                threshold=0.5,
                max_score=25.0,
                description="Matches preferred industry",
            ),
            ScoringCriteria(
                name="company_size",
                weight=0.20,
                threshold=0.5,
                max_score=20.0,
                description="Company size within preferred range",
            ),
            ScoringCriteria(
                name="email_quality",
                weight=0.15,
                threshold=0.6,
                max_score=15.0,
                description="Email confidence score",
            ),
            ScoringCriteria(
                name="scrape_quality",
                weight=0.15,
                threshold=0.6,
                max_score=15.0,
                description="Scraping confidence score",
            ),
            ScoringCriteria(
                name="enrichment_quality",
                weight=0.15,
                threshold=0.6,
                max_score=15.0,
                description="Enrichment confidence score",
            ),
            ScoringCriteria(
                name="linkedin_presence",
                weight=0.10,
                threshold=0.5,
                max_score=10.0,
                description="Has LinkedIn profile",
            ),
        ]

    def score_lead(
        self,
        lead: Lead,
        custom_criteria: Optional[List[ScoringCriteria]] = None,
        company_intelligence: Optional[CompanyIntelligenceOutput] = None,
    ) -> ScoreResult:
        """
        Score a lead based on configurable criteria.

        `company_intelligence` is the upstream Company Intelligence stage's
        output (see application/agents/company_intelligence_agent.py). It is
        optional and defaults to None so every existing caller keeps working
        unmodified; when provided, the industry_match criterion uses it
        instead of the previous hardcoded industry list (see
        _evaluate_industry_match for why).
        """
        criteria = custom_criteria or self.default_criteria

        criteria_scores = {}
        total_score = 0.0

        for criterion in criteria:
            score = self._evaluate_criterion(lead, criterion, company_intelligence)
            criteria_scores[criterion.name] = score
            total_score += score

        # Normalize to 0-100 scale
        normalized_score = min(total_score, 100.0)

        # Determine qualification label
        qualification_label = self._classify_lead(normalized_score)

        return ScoreResult(
            total_score=normalized_score,
            criteria_scores=criteria_scores,
            qualification_label=qualification_label,
            breakdown={
                "criteria_scores": criteria_scores,
                "raw_score": total_score,
                "normalized_score": normalized_score,
                "qualification_label": qualification_label,
            },
        )

    def _evaluate_criterion(
        self,
        lead: Lead,
        criterion: ScoringCriteria,
        company_intelligence: Optional[CompanyIntelligenceOutput] = None,
    ) -> float:
        """
        Evaluate a single scoring criterion
        """
        if criterion.name == "industry_match":
            return self._evaluate_industry_match(lead, criterion, company_intelligence)
        elif criterion.name == "company_size":
            return self._evaluate_company_size(lead, criterion)
        elif criterion.name == "email_quality":
            return self._evaluate_email_quality(lead, criterion)
        elif criterion.name == "scrape_quality":
            return self._evaluate_scrape_quality(lead, criterion)
        elif criterion.name == "enrichment_quality":
            return self._evaluate_enrichment_quality(lead, criterion)
        elif criterion.name == "linkedin_presence":
            return self._evaluate_linkedin_presence(lead, criterion)
        else:
            logger.warning(f"Unknown scoring criterion: {criterion.name}")
            return 0.0

    def _evaluate_industry_match(
        self,
        lead: Lead,
        criterion: ScoringCriteria,
        company_intelligence: Optional[CompanyIntelligenceOutput] = None,
    ) -> float:
        """
        Evaluate ICP/industry fit using the Company Intelligence stage's
        icp_alignment_score (application/dto/models.py -- an existing field,
        already 0.0-1.0, already computed upstream) instead of matching
        lead.industry against a hardcoded industry list.

        That previous approach was checked against real enrichment output
        and was silently broken, not just philosophically undesirable: the
        upstream enrichment tier produces free-text industry values ("retail",
        "financial services") which rarely match a fixed list of exact
        strings ("E-commerce", "Fintech") even when the lead is a clear fit.
        Reusing icp_alignment_score also gives continuous, partial-credit
        scoring for free, rather than the previous all-or-nothing match.

        Returns 0.0 (unknown, not "poor fit") when company_intelligence
        hasn't been computed for this lead yet -- that stage runs earlier in
        the pipeline (see application/graph/graph_nodes.py), so this should
        only happen if scoring is invoked out of order or standalone.
        """
        if company_intelligence is None:
            return 0.0
        return criterion.max_score * company_intelligence.icp_alignment_score

    def _evaluate_company_size(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate company size criterion: full credit inside the configured
        preferred range, decaying partial credit for each band of distance
        outside it, rather than zero credit for every band not explicitly
        listed. A real run against 26 live sites showed the previous
        all-or-nothing check scoring 0 for 25/26 leads -- including large,
        clearly legitimate organizations (500+ employees) that simply
        weren't on the list -- which is a mis-score, not a conservative one.
        """
        if not lead.employees or lead.employees not in self.employee_band_order:
            return 0.0  # unknown band -- no evidence, not "poor fit"

        band_index = self.employee_band_order.index(lead.employees)
        preferred_indices = [self.employee_band_order.index(b) for b in self.preferred_employee_bands]
        distance = min(abs(band_index - p) for p in preferred_indices)

        if distance == 0:
            return criterion.max_score
        # Each band of distance halves the credit; two or more bands away
        # earns nothing, same floor as before for genuinely poor fits.
        decay = 0.5 ** distance
        return criterion.max_score * decay if distance == 1 else 0.0

    def _evaluate_email_quality(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate contact quality. Prefers email_confidence (unchanged
        behavior when an email exists). Falls back to crediting a known
        phone number at a fixed moderate weight when there's no email --
        phones have no confidence column on Lead to scale by, so this
        mirrors the flat credit _prioritize_contact already gives a
        phone-only contact upstream (see enricher.py), rather than scoring
        the ~80% of real leads with no email at 0 regardless of whether
        they published a phone number.
        """
        if lead.email_confidence >= criterion.threshold:
            return criterion.max_score * (lead.email_confidence / 1.0)
        if not lead.email_confidence and getattr(lead, "phone", None):
            return criterion.max_score * 0.5
        return 0.0

    def _evaluate_scrape_quality(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate scrape quality criterion
        """
        if lead.scrape_confidence >= criterion.threshold:
            return criterion.max_score * (lead.scrape_confidence / 1.0)
        return 0.0

    def _evaluate_enrichment_quality(
        self, lead: Lead, criterion: ScoringCriteria
    ) -> float:
        """
        Evaluate enrichment quality criterion
        """
        if lead.enrichment_confidence >= criterion.threshold:
            return criterion.max_score * (lead.enrichment_confidence / 1.0)
        return 0.0

    def _evaluate_linkedin_presence(
        self, lead: Lead, criterion: ScoringCriteria
    ) -> float:
        """
        Evaluate LinkedIn presence criterion
        """
        if lead.linkedin_url:
            return criterion.max_score
        return 0.0

    def _classify_lead(self, score: float) -> str:
        """
        Classify lead based on score
        """
        if score >= 80:
            return "Hot Lead"
        elif score >= 60:
            return "Warm Lead"
        elif score >= 40:
            return "Cold Lead"
        else:
            return "Disqualified"

    def get_scoring_config(self, organization_id: int) -> List[ScoringCriteria]:
        """
        Get organization-specific scoring configuration
        In a real implementation, this would fetch from database
        """
        # For now, return default configuration
        # In production, this would load from org-specific settings
        return self.default_criteria

    def update_scoring_config(
        self, organization_id: int, criteria: List[ScoringCriteria]
    ) -> bool:
        """
        Update organization-specific scoring configuration
        In a real implementation, this would save to database
        """
        # For now, just validate the criteria
        total_weight = sum(c.weight for c in criteria)
        if abs(total_weight - 1.0) > 0.01:  # Allow small floating point errors
            logger.error(
                f"Scoring criteria weights must sum to 1.0, got {total_weight}"
            )
            return False

        # In production, this would save to database
        logger.info(f"Updated scoring configuration for organization {organization_id}")
        return True

    def calculate_custom_score(
        self, lead_data: Dict[str, Any], custom_weights: Dict[str, float]
    ) -> ScoreResult:
        """
        Calculate score using custom weights
        """
        # Create temporary criteria with custom weights
        temp_criteria = []
        for name, weight in custom_weights.items():
            # Find the original criterion to get other properties
            original = next((c for c in self.default_criteria if c.name == name), None)
            if original:
                temp_criteria.append(
                    ScoringCriteria(
                        name=original.name,
                        weight=weight,
                        threshold=original.threshold,
                        max_score=original.max_score,
                        description=original.description,
                    )
                )

        # Normalize weights to sum to 1.0
        total_weight = sum(c.weight for c in temp_criteria)
        if total_weight > 0:
            for criterion in temp_criteria:
                criterion.weight /= total_weight

        # Create a temporary lead object for evaluation
        from core.domain.models.lead import Lead

        temp_lead = Lead(
            **lead_data
        )  # This is simplified - in reality you'd need proper instantiation

        return self.score_lead(temp_lead, temp_criteria)