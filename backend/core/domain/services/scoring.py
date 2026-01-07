"""
Lead scoring service with configurable criteria
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum
import json

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
        self, lead: Lead, custom_criteria: Optional[List[ScoringCriteria]] = None
    ) -> ScoreResult:
        """
        Score a lead based on configurable criteria
        """
        criteria = custom_criteria or self.default_criteria

        criteria_scores = {}
        total_score = 0.0

        for criterion in criteria:
            score = self._evaluate_criterion(lead, criterion)
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

    def _evaluate_criterion(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate a single scoring criterion
        """
        if criterion.name == "industry_match":
            return self._evaluate_industry_match(lead, criterion)
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

    def _evaluate_industry_match(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate industry match criterion
        """
        preferred_industries = [
            "Software",
            "SaaS",
            "Technology",
            "Fintech",
            "Healthcare",
            "E-commerce",
            "AI",
            "Data",
        ]

        if lead.industry and lead.industry in preferred_industries:
            return criterion.max_score
        return 0.0

    def _evaluate_company_size(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate company size criterion
        """
        preferred_sizes = ["11-50", "51-200", "201-500"]

        if lead.employees and lead.employees in preferred_sizes:
            return criterion.max_score
        return 0.0

    def _evaluate_email_quality(self, lead: Lead, criterion: ScoringCriteria) -> float:
        """
        Evaluate email quality criterion
        """
        if lead.email_confidence >= criterion.threshold:
            return criterion.max_score * (lead.email_confidence / 1.0)
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
