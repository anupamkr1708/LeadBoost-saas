"""
Context Builder.

Builds a single `LeadContext` combining the lead, its organization, scraped
data, enrichment data, CRM history (pluggable extension point), and
business memory. Built once per pipeline run and reused by every agent, so
prompt-context construction never happens twice.
"""

import os
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from application.memory.db_memory import SQLBusinessMemory
from application.services import infra_adapters
from application.state.lead_state import LeadContext
from core.domain.models.lead import Lead
from core.infrastructure.logging import get_logger

logger = get_logger("application.context_builder")


class ContextBuilder:
    """Builds LeadContext objects. Stateless beyond its db session."""

    def __init__(self, db: Session):
        self.db = db
        self.memory = SQLBusinessMemory(db)

    def build(
        self,
        lead: Lead,
        scraped_data: Optional[Dict[str, Any]] = None,
        enriched_data: Optional[Dict[str, Any]] = None,
        crm_history: Optional[List[Dict[str, Any]]] = None,
    ) -> LeadContext:
        organization = infra_adapters.get_organization(self.db, lead.organization_id)

        previous_analysis = self.memory.get_previous_company_analysis(lead.id)
        previous_decisions = self.memory.get_previous_decisions(lead.id)
        previous_outreach = self.memory.get_previous_outreach(lead.id)

        return LeadContext(
            lead_id=lead.id,
            organization_id=lead.organization_id,
            company_name=lead.company_name,
            website=lead.website,
            industry=lead.industry,
            about_text=lead.about_text,
            employees=lead.employees,
            revenue_band=lead.revenue_band,
            founded_year=lead.founded_year,
            contact_name=lead.contact_name,
            contact_title=lead.contact_title,
            email=lead.email,
            phone=lead.phone,
            linkedin_url=lead.linkedin_url,
            scraped_data=scraped_data or {},
            enriched_data=enriched_data or {},
            organization_name=organization.name if organization else None,
            sender_org=os.getenv("SENDER_ORG", "Our Company"),
            # CRM history is a pluggable extension point: no CRM is wired up
            # in this codebase yet, so it defaults to empty rather than
            # faking data. A future CRM integration populates this list
            # before calling build(), or extends this method directly.
            crm_history=crm_history or [],
            previous_company_analysis=previous_analysis,
            previous_decisions=previous_decisions,
            previous_outreach=previous_outreach,
        )
