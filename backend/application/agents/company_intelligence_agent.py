"""
Company Intelligence Agent.

Input:  an enriched lead (via LeadContext)
Output: CompanyIntelligenceOutput -- structured intelligence about the
        company, its website, industry, technology, market position, pain
        points, growth indicators, and ICP alignment.

Responsibility boundary: analysis only. Never generates outreach copy --
that is the Messaging Agent's job.

Makes at most one LLM call. Falls back to a deterministic, data-availability
-based heuristic when the LLM is unavailable or the call fails, so this
stage never blocks the pipeline.
"""

from typing import Any, Dict, List

from application.agents.base import BaseAgent
from application.dto.models import CompanyIntelligenceOutput
from application.explainability.explainer import (
    deterministic_explanation,
    explanation_from_llm_payload,
)
from application.prompts.registry import get_prompt_registry
from application.services.llm_provider import is_llm_available, safe_invoke_json
from application.state.lead_state import LeadContext
from core.infrastructure.logging import get_logger

logger = get_logger("application.agents.company_intelligence")

_TECH_SIGNAL_KEYWORDS = {
    "React": ["react", "reactjs"],
    "WordPress": ["wordpress", "wp-content"],
    "Shopify": ["shopify", "myshopify"],
    "HubSpot": ["hubspot", "hs-scripts"],
    "Salesforce": ["salesforce"],
    "AWS": ["amazonaws", "aws"],
    "Cloud-native SaaS": ["saas", "cloud platform", "api-first"],
}

_PAIN_POINT_KEYWORDS = {
    "Scaling operations": ["scaling", "growing pains", "operational challenges"],
    "Manual/legacy processes": ["manual process", "spreadsheet", "legacy system"],
    "Lead generation": ["lead generation", "pipeline", "prospecting"],
    "Hiring/talent": ["hiring", "talent shortage", "staffing"],
}

_GROWTH_KEYWORDS = {
    "Recent funding": ["raised", "funding round", "series a", "series b", "seed round"],
    "Hiring expansion": ["we're hiring", "join our team", "open positions", "careers"],
    "New product launch": ["launching", "new product", "now available", "introducing"],
    "Expansion": ["expanding", "new office", "new market"],
}


class CompanyIntelligenceAgent(BaseAgent):
    name = "company_intelligence_agent"

    def run(self, context: LeadContext, allow_llm: bool = True) -> CompanyIntelligenceOutput:
        if allow_llm and is_llm_available():
            llm_result = self._analyze_with_llm(context)
            if llm_result is not None:
                return llm_result
            logger.info(
                "Company Intelligence: LLM call unavailable/failed, using heuristic fallback",
            )

        return self._analyze_heuristically(context)

    # -- LLM path -------------------------------------------------------

    def _analyze_with_llm(self, context: LeadContext) -> "CompanyIntelligenceOutput | None":
        registry = get_prompt_registry()
        try:
            messages = registry.render(
                "company_intelligence",
                company_name=context.company_name or "Unknown",
                website=context.website,
                industry=context.industry or "Unknown",
                employees=context.employees or "Unknown",
                about_text=(context.about_text or "")[:1000],
                website_content=context.scraped_data.get("text_content", "")[:2000],
            )
        except Exception as e:
            logger.warning(f"Failed to render company_intelligence prompt: {e}")
            return None

        payload, retry_count = safe_invoke_json(
            messages,
            inputs={
                "company_name": context.company_name or "Unknown",
                "website": context.website,
                "industry": context.industry or "Unknown",
                "employees": context.employees or "Unknown",
                "about_text": (context.about_text or "")[:1000],
                "website_content": context.scraped_data.get("text_content", "")[:2000],
            },
            temperature=0.1,
            max_tokens=700,
        )
        if payload is None:
            return None

        try:
            explanation = explanation_from_llm_payload(payload)
            resolved_version = registry.get("company_intelligence").version
            return CompanyIntelligenceOutput(
                industry_analysis=payload.get("industry_analysis"),
                website_quality=payload.get("website_quality"),
                technology_signals=payload.get("technology_signals") or [],
                market_position=payload.get("market_position"),
                pain_points=payload.get("pain_points") or [],
                growth_indicators=payload.get("growth_indicators") or [],
                icp_alignment_score=float(payload.get("icp_alignment_score", 0.0) or 0.0),
                explanation=explanation,
                source="llm",
                prompt_name="company_intelligence",
                prompt_version=resolved_version,
                retry_count=retry_count,
            )
        except Exception as e:
            logger.warning(f"Failed to parse company_intelligence LLM payload: {e}")
            return None

    # -- Deterministic fallback -------------------------------------------

    def _analyze_heuristically(self, context: LeadContext) -> CompanyIntelligenceOutput:
        text = " ".join(
            filter(
                None,
                [
                    context.about_text,
                    context.scraped_data.get("text_content"),
                    context.scraped_data.get("description"),
                ],
            )
        ).lower()

        tech_signals = self._match_keywords(text, _TECH_SIGNAL_KEYWORDS)
        pain_points = self._match_keywords(text, _PAIN_POINT_KEYWORDS)
        growth_indicators = self._match_keywords(text, _GROWTH_KEYWORDS)

        # Data-completeness proxy for ICP alignment (kept intentionally
        # distinct from LeadScoringService's own industry-preference list,
        # to avoid duplicating that business logic here).
        completeness_signals = [
            bool(context.industry),
            bool(context.employees),
            bool(context.email or context.scraped_data.get("email")),
            bool(context.linkedin_url or context.scraped_data.get("linkedin_url")),
        ]
        icp_score = round(sum(completeness_signals) / len(completeness_signals), 2)

        evidence = []
        if context.industry:
            evidence.append(f"Industry recorded as {context.industry}")
        if tech_signals:
            evidence.append(f"Detected technology signals: {', '.join(tech_signals)}")
        if growth_indicators:
            evidence.append(f"Detected growth signals: {', '.join(growth_indicators)}")

        explanation = deterministic_explanation(
            reasoning=(
                "Heuristic keyword analysis of available website/about text; "
                "the LLM path was unavailable for this run."
            ),
            evidence=evidence or ["Insufficient text data for keyword signals"],
        )

        return CompanyIntelligenceOutput(
            industry_analysis=context.industry,
            website_quality="unknown" if not text else "has_content",
            technology_signals=tech_signals,
            market_position=None,
            pain_points=pain_points,
            growth_indicators=growth_indicators,
            icp_alignment_score=icp_score,
            explanation=explanation,
            source="heuristic",
        )

    @staticmethod
    def _match_keywords(text: str, keyword_map: Dict[str, List[str]]) -> List[str]:
        matches = []
        for label, keywords in keyword_map.items():
            if any(kw in text for kw in keywords):
                matches.append(label)
        return matches
