"""
L1: the business context LeadBoost hands to the Mailer's generated-outreach route.

Pure functions, no I/O. Every value comes from an EXISTING LeadBoost field;
nothing is invented to satisfy the Mailer contract.

value_proposition  <- Organization.description
    The Company Profile field labeled "What does your team do?" -- the only
    place LeadBoost stores what the SENDER offers. Rejected alternatives:
    `icp_description` (describes the RECIPIENT, not the offer), `industry`
    (one word), anything produced by an agent (qualification reasoning,
    scores, prompts) -- those are internal intelligence, not a business offer.
    Unset ⇒ None ⇒ dispatch fails closed (`value_proposition_not_configured`).

    SEEDED PLACEHOLDER: registration (api/endpoints/auth.py) pre-fills this
    column with "Organization for <user e-mail>". That is bookkeeping, not an
    offer, and sending it would both ground the generated e-mail on nonsense and
    push the registering user's address into the model prompt. It is therefore
    treated exactly like an unset description until the user writes a real one.

recipient_facts    <- real Lead data only
    industry, about_text, founded_year, employees (+ revenue_band): the same
    fields, and the same wording for the last three, that
    company_intelligence_agent already uses to describe a lead. Never scores,
    AI confidence, qualification/decision reasoning or raw pipeline state.
    Company name and contact title travel in `recipient`, not as facts.

NOTE: `about_text` is scraped from the lead's own website, i.e. third-party
text. It is length-capped and whitespace-normalized here; treating it as
untrusted prompt input is the Mailer's grounding/generation responsibility,
exactly as it already is for LeadBoost's own MessagingAgent.
"""

import re
from typing import List, Optional

from core.domain.models.lead import Lead
from core.domain.models.organization import Organization

_WS = re.compile(r"\s+")
# The exact shape api/endpoints/auth.py seeds at registration (see module docstring).
_SEEDED_PLACEHOLDER = re.compile(r"^Organization for \S+@\S+$")
ABOUT_MAX_CHARS = 500


def _text(value) -> Optional[str]:
    if value is None:
        return None
    cleaned = _WS.sub(" ", str(value)).strip()
    return cleaned or None


def build_value_proposition(organization: Optional[Organization]) -> Optional[str]:
    if organization is None:
        return None
    description = _text(organization.description)
    if description is None or _SEEDED_PLACEHOLDER.match(description):
        return None
    return description


def build_recipient_facts(lead: Lead) -> List[str]:
    facts: List[str] = []

    industry = _text(lead.industry)
    if industry:
        facts.append(f"Industry: {industry}")

    about = _text(lead.about_text)
    if about:
        prefix = "About: "
        room = ABOUT_MAX_CHARS - len(prefix)
        facts.append(prefix + (about if len(about) <= room else about[: room - 3].rstrip() + "..."))

    if lead.founded_year:
        facts.append(f"Founded: {lead.founded_year}")

    employees = _text(lead.employees)
    if employees:
        revenue = _text(lead.revenue_band)
        facts.append(f"Employee band: {employees}" + (f", est. revenue {revenue}" if revenue else ""))

    return facts
