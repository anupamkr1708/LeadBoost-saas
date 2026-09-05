"""
Shared agent-execution functions -- used by BOTH generate_datasets.py
(to capture the golden baseline once) and run_eval.py (to re-check
against that baseline on every run). This is deliberately one shared
module, not two independent implementations: if generation and
evaluation constructed their inputs differently, a passing eval run
would prove nothing -- it's essential that both drive the exact same
real code the exact same way.

Every function here calls real, production agent/service code with
`allow_llm=False` (or relies on GROQ_API_KEY being unset, matching this
whole test environment's convention -- see tests/application/
conftest.py) so these are exercises of genuine deterministic fallback
behavior, not mocks.
"""

from types import SimpleNamespace
from typing import Any, Dict

from application.agents.company_intelligence_agent import CompanyIntelligenceAgent
from application.agents.decision_agent import DecisionAgent
from application.agents.messaging_agent import MessagingAgent
from application.dto.models import CompanyIntelligenceOutput, Explanation
from application.state.lead_state import DecisionContext, LeadContext
from core.domain.services.scoring import LeadScoringService

_ci_agent = CompanyIntelligenceAgent()
_decision_agent = DecisionAgent()
_messaging_agent = MessagingAgent()
_scoring_service = LeadScoringService()


class _DecisionOutputStub:
    """MessagingAgent's template path (the only path exercised with
    GROQ_API_KEY unset) never reads any field off the `decision`
    argument -- only `lead`/`context` matter -- so this stub exists
    purely to satisfy the function signature."""

    qualification = "Warm Lead"
    recommended_action = "review"
    explanation = SimpleNamespace(evidence=[], reasoning="", confidence=0.5)


def run_company_intelligence(input_context: Dict[str, Any]) -> Dict[str, Any]:
    ctx = LeadContext(**input_context)
    output: CompanyIntelligenceOutput = _ci_agent.run(ctx, allow_llm=False)
    result = output.model_dump()
    result["evidence"] = output.explanation.evidence
    result["reasoning"] = output.explanation.reasoning
    result["confidence"] = output.explanation.confidence
    return result


def run_qualification(input_context: Dict[str, Any]) -> Dict[str, Any]:
    lead_fields = dict(input_context.get("lead", {}))
    lead_fields.setdefault("industry", None)
    lead_fields.setdefault("employees", None)
    lead_fields.setdefault("email_confidence", 0.0)
    lead_fields.setdefault("phone", None)
    lead_fields.setdefault("scrape_confidence", 0.0)
    lead_fields.setdefault("enrichment_confidence", 0.0)
    lead_fields.setdefault("linkedin_url", None)
    lead = SimpleNamespace(**lead_fields)

    ci = CompanyIntelligenceOutput(
        icp_alignment_score=input_context.get("icp_alignment_score", 0.0),
        explanation=Explanation(reasoning="eval fixture", evidence=[]),
        source="heuristic",
    )
    result = _scoring_service.score_lead(lead, company_intelligence=ci)
    return {
        "qualification_label": result.qualification_label,
        "total_score": result.total_score,
        "criteria_scores": result.criteria_scores,
    }


def run_decision(input_context: Dict[str, Any]) -> Dict[str, Any]:
    ctx = DecisionContext(
        lead_context=LeadContext(lead_id=1, organization_id=1, website="https://example-co.example.com"),
        company_intelligence={
            "icp_alignment_score": input_context.get("icp_alignment_score", 0.0),
            "industry_analysis": "Test Industry",
        },
        score=input_context["score"],
        qualification_label=input_context["qualification_label"],
        scrape_confidence=input_context["scrape_confidence"],
        enrichment_confidence=input_context["enrichment_confidence"],
    )
    output = _decision_agent.run(ctx, allow_llm=False)
    result = output.model_dump()
    result["evidence"] = output.explanation.evidence
    result["reasoning"] = output.explanation.reasoning
    result["confidence"] = output.explanation.confidence
    return result


def run_messaging(input_context: Dict[str, Any]) -> Dict[str, Any]:
    lead_stub = SimpleNamespace(
        id=abs(hash(str(input_context))) % 100000,
        company_name=input_context.get("company_name"),
        industry=input_context.get("industry"),
        contact_name=input_context.get("contact_name"),
        website="https://example-co.example.com",
        organization=None,
        about_text=None,
        employees=None,
    )
    ctx = LeadContext(
        lead_id=1,
        organization_id=1,
        website="https://example-co.example.com",
        company_name=input_context.get("company_name"),
        industry=input_context.get("industry"),
        contact_name=input_context.get("contact_name"),
    )
    output = _messaging_agent.run(lead_stub, ctx, _DecisionOutputStub(), allow_llm=False)
    result = output.model_dump()
    result["message_nonempty"] = bool(output.email_body and output.email_body.strip())
    result["reasoning"] = output.explanation.reasoning
    result["confidence"] = output.explanation.confidence
    return result


AGENT_RUNNERS = {
    "company_intelligence": run_company_intelligence,
    "qualification": run_qualification,
    "decision": run_decision,
    "messaging": run_messaging,
}
