"""Waterfall Enrichment Engine with confidence scoring.

Architecture
------------
This module builds business intelligence from evidence -- it never
classifies what KIND of business something is by matching page text
against a hardcoded category table. That distinction matters: a keyword
table ("hospital", "restaurant", "SaaS", ...) can only ever cover the
categories someone thought to list in advance, silently misclassifies or
drops everything else, and re-derives a fact (what kind of organization
this is) that the scraper already captured more reliably, structurally,
straight from the site's own schema.org self-declaration.

So the waterfall here is:

  1. Deterministic enrichment (highest confidence) -- composes a business
     profile purely by reading `core.infrastructure.normalization
     .normalizer.normalize_scraped_fields()`'s canonical output: the
     site's own declared organization type, its structured facts
     (founding year, employee count, address/regions, products/services),
     and its contact channels, ranked by purpose. No industry table, no
     per-technology category mapping, no adjective-based size guessing --
     if the evidence isn't there, the corresponding field is simply
     omitted rather than guessed.
  2. External API enrichment (e.g. Clearbit/Apollo/ZoomInfo) -- a
     placeholder integration point, gated the same way.
  3. LLM enrichment, as the last resort for whatever the deterministic
     tier honestly couldn't determine (e.g. no schema.org type was
     declared at all). This is the ONE tier where open-ended inference
     belongs -- everything upstream of it stays deterministic and
     evidence-based on purpose.

Every tier operates on `normalize_scraped_fields(scraped_data)`'s output,
never on the scraper's raw field names directly -- this is what lets
Enrichment stay fully general across any business category without a
single `if hospital` / `if restaurant` / `if SaaS` branch anywhere in the
file, and what keeps it insulated from the scraper's internal key shapes.
"""

import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from core.domain.models.lead import Lead
from core.infrastructure.logging import get_logger
from core.infrastructure.normalization.normalizer import normalize_scraped_fields

logger = get_logger(__name__)


class EnrichmentMethod(str, Enum):
    HEURISTIC = "heuristic"
    EXTERNAL_API = "external_api"
    LLM = "llm"
    MERGED = "merged"  # more than one tier contributed fields to the result


@dataclass
class EnrichmentResult:
    success: bool
    data: Dict[str, Any]
    method: EnrichmentMethod
    confidence: float
    processing_time: int  # in milliseconds


# ---------------------------------------------------------------------------
# Universal, business-agnostic constants
#
# Neither of these tables says anything about what KIND of business an
# organization is -- one ranks contact-channel PURPOSE (every
# organization, whatever it does, distinguishes a general/sales inbox from
# a careers or press inbox), and the other buckets a headcount NUMBER into
# a size band. Both apply identically to a hospital, a law firm, a SaaS
# company, or a construction firm.
# ---------------------------------------------------------------------------

_CONTACT_CATEGORY_PRIORITY: Tuple[str, ...] = (
    "general", "contact", "sales", "support", "press", "careers", "privacy", "billing",
)

