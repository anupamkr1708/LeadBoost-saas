"""
Tests for application.context.context_builder.ContextBuilder.
"""

from application.context.context_builder import ContextBuilder


def test_build_populates_core_lead_fields(db_session, sample_lead):
    builder = ContextBuilder(db_session)
    context = builder.build(sample_lead)

    assert context.lead_id == sample_lead.id
    assert context.organization_id == sample_lead.organization_id
    assert context.company_name == "Example Co"
    assert context.website == "https://example.com"
    assert context.industry == "Software"
    assert context.email == "hello@example.com"


def test_build_includes_organization_name(db_session, sample_lead, sample_org):
    builder = ContextBuilder(db_session)
    context = builder.build(sample_lead)
    assert context.organization_name == sample_org.name


def test_sender_org_prefers_organization_db_profile_over_env_var(db_session, sample_lead, sample_org, monkeypatch):
    """PART 7: outreach must be personalized per-tenant using the
    organization's own profile in the database, not always the same
    global SENDER_ORG env var."""
    monkeypatch.setenv("SENDER_ORG", "Fallback Co")
    assert sample_org.name == "Test Org"  # from the sample_org fixture

    builder = ContextBuilder(db_session)
    context = builder.build(sample_lead)

    assert context.sender_org == "Test Org"


def test_sender_org_falls_back_to_env_var_when_organization_has_no_name(
    db_session, sample_lead, sample_org, monkeypatch
):
    monkeypatch.setenv("SENDER_ORG", "Fallback Co")
    sample_org.name = ""
    db_session.commit()

    builder = ContextBuilder(db_session)
    context = builder.build(sample_lead)

    assert context.sender_org == "Fallback Co"


def test_build_defaults_crm_history_to_empty_list(db_session, sample_lead):
    builder = ContextBuilder(db_session)
    context = builder.build(sample_lead)
    assert context.crm_history == []


def test_build_merges_scraped_and_enriched_data(db_session, sample_lead):
    builder = ContextBuilder(db_session)
    scraped = {"text_content": "We build robots for factories."}
    enriched = {"industry": "Robotics"}
    context = builder.build(sample_lead, scraped_data=scraped, enriched_data=enriched)

    assert context.scraped_data == scraped
    assert context.enriched_data == enriched


def test_build_picks_up_previous_ai_decision_logs_as_memory(db_session, sample_lead):
    from core.infrastructure.database.crud import create_ai_decision_log

    create_ai_decision_log(
        db_session,
        lead_id=sample_lead.id,
        organization_id=sample_lead.organization_id,
        stage="company_intelligence",
        agent_name="company_intelligence_agent",
        output_data='{"industry_analysis": "B2B SaaS"}',
        reasoning="Prior analysis",
        confidence=0.9,
    )

    builder = ContextBuilder(db_session)
    context = builder.build(sample_lead)

    assert context.previous_company_analysis is not None
    assert context.previous_company_analysis["reasoning"] == "Prior analysis"


def test_analysis_text_includes_key_fields(db_session, sample_lead):
    builder = ContextBuilder(db_session)
    context = builder.build(
        sample_lead, scraped_data={"text_content": "Robots for manufacturers."}
    )
    text = context.analysis_text()

    assert "Example Co" in text
    assert "https://example.com" in text
    assert "Software" in text
    assert "Robots for manufacturers." in text
