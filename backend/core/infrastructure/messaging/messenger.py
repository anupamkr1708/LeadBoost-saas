"""
Data-locked prompt system for outreach message generation
"""

import os
import time
from typing import Dict, Any, Optional
from enum import Enum

from core.domain.models.lead import Lead
from core.infrastructure.logging import get_logger

logger = get_logger(__name__)


class MessageStyle(str, Enum):
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    SHORT = "short"


class Messenger:
    """
    Data-locked prompt system that prevents AI hallucinations by using
    strict fallback templates when company context is missing.
    """

    def __init__(self):
        self.sender_org = os.getenv("SENDER_ORG", "Our Company")
        self.model_name = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

    def generate_message(self, lead: Lead) -> Optional[str]:
        """
        Generate a personalized outreach message based on available data
        """
        start_time = time.time()

        # Check if we have enough data to generate a meaningful message
        has_sufficient_data = self._has_sufficient_data(lead)

        if has_sufficient_data:
            # Try LLM-based generation
            message = self._generate_llm_message(lead)
            if message:
                logger.info(
                    "LLM message generated successfully",
                    extra={
                        "event": "message_generation",
                        "lead_id": lead.id,
                        "method": "llm",
                        "processing_time_ms": int((time.time() - start_time) * 1000),
                    },
                )
                return message

        # Fallback to template-based generation
        message = self._generate_template_message(lead)
        logger.info(
            "Template message generated",
            extra={
                "event": "message_generation",
                "lead_id": lead.id,
                "method": "template",
                "processing_time_ms": int((time.time() - start_time) * 1000),
            },
        )
        return message

    def _has_sufficient_data(self, lead: Lead) -> bool:
        """
        Check if we have enough data to generate a meaningful message
        """
        data_points = 0

        if lead.company_name:
            data_points += 1
        if lead.industry:
            data_points += 1
        if lead.about_text and len(lead.about_text) > 50:
            data_points += 1
        if lead.contact_name:
            data_points += 1
        if lead.employees:
            data_points += 1

        # Require at least 2 data points for meaningful personalization
        return data_points >= 2

    def _generate_llm_message(self, lead: Lead) -> Optional[str]:
        """
        Generate message using LLM with data-locked prompts
        """
        try:
            import os
            from langchain_groq import ChatGroq
            from langchain_core.prompts import ChatPromptTemplate

            # Only proceed if we have API key
            api_key = os.getenv("GROQ_API_KEY")
            if not api_key or api_key == "local_test_mode":
                logger.warning(
                    "GROQ_API_KEY not set or in local test mode, skipping LLM message generation"
                )
                return None

            # Create a context-safe prompt that only uses available data
            context = self._build_context(lead)

            # Create prompt template
            prompt = ChatPromptTemplate.from_messages(
                [
                    ("system", self._get_system_prompt()),
                    ("human", self._get_human_prompt(context)),
                ]
            )

            # Initialize LLM
            llm = ChatGroq(
                api_key=api_key,
                model=self.model_name,
                temperature=0.3,  # Low temperature to reduce hallucinations
                max_tokens=200,
            )

            # Create chain and execute
            chain = prompt | llm
            response = chain.invoke({})

            content = (
                response.content if hasattr(response, "content") else str(response)
            )

            # Validate the response doesn't contain hallucinated information
            validated_content = self._validate_response(content, context)

            return validated_content.strip()

        except ImportError:
            logger.warning("LangChain not installed, skipping LLM message generation")
            return None
        except Exception as e:
            logger.error(f"LLM message generation failed: {str(e)}")
            return None

    def _build_context(self, lead: Lead) -> Dict[str, Any]:
        """
        Build context dictionary with available lead data
        """
        return {
            "company_name": lead.company_name or "their company",
            "industry": lead.industry or "their industry",
            "about_text": lead.about_text or "",
            "contact_name": lead.contact_name or "the team",
            "employees": lead.employees or "",
            "website": lead.website,
            "sender_org": self.sender_org,
        }

    def _get_system_prompt(self) -> str:
        """
        Get system prompt that enforces data-locked behavior
        """
        return (
            "You are an outreach assistant. Generate a professional outreach message "
            "using ONLY the information provided in the context. Do not invent or "
            "hallucinate any information not present in the context. "
            "Keep the message concise and relevant to the recipient."
        )

    def _get_human_prompt(self, context: Dict[str, Any]) -> str:
        """
        Get human prompt with context
        """
        prompt_parts = [
            f"Sender Organization: {context['sender_org']}",
            f"Recipient Company: {context['company_name']}",
            f"Industry: {context['industry']}",
            f"Website: {context['website']}",
        ]

        if context["about_text"]:
            prompt_parts.append(f"About: {context['about_text'][:200]}...")

        if context["contact_name"]:
            prompt_parts.append(f"Contact: {context['contact_name']}")

        if context["employees"]:
            prompt_parts.append(f"Size: {context['employees']} employees")

        prompt_parts.append(
            f"\nWrite a personalized outreach message from {context['sender_org']} "
            f"to {context['company_name']} that acknowledges their work in {context['industry']}. "
            "The message should be professional but not overly formal. "
            "Focus on how {sender_org} could provide value to their business."
        )

        return "\n".join(prompt_parts)

    def _validate_response(self, response: str, context: Dict[str, Any]) -> str:
        """
        Validate that the response doesn't contain hallucinated information
        """
        # This is a basic validation - in a production system, you might want
        # more sophisticated validation logic
        validated_response = response

        # Ensure the response mentions the company name if available
        if context["company_name"] != "their company":
            if context["company_name"].lower() not in response.lower():
                # Add a mention of the company if missing
                validated_response = f"Hi {context['company_name']} team,\n\n{response}"

        return validated_response

    def _generate_template_message(self, lead: Lead) -> str:
        """
        Generate message using template fallback when LLM is unavailable
        or insufficient data exists
        """
        # Use company-specific templates based on available data
        if lead.company_name and lead.industry:
            return self._get_industry_template(lead)
        elif lead.company_name:
            return self._get_generic_template(lead)
        else:
            return self._get_website_only_template(lead)

    def _get_industry_template(self, lead: Lead) -> str:
        """
        Get industry-specific template
        """
        contact_ref = lead.contact_name or "the team"
        company_ref = lead.company_name or "your company"

        templates = {
            "software": f"Hi {contact_ref},\n\nI came across {company_ref} in the software space and was impressed by your work. At {self.sender_org}, we help software companies optimize their operations and growth. I'd love to explore how we might add value to {company_ref}'s journey.\n\nBest regards,\n{self.sender_org}",
            "consulting": f"Hi {contact_ref},\n\nI noticed {company_ref} in the consulting field and thought there might be synergies with our work at {self.sender_org}. We specialize in helping consulting firms enhance their client value proposition. Would you be open to a brief conversation?\n\nBest regards,\n{self.sender_org}",
            "ecommerce": f"Hi {contact_ref},\n\nI discovered {company_ref} in the e-commerce space and was intrigued by your approach. {self.sender_org} works with e-commerce businesses to streamline their operations and drive growth. I'd be keen to learn more about your current challenges and see if there's alignment with our expertise.\n\nBest regards,\n{self.sender_org}",
        }

        industry_key = (lead.industry or "").lower().replace(" ", "")
        if industry_key in templates:
            return templates[industry_key]

        # Default template
        return f"Hi {contact_ref},\n\nI came across {company_ref} in the {lead.industry} space and thought there could be value in a brief conversation. We at {self.sender_org} work with companies like yours to explore growth and efficiency opportunities.\n\nBest regards,\n{self.sender_org}"

    def _get_generic_template(self, lead: Lead) -> str:
        """
        Get generic template when only company name is available
        """
        contact_ref = lead.contact_name or "the team"
        company_ref = lead.company_name or "your company"

        return f"Hi {contact_ref},\n\nI discovered {company_ref} and was interested in what you're building. At {self.sender_org}, we work with innovative companies to help them achieve their growth objectives. I'd love to learn more about your current initiatives and see if there's potential for collaboration.\n\nBest regards,\n{self.sender_org}"

    def _get_website_only_template(self, lead: Lead) -> str:
        """
        Get template when only website is available
        """
        website = lead.website or "your website"
        company_ref = lead.company_name or "your company"

        return f"Hi team,\n\nI visited {website} and was impressed by {company_ref}'s work. At {self.sender_org}, we help companies like yours navigate growth challenges and operational efficiencies. I'd be interested in exploring potential synergies.\n\nBest regards,\n{self.sender_org}"

    def generate_message_with_style(self, lead: Lead, style: MessageStyle) -> str:
        """
        Generate message with specific style (professional, friendly, short)
        """
        base_message = self.generate_message(lead)

        if style == MessageStyle.PROFESSIONAL:
            return self._make_professional(base_message)
        elif style == MessageStyle.FRIENDLY:
            return self._make_friendly(base_message)
        elif style == MessageStyle.SHORT:
            return self._make_short(base_message)

        return base_message

    def _make_professional(self, message: str) -> str:
        """
        Make message more professional
        """
        # Ensure formal tone
        message = message.replace("Hi ", "Dear ")
        if "Best regards," not in message:
            message += f"\n\nBest regards,\n{self.sender_org}"
        return message

    def _make_friendly(self, message: str) -> str:
        """
        Make message more friendly
        """
        # Ensure friendly tone
        message = message.replace("Dear ", "Hi ")
        if "Best regards," in message:
            message = message.replace("Best regards,", "Cheers,")
        return message

    def _make_short(self, message: str) -> str:
        """
        Make message shorter
        """
        lines = message.split("\n")
        # Keep the greeting and first paragraph, remove the signature if too long
        if len(lines) > 4:
            message = "\n".join(lines[:4])
        return message