_EMPLOYEE_BANDS: Tuple[Tuple[float, str], ...] = (
    (10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"), (float("inf"), "500+"),
)
_KNOWN_EMPLOYEE_BAND_LABELS = {label for _, label in _EMPLOYEE_BANDS}

_REVENUE_BAND_BY_EMPLOYEE_BAND: Dict[str, str] = {
    "1-10": "$0-1M", "11-50": "$1M-10M", "51-200": "$10M-50M",
    "201-500": "$50M-100M", "500+": "$100M+",
}

# Relative importance of each fact toward the deterministic tier's overall
# confidence score -- "how much does knowing this fact tell us we have a
# well-populated, trustworthy profile," not a business-category weighting.
# Centralized here instead of scattered inline numbers so the scale is
# auditable in one place.
_PROFILE_FACT_WEIGHTS: Dict[str, float] = {
    "organization_type": 0.30,
    "founded_year": 0.15,
    "employee_count": 0.15,
    "operating_regions": 0.10,
    "offerings": 0.10,
    "primary_contact": 0.15,
    "contact_name": 0.10,
    "contact_title": 0.05,
}

# Generic business-title vocabulary used to spot a named contact person in
# free text. This is role vocabulary (CEO/Founder/Director...), not an
# industry classification -- a hospital, a law firm, and a SaaS startup
# all use the same titles, so this generalizes without per-industry rules.
_CONTACT_PERSON_PATTERNS = (
    r"(?:CEO|Founder|President|CTO|CFO|COO|Director|Manager|Lead|Owner)\s+([A-Z][a-z]+\s[A-Z][a-z]+)",
    r"([A-Z][a-z]+\s[A-Z][a-z]+)\s+(?:CEO|Founder|President|CTO|CFO|COO|Director|Manager|Lead)",
)
_CONTACT_TITLE_PATTERNS = (
    r"(CEO|Founder|President|CTO|CFO|COO|Director|Manager|Lead|VP|Owner)",
    r"(Chief\s+\w+\s+Officer)",
)


def _bucket_employee_count(raw: Optional[str]) -> Optional[str]:
    """Buckets a headcount (a bare number, a range like "50-100", or an
    already-banded string like "500+") into one of the standard size
    bands. Pure numeric bucketing -- no business-category logic."""
    if not raw:
        return None
    text = str(raw).strip()
    if text in _KNOWN_EMPLOYEE_BAND_LABELS:
        return text
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return None
    value = max(numbers)
    for ceiling, band in _EMPLOYEE_BANDS:
        if value <= ceiling:
            return band
    return None


def _prioritize_contact(normalized: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Picks the single best contact channel from normalized evidence,
    ranked by universal contact PURPOSE (general/sales beats careers or
    press for outreach) and, within the same purpose, by confidence. A
    phone is valid contact evidence in its own right, not just a bonus
    attached to an email -- a business that only publishes a phone number
    (no email at all) still has real, usable contact evidence."""
    emails = normalized.get("emails") or []
    phones = normalized.get("phones") or []
    if not emails and not phones:
        return None

    if not emails:
        # Phone-only: no purpose signal to rank by (unlike a categorized
        # email), so a flat, moderate confidence -- the same tier used
        # elsewhere in this pipeline for a generic, uncorroborated match.
        return {
            "email": None,
            "email_category": None,
            "confidence": 0.5,
            "phone": phones[0],
        }

    def _rank(entry: Dict[str, Any]) -> Tuple[int, float]:
        try:
            idx = _CONTACT_CATEGORY_PRIORITY.index(entry.get("category", ""))
        except ValueError:
            idx = len(_CONTACT_CATEGORY_PRIORITY)
        return (idx, -entry.get("confidence", 0.0))

    best = sorted(emails, key=_rank)[0]
    return {
        "email": best["email"],
        "email_category": best.get("category", "general"),
        "confidence": best.get("confidence", 0.5),
        "phone": phones[0] if phones else None,
    }


class WaterfallEnricher:
    """Implements a waterfall enrichment approach:
    1. Deterministic enrichment over normalized evidence (highest confidence)
    2. External API enrichment (when available)
    3. LLM-based enrichment (as a last resort)
    """

    def enrich_lead_data(
        self, lead: Lead, scraped_data: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """Main entry point. Normalizes the scraper's raw output exactly
        once, then runs every tier against that single canonical view.

        Each tier can still short-circuit early when it's confident enough
        (the original waterfall behavior, kept as-is for efficiency), but
        if nothing clears its threshold, the best-scoring result actually
        produced is returned instead of being discarded -- a genuinely
        derived, moderate-confidence profile is more useful to a caller
        (who can check `.confidence` themselves) than silently getting
        nothing back.
        """
        start_time = time.time()
        normalized = normalize_scraped_fields(scraped_data)

        merged_data: Dict[str, Any] = {}
        contributing_methods: List[EnrichmentMethod] = []
        confidence = 0.0

        deterministic_result = self._deterministic_enrichment(lead, normalized)
        if deterministic_result:
            merged_data.update(deterministic_result.data)
            contributing_methods.append(deterministic_result.method)
            confidence = deterministic_result.confidence
            if deterministic_result.confidence > 0.7:
                # Deterministic evidence is already complete/confident
                # enough on its own -- skip the API/LLM tiers' latency and
                # cost entirely rather than run them just to discard their
                # output.
                return self._finalize(merged_data, contributing_methods, confidence, start_time)

        api_result = self._external_api_enrichment(lead, normalized)
        if api_result:
            self._merge_new_fields(merged_data, api_result.data)
            contributing_methods.append(api_result.method)
            confidence = max(confidence, api_result.confidence)
            if api_result.confidence > 0.6:
                return self._finalize(merged_data, contributing_methods, confidence, start_time)

        llm_result = self._llm_enrichment(lead, normalized)
        if llm_result:
            self._merge_new_fields(merged_data, llm_result.data)
            contributing_methods.append(llm_result.method)
            confidence = max(confidence, llm_result.confidence)

        if not merged_data:
            return None
        return self._finalize(merged_data, contributing_methods, confidence, start_time)

    @staticmethod
    def _merge_new_fields(merged_data: Dict[str, Any], new_fields: Dict[str, Any]) -> None:
        """Gap-fill only: a later tier can add a field the earlier tiers
        left empty, but can never overwrite one they already populated.
        Same generic merge pattern used throughout the scraper for
        combining tiers/pages, applied here to enrichment tiers."""
        for key, value in (new_fields or {}).items():
            if value is not None and merged_data.get(key) is None:
                merged_data[key] = value

    @staticmethod
    def _finalize(
        merged_data: Dict[str, Any],
        contributing_methods: List[EnrichmentMethod],
        confidence: float,
        start_time: float,
    ) -> EnrichmentResult:
        method = contributing_methods[0] if len(contributing_methods) == 1 else EnrichmentMethod.MERGED
        return EnrichmentResult(
            success=True,
            data=merged_data,
            method=method,
            confidence=confidence,
            processing_time=int((time.time() - start_time) * 1000),
        )

    # -- Tier 1: deterministic, evidence-based ------------------------------

    def _deterministic_enrichment(
        self, lead: Lead, normalized: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """Composes a business profile purely from normalized evidence.
        Every field is either present (backed by real evidence, with its
        own confidence) or absent -- nothing here is guessed from
        ambiguous keywords."""
        try:
            enriched_data: Dict[str, Any] = {}
            confidence_parts: List[Tuple[float, float]] = []
            field_confidence = normalized.get("field_confidence") or {}

            org_type = normalized.get("organization_type")
            if org_type:
                enriched_data["business_type"] = org_type
                confidence_parts.append((_PROFILE_FACT_WEIGHTS["organization_type"], org_type["confidence"]))

            if normalized.get("founded_year"):
                enriched_data["founded_year"] = normalized["founded_year"]
                confidence_parts.append(
                    (_PROFILE_FACT_WEIGHTS["founded_year"], field_confidence.get("founded_year", 0.7))
                )

            employee_band = _bucket_employee_count(normalized.get("employee_count"))
            if employee_band:
                enriched_data["employees"] = employee_band
                confidence_parts.append(
                    (_PROFILE_FACT_WEIGHTS["employee_count"], field_confidence.get("employee_count", 0.75))
                )
                revenue_band = _REVENUE_BAND_BY_EMPLOYEE_BAND.get(employee_band)
                if revenue_band:
                    # A correlation-based estimate, not a fact -- kept
                    # deliberately low-confidence and not counted toward
                    # the weighted profile score on its own.
                    enriched_data["revenue_band"] = revenue_band

            if normalized.get("operating_regions"):
                enriched_data["operating_regions"] = normalized["operating_regions"]
                confidence_parts.append((_PROFILE_FACT_WEIGHTS["operating_regions"], 0.8))

            offerings = {
                "products": normalized.get("products") or [],
                "services": normalized.get("services") or [],
            }
            if offerings["products"] or offerings["services"]:
                enriched_data["offerings"] = offerings
                confidence_parts.append((_PROFILE_FACT_WEIGHTS["offerings"], 0.75))

            if normalized.get("description"):
                enriched_data["description"] = normalized["description"]

            if normalized.get("technologies"):
                # Evidence only -- exposed as-is for a human or downstream
                # consumer to read. Never used here to infer a category
                # (e.g. "uses Shopify" no longer implies "is E-commerce");
                # that inference doesn't scale and belongs, if anywhere, in
                # the LLM tier, not a hardcoded technology-to-category map.
                enriched_data["technologies"] = normalized["technologies"]

            primary_contact = _prioritize_contact(normalized)
            if primary_contact:
                enriched_data["primary_contact"] = primary_contact
                confidence_parts.append(
                    (_PROFILE_FACT_WEIGHTS["primary_contact"], primary_contact["confidence"])
                )

            text_excerpt = normalized.get("text_excerpt") or ""
            contact_name = self._extract_contact_person(text_excerpt)
            if contact_name:
                enriched_data["contact_name"] = contact_name
                confidence_parts.append((_PROFILE_FACT_WEIGHTS["contact_name"], 0.4))

            contact_title = self._extract_contact_title(text_excerpt)
            if contact_title:
                enriched_data["contact_title"] = contact_title
                confidence_parts.append((_PROFILE_FACT_WEIGHTS["contact_title"], 0.4))

            if not confidence_parts:
                # Nothing here is backed by an actual confidence-scored
                # fact (e.g. only a description was carried forward) --
                # that's not meaningful enrichment on its own, so don't
                # manufacture a hollow, zero-confidence result.
                return None

            confidence = self._aggregate_confidence(confidence_parts)

            return EnrichmentResult(
                success=True,
                data=enriched_data,
                method=EnrichmentMethod.HEURISTIC,
                confidence=confidence,
                processing_time=0,  # set by caller
            )

        except Exception as e:
            logger.error(f"Deterministic enrichment failed: {str(e)}")
            return None

    @staticmethod
    def _aggregate_confidence(parts: List[Tuple[float, float]]) -> float:
        """Weighted average of each fact's own confidence, with a modest
        coverage bonus for having multiple independent facts (a
        well-populated profile is itself corroborating evidence, not just
        an average of whichever fields happened to be found). Capped at
        0.9 -- deterministic evidence composition is never 100% certain."""
        if not parts:
            return 0.0
        total_weight = sum(w for w, _ in parts)
        if total_weight <= 0:
            return 0.0
        weighted = sum(w * c for w, c in parts) / total_weight
        coverage = min(1.0, len(parts) / 6.0)
        return min(weighted * (0.7 + 0.3 * coverage), 0.9)

    # -- Tier 2: external API (placeholder integration point) ---------------

    def _external_api_enrichment(
        self, lead: Lead, normalized: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """Placeholder for a third-party enrichment API (Clearbit, Apollo,
        ZoomInfo, ...). Returns None until such an integration exists."""
        return None

    # -- Tier 3: LLM, last resort ---------------------------------------------

    def _llm_enrichment(
        self, lead: Lead, normalized: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """LLM-based enrichment using structured prompts, for whatever the
        deterministic tier honestly couldn't determine (most commonly: no
        schema.org organization type was declared at all). Grounded in the
        same normalized evidence the deterministic tier used, so it can't
        silently contradict a fact that's already confidently known."""
        try:
            import json
            import os

            from langchain_core.prompts import ChatPromptTemplate
            from langchain_groq import ChatGroq

            api_key = os.getenv("GROQ_API_KEY")
            if not api_key or api_key == "local_test_mode":
                logger.warning("GROQ_API_KEY not set or in local test mode, skipping LLM enrichment")
                return None

            context = self._build_llm_context(lead, normalized)

            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a business intelligence assistant. Extract structured "
                        "company information from the provided evidence. Respond ONLY "
                        "with valid JSON.",
                    ),
                    (
                        "human",
                        """
                Evidence:
                {context}

                Extract the following information in JSON format:
                {{
                  "industry": "string or null",
                  "employees": "1-10 | 11-50 | 51-200 | 201-500 | 500+ | null",
                  "revenue_band": "$0-1M | $1M-10M | $10M-50M | $50M-100M | $100M+ | null",
                  "founded_year": "integer or null",
                  "contact_name": "string or null",
                  "contact_title": "string or null"
                }}

                Be conservative and only include information you can confidently infer
                from the evidence above. A field already given in the evidence (e.g. a
                declared type or founding year) should be treated as ground truth, not
                re-guessed. If you cannot confidently determine a field, return null.
                """,
                    ),
                ]
            )

            llm = ChatGroq(
                api_key=api_key,
                model=os.getenv("LLM_MODEL", "openai/gpt-oss-120b"),
                temperature=0.0,
                max_tokens=500,
            )

            chain = prompt | llm
            # Production-robustness hardening (Phase B4): see
            # application/services/llm_provider.py's docstring -- this
            # module independently constructs its own ChatGroq client and
            # must share the same process-wide concurrency gate as the
            # other two Groq call sites (they all draw on the same
            # account/TPM budget).
            from application.services.llm_provider import llm_call_slot

            with llm_call_slot():
                response = chain.invoke({"context": context})
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                logger.warning("No JSON found in LLM response")
                return None

            parsed_data = json.loads(json_match.group())
            enriched_data = {k: v for k, v in parsed_data.items() if v is not None}
            if not enriched_data:
                return None

            confidence = min(0.5 + len(enriched_data) * 0.1, 0.8)

            return EnrichmentResult(
                success=True,
                data=enriched_data,
                method=EnrichmentMethod.LLM,
                confidence=confidence,
                processing_time=0,
            )

        except ImportError:
            logger.warning("LangChain or langchain-groq not installed, skipping LLM enrichment")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM enrichment failed: {str(e)}")
            return None

    @staticmethod
    def _build_llm_context(lead: Lead, normalized: Dict[str, Any]) -> str:
        """Builds the LLM's free-text context purely from normalized
        evidence -- keeps even the last-resort tier decoupled from the
        scraper's raw field names, and grounds it in whatever the
        deterministic tier already established."""
        parts: List[str] = []
        if lead.company_name:
            parts.append(f"Company: {lead.company_name}")
        if lead.website:
            parts.append(f"Website: {lead.website}")
        if normalized.get("organization_type"):
            parts.append(f"Declared type (schema.org): {normalized['organization_type']['value']}")
        if normalized.get("description"):
            parts.append(f"Description: {normalized['description']}")
        if normalized.get("products"):
            parts.append(f"Products: {', '.join(normalized['products'][:10])}")
        if normalized.get("services"):
            parts.append(f"Services: {', '.join(normalized['services'][:10])}")
        if normalized.get("operating_regions"):
            parts.append(f"Operating regions: {', '.join(normalized['operating_regions'])}")
        if lead.about_text:
            parts.append(lead.about_text)
        if normalized.get("text_excerpt"):
            parts.append(normalized["text_excerpt"][:2000])
        return "\n".join(parts)

    # -- Generic (non-industry-specific) contact-person heuristics -----------

    @staticmethod
    def _extract_contact_person(text: str) -> Optional[str]:
        """Best-effort contact-name spotting using generic business-title
        vocabulary (CEO/Founder/Director/...). Deliberately low weight in
        the overall confidence score -- adjacency-based name matching is
        inherently approximate."""
        for pattern in _CONTACT_PERSON_PATTERNS:
            matches = re.findall(pattern, text)
            if matches:
                return matches[0]
        return None

    @staticmethod
    def _extract_contact_title(text: str) -> Optional[str]:
        for pattern in _CONTACT_TITLE_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return matches[0].title()
        return None