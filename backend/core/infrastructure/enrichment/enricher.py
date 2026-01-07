"""
Waterfall Enrichment Engine with confidence scoring
"""

import re
import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum

from core.domain.models.lead import Lead
from core.infrastructure.logging import get_logger

logger = get_logger(__name__)


class EnrichmentMethod(str, Enum):
    HEURISTIC = "heuristic"
    EXTERNAL_API = "external_api"
    LLM = "llm"


@dataclass
class EnrichmentResult:
    success: bool
    data: Dict[str, Any]
    method: EnrichmentMethod
    confidence: float
    processing_time: int  # in milliseconds


class WaterfallEnricher:
    """
    Implements a waterfall enrichment approach:
    1. Heuristic-based enrichment (deterministic, high confidence)
    2. External API enrichment (when available)
    3. LLM-based enrichment (as a last resort)
    """

    def __init__(self):
        self.industry_keywords = {
            "Software": [
                "software",
                "saas",
                "platform",
                "cloud",
                "api",
                "app",
                "application",
                "tech",
                "technology",
            ],
            "Consulting": [
                "consulting",
                "advisory",
                "services",
                "strategy",
                "business",
                "management",
            ],
            "E-commerce": [
                "ecommerce",
                "retail",
                "shop",
                "store",
                "marketplace",
                "buy",
                "sell",
            ],
            "Finance": [
                "finance",
                "banking",
                "investment",
                "fintech",
                "payment",
                "financial",
                "money",
            ],
            "Healthcare": [
                "health",
                "medical",
                "clinic",
                "hospital",
                "care",
                "pharma",
                "healthcare",
            ],
            "Marketing": [
                "marketing",
                "advertising",
                "media",
                "social",
                "campaign",
                "brand",
            ],
            "Education": [
                "education",
                "learning",
                "school",
                "university",
                "course",
                "training",
                "edu",
            ],
            "Real Estate": [
                "real estate",
                "property",
                "realestate",
                "estate",
                "housing",
                "rent",
                "buy",
            ],
            "Travel": [
                "travel",
                "tourism",
                "hotel",
                "booking",
                "vacation",
                "flight",
                "airline",
            ],
            "Food & Beverage": [
                "restaurant",
                "food",
                "beverage",
                "cafe",
                "catering",
                "delivery",
            ],
        }

        self.employee_keywords = {
            "1-10": ["startup", "early stage", "small team", "small business"],
            "11-50": ["growing", "medium sized", "expanding", "scale up"],
            "51-200": ["established", "mid sized", "corporate", "professional"],
            "201-500": ["large", "enterprise", "major", "substantial"],
            "500+": ["huge", "massive", "very large", "major corporation"],
        }

    def enrich_lead_data(
        self, lead: Lead, scraped_data: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """
        Main enrichment method that implements the waterfall approach
        """
        start_time = time.time()

        # Step 1: Try heuristic enrichment (highest confidence)
        heuristic_result = self._heuristic_enrichment(lead, scraped_data)
        if heuristic_result and heuristic_result.confidence > 0.7:
            heuristic_result.processing_time = int((time.time() - start_time) * 1000)
            return heuristic_result

        # Step 2: Try external API enrichment (if available)
        # For now, we'll simulate this with placeholder data
        api_result = self._external_api_enrichment(lead, scraped_data)
        if api_result and api_result.confidence > 0.6:
            api_result.processing_time = int((time.time() - start_time) * 1000)
            return api_result

        # Step 3: Try LLM enrichment (as last resort)
        llm_result = self._llm_enrichment(lead, scraped_data)
        if llm_result:
            llm_result.processing_time = int((time.time() - start_time) * 1000)
            return llm_result

        # If all methods fail, return None
        return None

    def _heuristic_enrichment(
        self, lead: Lead, scraped_data: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """
        Heuristic-based enrichment using deterministic rules
        """
        try:
            enriched_data = {}
            confidence = 0.0

            # Extract text to analyze
            text_to_analyze = self._get_text_for_analysis(lead, scraped_data)
            text_lower = text_to_analyze.lower()

            # Industry inference
            industry = self._infer_industry(text_lower)
            if industry:
                enriched_data["industry"] = industry
                confidence += 0.3

            # Employee count estimation
            employees = self._estimate_employees(text_lower)
            if employees:
                enriched_data["employees"] = employees
                confidence += 0.2

            # Revenue estimation based on employee count
            if employees:
                revenue_band = self._estimate_revenue_from_employees(employees)
                if revenue_band:
                    enriched_data["revenue_band"] = revenue_band
                    confidence += 0.1

            # Contact person extraction
            contact_name = self._extract_contact_person(text_to_analyze)
            if contact_name:
                enriched_data["contact_name"] = contact_name
                confidence += 0.15

            # Contact title extraction
            contact_title = self._extract_contact_title(text_to_analyze)
            if contact_title:
                enriched_data["contact_title"] = contact_title
                confidence += 0.1

            # Founded year extraction
            founded_year = self._extract_founded_year(text_to_analyze)
            if founded_year:
                enriched_data["founded_year"] = founded_year
                confidence += 0.15

            if not enriched_data:
                return None

            # Cap confidence at 0.9 for heuristic (never 100% certain)
            confidence = min(confidence, 0.9)

            return EnrichmentResult(
                success=True,
                data=enriched_data,
                method=EnrichmentMethod.HEURISTIC,
                confidence=confidence,
                processing_time=0,  # Will be set by parent
            )

        except Exception as e:
            logger.error(f"Heuristic enrichment failed: {str(e)}")
            return None

    def _external_api_enrichment(
        self, lead: Lead, scraped_data: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """
        External API enrichment (placeholder implementation)
        In a real system, this would call services like Clearbit, Apollo, etc.
        """
        # This is a placeholder - in a real implementation, you would call external APIs
        # such as Clearbit, Apollo, ZoomInfo, etc.

        # For now, we'll return None to simulate no external API data available
        # In a real system, you'd implement actual API calls here
        return None

    def _llm_enrichment(
        self, lead: Lead, scraped_data: Dict[str, Any]
    ) -> Optional[EnrichmentResult]:
        """
        LLM-based enrichment using structured prompts
        """
        try:
            import os
            from langchain_groq import ChatGroq
            from langchain_core.prompts import ChatPromptTemplate
            import json

            # Only proceed if we have API key
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key or api_key == "local_test_mode":
                logger.warning(
                    "GROQ_API_KEY not set or in local test mode, skipping LLM enrichment"
                )
                return None

            # Prepare the data for LLM
            text_to_analyze = self._get_text_for_analysis(lead, scraped_data)

            # Create prompt
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "You are a business intelligence assistant. Extract structured company information from the provided text. Respond ONLY with valid JSON.",
                    ),
                    (
                        "human",
                        """
                Company Name: {company_name}
                Website: {website}
                Text Content: {text_content}
                
                Extract the following information in JSON format:
                {{
                  "industry": "string or null",
                  "employees": "1-10 | 11-50 | 51-200 | 201-500 | 500+ | null",
                  "revenue_band": "$0-1M | $1M-10M | $10M-50M | $50M-100M | $100M+ | null",
                  "founded_year": "integer or null",
                  "contact_name": "string or null",
                  "contact_title": "string or null"
                }}
                
                Be conservative and only include information you can confidently extract from the text.
                If you cannot extract specific information, return null for that field.
                """,
                    ),
                ]
            )

            # Initialize LLM
            llm = ChatGroq(
                api_key=api_key,
                model=os.getenv("LLM_MODEL", "llama-3.3-70b-versatile"),
                temperature=0.0,  # Low temperature for consistency
                max_tokens=500,
            )

            # Create chain
            chain = prompt | llm

            # Execute
            response = chain.invoke(
                {
                    "company_name": lead.company_name or "Unknown",
                    "website": lead.website,
                    "text_content": text_to_analyze[:2000],  # Limit text length
                }
            )

            # Extract JSON from response
            content = (
                response.content if hasattr(response, "content") else str(response)
            )
            json_match = re.search(r"\{.*\}", content, re.DOTALL)

            if not json_match:
                logger.warning("No JSON found in LLM response")
                return None

            try:
                parsed_data = json.loads(json_match.group())

                # Filter out null values
                enriched_data = {k: v for k, v in parsed_data.items() if v is not None}

                if not enriched_data:
                    return None

                # Calculate confidence based on how much data was extracted
                confidence = 0.5 + (
                    len(enriched_data) * 0.1
                )  # Base 0.5 + 0.1 per field
                confidence = min(confidence, 0.8)  # Cap at 0.8 for LLM

                return EnrichmentResult(
                    success=True,
                    data=enriched_data,
                    method=EnrichmentMethod.LLM,
                    confidence=confidence,
                    processing_time=0,  # Will be set by parent
                )

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM response as JSON: {e}")
                return None

        except ImportError:
            logger.warning(
                "LangChain or langchain-groq not installed, skipping LLM enrichment"
            )
            return None
        except Exception as e:
            logger.error(f"LLM enrichment failed: {str(e)}")
            return None

    def _get_text_for_analysis(self, lead: Lead, scraped_data: Dict[str, Any]) -> str:
        """
        Combine all available text data for analysis
        """
        texts = []

        # Add company name
        if lead.company_name:
            texts.append(f"Company: {lead.company_name}")

        # Add about text
        if lead.about_text:
            texts.append(lead.about_text)

        # Add scraped data text content
        if "text_content" in scraped_data:
            texts.append(scraped_data["text_content"])
        elif "description" in scraped_data:
            texts.append(scraped_data["description"])
        elif "og_description" in scraped_data:
            texts.append(scraped_data["og_description"])
        elif "title" in scraped_data:
            texts.append(scraped_data["title"])

        # Add JSON-LD data if available
        if "jsonld" in scraped_data:
            try:
                texts.append(str(scraped_data["jsonld"]))
            except:
                pass

        return " ".join(texts)

    def _infer_industry(self, text: str) -> Optional[str]:
        """
        Infer industry using keyword matching
        """
        industry_scores = {}

        for industry, keywords in self.industry_keywords.items():
            score = sum(1 for keyword in keywords if keyword in text)
            if score > 0:
                industry_scores[industry] = score

        if industry_scores:
            # Return industry with highest score
            return max(industry_scores, key=industry_scores.get)

        return None

    def _estimate_employees(self, text: str) -> Optional[str]:
        """
        Estimate employee count using keyword matching
        """
        for size_range, keywords in self.employee_keywords.items():
            for keyword in keywords:
                if keyword in text:
                    return size_range

        # Try to extract numbers that might represent employee counts
        # Look for patterns like "50 employees", "team of 10", etc.
        employee_patterns = [
            r"(\d+)\s+employees?",
            r"team\s+of\s+(\d+)",
            r"(\d+)\s+person\s+team",
            r"(\d+)\s+staff",
        ]

        for pattern in employee_patterns:
            matches = re.findall(pattern, text)
            if matches:
                count = int(matches[0])
                if count <= 10:
                    return "1-10"
                elif count <= 50:
                    return "11-50"
                elif count <= 200:
                    return "51-200"
                elif count <= 500:
                    return "201-500"
                else:
                    return "500+"

        return None

    def _estimate_revenue_from_employees(self, employees: str) -> Optional[str]:
        """
        Estimate revenue band based on employee count
        """
        revenue_mapping = {
            "1-10": "$0-1M",
            "11-50": "$1M-10M",
            "51-200": "$10M-50M",
            "201-500": "$50M-100M",
            "500+": "$100M+",
        }
        return revenue_mapping.get(employees)

    def _extract_contact_person(self, text: str) -> Optional[str]:
        """
        Extract contact person using regex patterns
        """
        # Patterns for contact names
        patterns = [
            r"(?:CEO|Founder|President|CTO|CFO|COO|Director|Manager|Lead)\s+([A-Z][a-z]+\s[A-Z][a-z]+)",
            r"(?:Founder|Owner|Director|Manager|Lead)\s+([A-Z][a-z]+\s[A-Z][a-z]+)",
            r"(?:CEO|CTO|CFO|COO)\s+([A-Z][a-z]+\s[A-Z][a-z]+)",
            r"([A-Z][a-z]+\s[A-Z][a-z]+)\s+(?:CEO|Founder|President|CTO|CFO|COO|Director|Manager|Lead)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text)
            if matches:
                return matches[0]

        return None

    def _extract_contact_title(self, text: str) -> Optional[str]:
        """
        Extract contact title using regex patterns
        """
        # Patterns for titles
        patterns = [
            r"(CEO|Founder|President|CTO|CFO|COO|Director|Manager|Lead|VP|President|Owner)",
            r"(Chief\s+\w+\s+Officer)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                return matches[0].title()

        return None

    def _extract_founded_year(self, text: str) -> Optional[int]:
        """
        Extract founded year using regex patterns
        """
        # Patterns for years (1900-2030)
        patterns = [
            r"(?:founded|established|started|launched|incorporated)\s+in?\s+(19\d{2}|20\d{2}|\'\d{2})",
            r"(?:founded|established|started|launched|incorporated)\s+(19\d{2}|20\d{2}|\'\d{2})",
            r"(19\d{2}|20\d{2})\s+(?:founded|established|started|launched|incorporated)",
            r"(?:since|from)\s+(19\d{2}|20\d{2})",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                year_str = matches[0]
                # Handle abbreviated years like '05
                if year_str.startswith("'"):
                    year_str = "20" + year_str[1:]
                elif len(year_str) == 2:
                    year_str = (
                        "20" + year_str if int(year_str) < 50 else "19" + year_str
                    )

                try:
                    year = int(year_str)
                    if 1900 <= year <= 2030:
                        return year
                except ValueError:
                    continue

        return None
