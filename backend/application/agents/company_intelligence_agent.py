"""
Company Intelligence Agent.

Input:  an enriched lead (via LeadContext)
Output: CompanyIntelligenceOutput -- structured intelligence about the
        company, its website, industry, technology, market position, pain
        points, growth indicators, and ICP alignment.

Responsibility boundary: analysis only. Never generates outreach copy --
that is the Messaging Agent's job. Also never extracts -- every fact this
agent reports traces back to something the scraper/normalizer/enrichment
stages already found (see core/infrastructure/enrichment/enricher.py's
`_deterministic_enrichment`, whose exact output shape `_gather_evidence`
below reads). This agent's job is to reason over and surface that
evidence, never to re-derive it from raw text.

Makes at most one LLM call. Falls back to a deterministic, evidence-
completeness-based heuristic when the LLM is unavailable or the call
fails, so this stage never blocks the pipeline. Neither path does keyword
or regex matching against page text: there is no reliable way to infer a
business fact (a technology, a pain point, a growth signal) from isolated
words without a real risk of confidently reporting something the evidence
doesn't actually support, which is exactly what "no unsupported
conclusions" rules out.
"""

from typing import Any, Dict, List, Optional

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
from core.infrastructure.normalization.normalizer import normalize_scraped_fields

logger = get_logger("application.agents.company_intelligence")


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

    # -- Evidence gathering (shared by both paths) ---------------------------

    @staticmethod
    def _gather_evidence(context: LeadContext) -> Dict[str, Any]:
        """Pulls whatever canonical evidence is actually available for this
        lead. Prefers `context.enriched_data` (the WaterfallEnricher's
        merged output -- richer, confidence-scored, already reasoned over
        JSON-LD/contact evidence) and falls back to `context.scraped_data`
        (the scraper's own canonical fields) for the cases enrichment
        didn't run at all, e.g. AI features disabled for this organization
        (see application/graph/graph_nodes.py's `enrich` stage, which
        returns None in that case). Every value returned here traces back
        to a real extracted fact -- nothing is guessed from text.

        Also re-derives the normalizer's output from context.scraped_data
        via normalize_scraped_fields -- the SAME pure function already
        used earlier in the pipeline (imported, not reimplemented; no
        HTML inspection, no keyword matching, nothing re-extracted). This
        recovers fields that WaterfallEnricher computes internally but
        never forwards into EnrichmentResult.data: legal_name, structured
        address/headquarters, the full multi-platform social_profiles
        dict, and categorized+confidence-scored emails/phones. Cheap
        (sub-millisecond, pure dict transformation -- see
        pipeline_metrics.json's normalize stage latency) and side-effect
        free, so recomputing it here is negligible overhead for evidence
        that would otherwise be silently unavailable to this agent.
        """
        enriched = context.enriched_data or {}
        scraped = context.scraped_data or {}
        normalized = normalize_scraped_fields(scraped) if scraped else {}

        offerings = enriched.get("offerings") or {}
        social_profiles = normalized.get("social_profiles") or {}
        if not social_profiles:
            # Fallback for the no-scraped_data-at-all case where
            # normalize_scraped_fields had nothing to work with.
            social_profiles = {
                key: scraped.get(key)
                for key in ("linkedin_url", "twitter_url", "facebook_url", "instagram_url", "youtube_url")
                if scraped.get(key)
            }

        return {
            "business_type": enriched.get("business_type"),  # {value, schema_type, confidence, source}
            "technologies": enriched.get("technologies") or scraped.get("technologies") or [],
            "operating_regions": enriched.get("operating_regions") or [],
            "offerings": offerings,
            "primary_contact": enriched.get("primary_contact"),
            "social_profiles": social_profiles,
            "description": enriched.get("description") or scraped.get("description"),
            "founded_year": enriched.get("founded_year"),
            "employees": enriched.get("employees"),
            "revenue_band": enriched.get("revenue_band"),
            "contact_name": enriched.get("contact_name"),
            "contact_title": enriched.get("contact_title"),
            "legal_name": normalized.get("legal_name"),
            "brand_name": normalized.get("brand_name"),
            "address": normalized.get("address"),
            "emails": normalized.get("emails") or [],
            "phones": normalized.get("phones") or [],
        }

    @staticmethod
    def _evidence_facts(ev: Dict[str, Any]) -> List[str]:
        """Turns the gathered evidence into short, human-readable facts --
        used both as the heuristic path's explanation.evidence and as the
        structured summary handed to the LLM, so the two paths never
        describe the same underlying facts differently."""
        facts: List[str] = []
        business_type = ev["business_type"]
        if business_type:
            facts.append(
                f"Organization type: {business_type.get('value')} "
                f"(confidence {business_type.get('confidence', 0):.2f}, "
                f"source: {business_type.get('source', 'unknown')})"
            )
        if ev.get("legal_name") and ev.get("legal_name") != ev.get("brand_name"):
            facts.append(f"Legal entity name: {ev['legal_name']} (brand: {ev.get('brand_name')})")
        if ev.get("founded_year"):
            facts.append(f"Founded: {ev['founded_year']}")
        if ev.get("employees"):
            revenue_note = f", est. revenue {ev['revenue_band']}" if ev.get("revenue_band") else ""
            facts.append(f"Employee band: {ev['employees']}{revenue_note}")
        if ev["technologies"]:
            facts.append(
                f"{len(ev['technologies'])} technology signal(s) detected: "
                f"{', '.join(ev['technologies'])}"
            )
        if ev["operating_regions"]:
            facts.append(
                f"Operates in {len(ev['operating_regions'])} region(s): "
                f"{', '.join(ev['operating_regions'])}"
            )
        address = ev.get("address")
        if address and address.get("formatted"):
            facts.append(f"Headquarters/address on record: {address['formatted']}")
        offering_count = len(ev["offerings"].get("products") or []) + len(
            ev["offerings"].get("services") or []
        )
        if offering_count:
            facts.append(f"{offering_count} product/service offering(s) identified")
        contact_channels = []
        if ev.get("emails"):
            categories = sorted({e.get("category", "general") for e in ev["emails"] if isinstance(e, dict)})
            contact_channels.append(f"{len(ev['emails'])} email(s) ({', '.join(categories)})")
        if ev.get("phones"):
            contact_channels.append(f"{len(ev['phones'])} phone number(s)")
        if contact_channels:
            facts.append("Contact channels found: " + "; ".join(contact_channels))
        if ev.get("contact_name"):
            title_note = f", {ev['contact_title']}" if ev.get("contact_title") else ""
            facts.append(f"Named contact identified: {ev['contact_name']}{title_note}")
        if ev.get("social_profiles"):
            platforms = sorted(k.replace("_url", "") for k in ev["social_profiles"].keys())
            facts.append(f"Social presence on {len(platforms)} platform(s): {', '.join(platforms)}")
        return facts

    # -- LLM path -------------------------------------------------------

    def _analyze_with_llm(self, context: LeadContext) -> "CompanyIntelligenceOutput | None":
        ev = self._gather_evidence(context)
        evidence_summary = "\n".join(self._evidence_facts(ev)) or "No structured evidence available."

        registry = get_prompt_registry()
        prompt_inputs = {
            "company_name": context.company_name or "Unknown",
            "website": context.website,
            "industry": context.industry or "Unknown",
            "employees": context.employees or "Unknown",
            "about_text": (context.about_text or ev["description"] or "")[:1000],
            # Structured evidence, not raw page/HTML text -- the LLM reasons
            # over facts the scraper/normalizer/enricher already extracted
            # instead of re-deriving them from website copy. NOTE: the
            # registered "company_intelligence" prompt template must accept
            # an `evidence_summary` variable for this to take effect; it
            # previously took `website_content` instead.
            "evidence_summary": evidence_summary,
        }
        try:
            messages = registry.render("company_intelligence", **prompt_inputs)
        except Exception as e:
            logger.warning(f"Failed to render company_intelligence prompt: {e}")
            return None

        payload, retry_count = safe_invoke_json(
            messages,
            inputs=prompt_inputs,
            temperature=0.1,
            max_tokens=700,
        )
        if payload is None:
            return None

        try:
            explanation = explanation_from_llm_payload(payload)
            resolved_version = registry.get("company_intelligence").version
            technology_signals = self._grounded_technology_signals(
                payload.get("technology_signals"), ev["technologies"], context.website
            )
            return CompanyIntelligenceOutput(
                industry_analysis=payload.get("industry_analysis"),
                website_quality=payload.get("website_quality"),
                technology_signals=technology_signals,
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

    @staticmethod
    def _grounded_technology_signals(
        llm_technologies: Any, evidence_technologies: List[str], website: Any
    ) -> List[str]:
        """Deterministic hallucination guard: the prompt already instructs
        the model to only restate technologies present in Evidence Summary,
        but a prompt instruction is a request, not a guarantee. This is the
        enforcement -- any returned technology that isn't a case-insensitive
        match against what evidence actually contains is dropped rather
        than trusted, and logged so a pattern of violations is visible."""
        if not llm_technologies:
            return list(evidence_technologies)
        evidence_lower = {t.lower() for t in evidence_technologies}
        grounded, dropped = [], []
        for tech in llm_technologies:
            if isinstance(tech, str) and tech.lower() in evidence_lower:
                grounded.append(tech)
            else:
                dropped.append(tech)
        if dropped:
            logger.warning(
                f"Company Intelligence: dropped {len(dropped)} ungrounded technology "
                f"signal(s) not present in evidence for {website}: {dropped}"
            )
        return grounded

    # -- Deterministic fallback -------------------------------------------

    def _analyze_heuristically(self, context: LeadContext) -> CompanyIntelligenceOutput:
        ev = self._gather_evidence(context)
        facts = self._evidence_facts(ev)

        explanation = deterministic_explanation(
            reasoning=(
                "Reasoning over structured evidence already extracted by the "
                "scraper/normalizer/enrichment stages; no keyword matching or "
                "raw-text inference. The LLM path was unavailable for this "
                "run, so pain_points and growth_indicators -- which need "
                "reasoning this deterministic path doesn't attempt -- are "
                "left empty rather than guessed from isolated words."
            ),
            evidence=facts or ["No structured evidence available for this lead"],
        )

        return CompanyIntelligenceOutput(
            industry_analysis=context.industry or (ev["business_type"] or {}).get("value"),
            website_quality=self._infer_website_quality(ev),
            technology_signals=ev["technologies"],
            market_position=None,
            pain_points=[],
            growth_indicators=[],
            icp_alignment_score=self._infer_icp_alignment(ev),
            explanation=explanation,
            source="heuristic",
        )

    @staticmethod
    def _infer_website_quality(ev: Dict[str, Any]) -> str:
        """Evidence-*count* based, not text-length based: how many
        independent kinds of real evidence were actually extracted, not
        how much raw text exists on the page (a thin page can carry rich
        JSON-LD; a long page can be entirely boilerplate)."""
        signals_present = sum(
            [
                bool(ev["description"]),
                bool(ev["business_type"]),
                bool(ev["technologies"]),
                bool(ev["offerings"].get("products") or ev["offerings"].get("services")),
                bool(ev.get("social_profiles")),
                bool(ev["primary_contact"]),
                bool(ev.get("founded_year")),
                bool(ev.get("address")),
            ]
        )
        if signals_present >= 5:
            return "comprehensive"
        if signals_present >= 3:
            return "developed"
        if signals_present >= 1:
            return "minimal"
        return "unknown"

    @staticmethod
    def _infer_icp_alignment(ev: Dict[str, Any]) -> float:
        """Data-completeness proxy: how much structured evidence exists for
        this lead, not a judgment of whether that evidence represents a
        'good' industry or size -- that's a business-policy decision that
        belongs in LeadScoringService's configuration, not hardcoded here.
        """
        signals = [
            bool(ev["business_type"]),
            bool(ev["technologies"]),
            bool(ev["operating_regions"]),
            bool(ev["offerings"].get("products") or ev["offerings"].get("services")),
            bool(ev["primary_contact"]),
            bool(ev.get("founded_year") or ev.get("employees")),
            bool(ev.get("address")),
        ]
        return round(sum(signals) / len(signals), 2)