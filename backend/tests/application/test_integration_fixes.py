"""
Regression tests for the LeadBoost backend integration-fix pass.

Covers, in order:
  1. Scraper -> Lead persistence: twitter_url/facebook_url/address (audit
     items #1, #2) alongside the pre-existing linkedin_url path.
  2. AIDecisionLog -> API "ai_insights" reconstruction (audit items #3-#7):
     Company Intelligence, Decision, Evaluation, Review, Messaging.
  3. POST /leads/{id}/process actually returning the computed
     PipelineResult instead of discarding it (audit item #10).
  4. GET /leads/{id} exposing ai_insights end-to-end over real HTTP.
  5. sender_org correctly reaching the deterministic messaging fallback
     (audit item #8 -- verified already-correct; this locks it in).

The scraper/LLM are never hit over the network: GROQ_API_KEY is unset by
conftest.py (forcing every agent's deterministic/heuristic path), and the
scraper is monkeypatched exactly like the existing tests in
test_lead_pipeline.py.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from application.services import infra_adapters
from application.workflows.graph_nodes import LeadPipelineNodes
from application.workflows.lead_pipeline import LeadPipeline
from core.infrastructure.database.crud import get_lead
from core.infrastructure.scraping.scraper import ScrapingMethod

import main


@dataclass
class _FakeScrapingResult:
    success: bool
    data: Dict[str, Any]
    method: ScrapingMethod = ScrapingMethod.STRUCTURED_DATA
    confidence: float = 0.8
    processing_time: float = 0.1
    error_message: Optional[str] = None
    pages_scraped: int = 1
    blocked_detected: bool = False
    tiers_attempted: List[str] = field(default_factory=list)


FULL_SCRAPE_DATA = {
    "title": "Acme Robotics",
    "description": "Industrial automation and robotics for manufacturers.",
    "email": "sales@acme.com",
    "linkedin_url": "https://linkedin.com/company/acme",
    "twitter_url": "https://twitter.com/acmerobotics",
    "facebook_url": "https://facebook.com/acmerobotics",
    "address": "500 Robotics Way, Pittsburgh, PA",
    "text_content": "We are hiring across engineering as we expand after our Series B.",
}


@pytest.fixture()
def mock_full_scrape(monkeypatch):
    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(success=True, data=dict(FULL_SCRAPE_DATA))

    monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)


# -- 1. Scrape node persistence: twitter/facebook/address --------------------


async def test_scrape_node_persists_social_and_address(db_session, sample_lead, mock_full_scrape):
    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}

    result = await nodes.scrape(state)

    assert result["errors"] == []
    refreshed = get_lead(db_session, sample_lead.id)
    # Pre-existing behavior (must still work)
    assert refreshed.linkedin_url == "https://linkedin.com/company/acme"
    # Bug fix under test
    assert refreshed.twitter_url == "https://twitter.com/acmerobotics"
    assert refreshed.facebook_url == "https://facebook.com/acmerobotics"
    assert refreshed.address == "500 Robotics Way, Pittsburgh, PA"


async def test_scrape_node_falls_back_to_links_list_for_social_urls(db_session, sample_lead, monkeypatch):
    """When the scraper hasn't set the dedicated twitter_url/facebook_url
    keys but a matching URL is present in the generic `links` list, the
    same fallback already used for linkedin_url must apply."""

    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(
            success=True,
            data={
                "title": "Acme Robotics",
                "links": [
                    "https://twitter.com/acmerobotics",
                    "https://facebook.com/acmerobotics",
                    "https://linkedin.com/company/acme",
                ],
            },
        )

    monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)

    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}
    await nodes.scrape(state)

    refreshed = get_lead(db_session, sample_lead.id)
    assert refreshed.twitter_url == "https://twitter.com/acmerobotics"
    assert refreshed.facebook_url == "https://facebook.com/acmerobotics"


async def test_scrape_node_does_not_overwrite_address_when_absent(db_session, sample_lead, monkeypatch):
    """A scrape that finds no address must not clobber a previously-known
    one -- update_lead only sets keys present in update_fields."""
    from core.domain.schemas.lead import LeadUpdate
    from core.infrastructure.database.crud import update_lead

    update_lead(db_session, sample_lead.id, LeadUpdate(address="Pre-existing Address"))

    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(success=True, data={"title": "Acme Robotics"})

    monkeypatch.setattr(infra_adapters, "scrape_lead", _fake_scrape_lead)

    nodes = LeadPipelineNodes(db_session)
    state = {"lead_id": sample_lead.id, "stage_timings_ms": {}, "errors": []}
    await nodes.scrape(state)

    refreshed = get_lead(db_session, sample_lead.id)
    assert refreshed.address == "Pre-existing Address"


# -- 2. AIDecisionLog -> ai_insights reconstruction ---------------------------


async def test_get_lead_ai_insights_reconstructs_all_stage_outputs(
    db_session, sample_lead, mock_full_scrape
):
    """Runs the real pipeline end-to-end (deterministic/heuristic path,
    no network) and verifies get_lead_ai_insights() surfaces the exact
    outputs each stage already computed and stored -- not a fabricated
    reshaping of them."""
    pipeline = LeadPipeline(db_session)
    result = await pipeline.execute(sample_lead.id)
    assert result.status.value in ("SUCCESS", "PARTIAL_SUCCESS")

    insights = infra_adapters.get_lead_ai_insights(db_session, sample_lead.id)

    # Company Intelligence: technology_signals must be reachable here too
    # (audit item #3 -- resolved via this path, no new DB column).
    assert insights["company_intelligence"] is not None
    assert insights["company_intelligence"]["technology_signals"] == (
        result.company_intelligence.technology_signals
    )
    assert insights["company_intelligence"]["explanation"]["reasoning"] == (
        result.company_intelligence.explanation.reasoning
    )

    # Decision: qualification/recommended_action/reasoning (audit item #5)
    assert insights["decision"] is not None
    assert insights["decision"]["qualification"] == result.decision.qualification
    assert insights["decision"]["recommended_action"] == result.decision.recommended_action
    assert insights["decision"]["explanation"]["reasoning"] == result.decision.explanation.reasoning

    # Evaluation: full confidence/completeness/grounding/consistency breakdown
    # (audit item #6)
    assert insights["evaluation"] is not None
    assert insights["evaluation"]["completeness"] == pytest.approx(result.evaluation.completeness)
    assert insights["evaluation"]["grounding"] == pytest.approx(result.evaluation.grounding)
    assert insights["evaluation"]["consistency"] == pytest.approx(result.evaluation.consistency)
    assert insights["evaluation"]["overall"] == pytest.approx(result.evaluation.overall)

    # Review
    assert insights["review"] is not None
    assert insights["review"]["decision"] == result.review.decision

    # Messaging: full output, not just the outreach_message body
    # (audit item #7)
    if result.message is not None:
        assert insights["messaging"] is not None
        assert insights["messaging"]["email_body"] == result.message.email_body
        assert insights["messaging"]["channel_notes"] == result.message.channel_notes


async def test_get_lead_ai_insights_returns_none_for_stages_not_yet_run(db_session, sample_lead):
    """A lead that hasn't been through the pipeline yet must not error --
    every stage should come back None."""
    insights = infra_adapters.get_lead_ai_insights(db_session, sample_lead.id)
    assert insights == {
        "company_intelligence": None,
        "decision": None,
        "evaluation": None,
        "review": None,
        "messaging": None,
    }


async def test_get_lead_ai_insights_messaging_is_none_when_routed_to_human_review(
    db_session, sample_lead, mock_full_scrape, monkeypatch
):
    """Message generation is intentionally skipped for human_review leads
    (see graph_nodes.message_generation) -- ai_insights must reflect that
    as None, not a fabricated/empty message."""
    import application.agents.review_agent as review_module

    monkeypatch.setattr(
        review_module.ReviewAgent,
        "run",
        lambda self, evaluation: __import__(
            "application.dto.models", fromlist=["ReviewOutput"]
        ).ReviewOutput(decision="human_review", reason="forced for test"),
    )

    pipeline = LeadPipeline(db_session)
    await pipeline.execute(sample_lead.id)

    insights = infra_adapters.get_lead_ai_insights(db_session, sample_lead.id)
    assert insights["review"]["decision"] == "human_review"
    assert insights["messaging"] is None


# -- 3 & 4. API-level: /process response + GET /{id} ai_insights -------------


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


def _register_and_login(client, email):
    r = client.post(
        "/api/v2/register",
        json={"email": email, "password": "TestPass123!", "first_name": "Ada"},
    )
    assert r.status_code == 200, r.text
    token = None
    r2 = client.post("/api/v2/login", data={"username": email, "password": "TestPass123!"})
    assert r2.status_code == 200, r2.text
    token = r2.json()["access_token"]
    return token


def _org_and_owner_id(client, token):
    r = client.get("/api/v2/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["organization_id"], body["id"]


def test_process_endpoint_returns_pipeline_result_not_placeholder(client, monkeypatch):
    """Audit item #10: POST /leads/{id}/process previously computed the
    full PipelineResult and then discarded it, always responding with a
    static placeholder. It must now return the actual result."""
    from application.services import infra_adapters as ia

    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(success=True, data=dict(FULL_SCRAPE_DATA))

    monkeypatch.setattr(ia, "scrape_lead", _fake_scrape_lead)

    token = _register_and_login(client, "process_result@example.com")
    org_id, owner_id = _org_and_owner_id(client, token)

    r = client.post(
        "/api/v2/leads/single",
        json={"website": "https://acme-process-test.com", "organization_id": org_id, "owner_id": owner_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    lead_id = r.json()["id"]

    r = client.post(f"/api/v2/leads/{lead_id}/process", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()

    # Backward-compatible keys preserved
    assert body["lead_id"] == lead_id
    assert "message" in body

    # New, previously-discarded data now present
    assert "result" in body
    assert body["result"]["decision"] is not None
    assert body["result"]["evaluation"] is not None
    assert body["result"]["company_intelligence"] is not None


def test_get_lead_detail_includes_ai_insights_after_processing(client, monkeypatch):
    from application.services import infra_adapters as ia

    async def _fake_scrape_lead(url: str):
        return _FakeScrapingResult(success=True, data=dict(FULL_SCRAPE_DATA))

    monkeypatch.setattr(ia, "scrape_lead", _fake_scrape_lead)

    token = _register_and_login(client, "detail_ai_insights@example.com")
    org_id, owner_id = _org_and_owner_id(client, token)

    r = client.post(
        "/api/v2/leads/single",
        json={"website": "https://acme-detail-test.com", "organization_id": org_id, "owner_id": owner_id},
        headers={"Authorization": f"Bearer {token}"},
    )
    lead_id = r.json()["id"]

    r = client.post(f"/api/v2/leads/{lead_id}/process", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/v2/leads/{lead_id}", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()

    # Existing Lead fields must still be present/unchanged (backward compat)
    assert body["website"] == "https://acme-detail-test.com"
    assert body["twitter_url"] == "https://twitter.com/acmerobotics"
    assert body["facebook_url"] == "https://facebook.com/acmerobotics"
    assert body["address"] == "500 Robotics Way, Pittsburgh, PA"

    # New field
    assert "ai_insights" in body
    assert body["ai_insights"]["decision"] is not None
    assert body["ai_insights"]["evaluation"] is not None
    assert body["ai_insights"]["company_intelligence"] is not None


# -- 5. sender_org reaching the deterministic messaging fallback -------------


def test_messaging_fallback_uses_lead_organization_name(db_session, sample_lead):
    """Audit item #8: verifies infra_adapters.generate_template_message()
    correctly threads the lead's organization name into
    Messenger.generate_message() (via Messenger(sender_org=...)), even
    though the LLM path is unavailable. This is a lock-in regression
    test for behavior found to already be correct on inspection -- no
    production code changed for this item."""
    sample_lead.organization.name = "Beacon Analytics"
    db_session.commit()

    message = infra_adapters.generate_template_message(sample_lead)

    assert message is not None
    assert "Beacon Analytics" in message


async def test_message_generation_node_fallback_persists_sender_org_message(
    db_session, sample_lead, mock_full_scrape
):
    """End-to-end through the actual pipeline node (not just the adapter
    function directly): with no LLM available, the persisted
    outreach_message must reflect the lead's real organization name."""
    sample_lead.organization.name = "Beacon Analytics"
    db_session.commit()

    nodes = LeadPipelineNodes(db_session)
    state = {
        "lead_id": sample_lead.id,
        "organization_id": sample_lead.organization_id,
        "ai_features_enabled": True,
        "review": {"decision": "auto_approved"},
        "context": {},
        "decision": {},
        "stage_timings_ms": {},
        "errors": [],
    }

    result = await nodes.message_generation(state)

    assert result["message"]["source"] == "template"
    assert "Beacon Analytics" in result["message"]["email_body"]

    refreshed = get_lead(db_session, sample_lead.id)
    assert "Beacon Analytics" in refreshed.outreach_message


# -- 6. Model registration order-independence (found via a real, isolated ---
# -- pytest run on the developer's machine: sqlalchemy.exc.InvalidRequestError
# -- "failed to locate a name ('APIKey')" at mapper configuration) -----------


def test_importing_database_module_alone_registers_every_model():
    """Regression test for a real bug: running exactly one test from
    test_subscription_service.py in isolation (no other test file
    imported first) used to raise sqlalchemy.exc.InvalidRequestError,
    because core/domain/models/__init__.py never imported every model,
    so whether SQLAlchemy's string-based `relationship("APIKey")`
    references resolved depended entirely on which unrelated module
    happened to import api_key.py first.

    A subprocess-spawning version of this test (running a real, separate
    `pytest` invocation to reproduce the exact "fresh interpreter"
    condition the original bug needed) was tried first and dropped: each
    subprocess re-imports this project's full dependency stack from
    scratch (~15-25s), which made it liable to be killed by an outer
    `timeout` wrapper around a whole test file/session -- exactly what
    happened in practice. This in-process version is sufficient going
    forward because the fix itself (core/domain/models/__init__.py
    importing every model, pulled in unconditionally by
    core.infrastructure.database) no longer depends on *what already
    happened to be imported* in any given process -- so there's nothing
    left for a fresh-process reproduction to prove that this doesn't
    already cover: importing only core.infrastructure.database (the
    common entry point) must be enough to register every model with
    SQLAlchemy, regardless of whether anything else has imported
    core.domain.models.api_key yet."""
    from sqlalchemy.orm import configure_mappers

    from core.infrastructure.database import Base

    configure_mappers()  # must not raise
    assert "APIKey" in Base.registry._class_registry
    assert "Lead" in Base.registry._class_registry
    assert "Plan" in Base.registry._class_registry


# -- 7. business_search_fallback=None sentinel bug (found via a real, --------
# -- previously-hanging pytest run of test_fallback_disabled_when_explicitly_
# -- none, and a real failure of test_search_provider_failure_degrades_
# -- gracefully on the developer's machine) -----------------------------------


def test_business_search_fallback_none_is_actually_disabled_not_replaced_with_default():
    """DiscoveryService(business_search_fallback=None) must leave
    self.business_search_fallback as None (disabled), not silently swap
    in a real SerperBusinessSearchProvider() -- the bug was that a plain
    `None` default made "explicitly disabled" indistinguishable from
    "argument omitted"."""
    from application.discovery.discovery_service import DiscoveryService

    service = DiscoveryService(db=None, business_search_fallback=None)
    assert service.business_search_fallback is None
    assert service._owns_business_search_fallback is False


def test_business_search_fallback_omitted_still_gets_the_real_default():
    """The other half of the same fix: callers that don't pass
    business_search_fallback at all must still get the real
    SerperBusinessSearchProvider() default -- unchanged behavior for
    every existing caller that relies on it."""
    from application.discovery.discovery_service import DiscoveryService
    from application.discovery.providers.serper_provider import SerperBusinessSearchProvider

    service = DiscoveryService(db=None)
    assert isinstance(service.business_search_fallback, SerperBusinessSearchProvider)
    assert service._owns_business_search_fallback is True


def test_business_search_fallback_explicit_instance_is_used_as_is():
    """A caller-supplied provider instance must be used directly (and
    not treated as 'owned', since the caller controls its lifecycle)."""
    from application.discovery.discovery_service import DiscoveryService
    from tests.application.discovery.test_discovery_service import _StubBusinessSearchFallback

    stub = _StubBusinessSearchFallback([])
    service = DiscoveryService(db=None, business_search_fallback=stub)
    assert service.business_search_fallback is stub
    assert service._owns_business_search_fallback is False


# -- 8. sender_org missing-variable bug in the LLM messaging path (found ----
# -- from a real dev-server log: EVERY LLM message generation call was ------
# -- failing with "Input to ChatPromptTemplate is missing variables --------
# -- {'sender_org'}", silently falling back to the template path every time)-


def test_llm_human_prompt_has_no_unformatted_placeholders():
    """Messenger._get_human_prompt() builds a string that gets fed
    straight into langchain's ChatPromptTemplate, which treats any
    literal `{xxx}` left in it as a template variable to fill at invoke
    time. One line in the trailing sentence was a plain (non-f) string
    literal in the middle of an f-string concatenation chain, so
    `{sender_org}` never got substituted -- ChatPromptTemplate correctly
    detected the leftover placeholder and refused to invoke with no
    variables. Runs the exact real langchain call this failed at,
    without needing a network call (invoke() alone does the variable
    substitution/validation -- no LLM call happens until after that)."""
    from langchain_core.prompts import ChatPromptTemplate

    from core.infrastructure.messaging.messenger import Messenger

    messenger = Messenger(sender_org="Acme Analytics")
    context = {
        "sender_org": "Acme Analytics",
        "company_name": "Beacon Robotics",
        "industry": "software",
        "website": "https://beacon.example.com",
        "about_text": "Beacon builds robots.",
        "contact_name": None,
        "employees": None,
    }

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", messenger._get_system_prompt()),
            ("human", messenger._get_human_prompt(context)),
        ]
    )

    formatted = prompt.invoke({})  # must not raise
    rendered = "\n".join(m.content for m in formatted.to_messages())
    assert "{sender_org}" not in rendered
    assert "Acme Analytics" in rendered


# -- 8b. A second, distinct trigger for the *same* ChatPromptTemplate -------
# -- hazard as #8 above, found via this audit's own server.log: all 10 of ---
# -- 10 real LLM message-generation attempts failed with the identical -----
# -- "missing variables {'sender_org'}" error, even though the sender_org --
# -- f-string bug (#8) was already fixed. Root cause: _get_human_prompt() --
# -- embeds up to 200 raw characters of scraped `about_text` into the -------
# -- string it hands to ChatPromptTemplate.from_messages(); ChatPromptTemplate
# -- re-parses that already-fully-rendered string as *its own* template, so --
# -- any curly brace surviving in real scraped website content (JSON-LD, ---
# -- CSS, code samples -- all common on real sites) is misread as a required
# -- template variable that chain.invoke({}) (an empty dict) can never -----
# -- satisfy. Fixed by dropping ChatPromptTemplate entirely for this call: --
# -- both strings are already fully rendered, so they're passed straight to
# -- the chat model as a plain message list instead of being re-templated. -


def test_llm_message_generation_survives_braces_in_scraped_about_text(monkeypatch):
    """Regression test: about_text containing literal curly braces (as
    commonly appears in real scraped website content) must not break LLM
    message generation. Reproduces the exact failure mode from this
    audit's server.log using synthetic content shaped like real scraped
    JSON/CSS leftovers, with a mocked ChatGroq.invoke (no network call)."""
    from unittest.mock import MagicMock, patch

    from core.infrastructure.messaging.messenger import Messenger

    class _FakeLead:
        id = 1
        company_name = "Acme Robotics"
        industry = "Software"
        about_text = (
            'Acme Robotics {"builds": "industrial automation"} software '
            "for factories, see our {sender_org} program."
        )
        contact_name = "Jane Doe"
        employees = "50-100"
        website = "https://acme-robotics.example.com"

    monkeypatch.setenv("GROQ_API_KEY", "fake-key-for-test")

    fake_response = MagicMock()
    fake_response.content = "Hi Jane, loved what Acme Robotics is building..."

    with patch("langchain_groq.ChatGroq.invoke", return_value=fake_response) as mock_invoke:
        messenger = Messenger(sender_org="API Tester's Organization")
        result = messenger._generate_llm_message(_FakeLead())

    assert result == "Hi Jane, loved what Acme Robotics is building..."
    # The message list passed to the chat model must be the plain,
    # already-rendered strings -- not run back through any templating.
    messages = mock_invoke.call_args[0][0]
    assert messages[0][0] == "system"
    assert messages[1][0] == "human"
    assert "API Tester's Organization" in messages[1][1]


# -- 9. DiscoveryService aiohttp session leak (found from a real dev-server -
# -- log: recurring "Unclosed client session"/"Unclosed connector" ----------
# -- warnings on every /discovery/search call) -------------------------------


def test_discovery_search_endpoint_closes_its_service_on_success(monkeypatch):
    """POST /discovery/search creates a fresh DiscoveryService per
    request but never called its own aclose() (which exists
    specifically to close the aiohttp session the service's default
    sub-components lazily create) -- every call leaked one
    ClientSession + TCPConnector, only ever cleaned up later by the
    garbage collector. Verifies aclose() is called via the real
    endpoint code path (not by re-implementing it), for both the
    success and the error exit paths."""
    import api.endpoints.discovery as discovery_endpoint

    calls = {"aclose": 0}

    class _RecordingStub:
        def __init__(self, db):
            pass

        async def discover_and_create_leads(self, **kwargs):
            from application.discovery.dto import DiscoveryResponse

            return DiscoveryResponse(
                query=kwargs["query"], category="c", location="l",
                requested_limit=1, businesses_found=0, businesses=[], duration_ms=1,
            )

        async def aclose(self):
            calls["aclose"] += 1

    monkeypatch.setattr(discovery_endpoint, "DiscoveryService", _RecordingStub)

    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        r = client.post(
            "/api/v2/register",
            json={"email": "discovery_leak@example.com", "password": "TestPass123!", "first_name": "A"},
        )
        assert r.status_code == 200, r.text
        token = client.post(
            "/api/v2/login", data={"username": "discovery_leak@example.com", "password": "TestPass123!"}
        ).json()["access_token"]

        r = client.post(
            "/api/v2/discovery/search",
            json={"query": "Coffee shops in Pune"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, r.text

    assert calls["aclose"] == 1


def test_discovery_search_endpoint_closes_its_service_on_error(monkeypatch):
    """Same as above, but for the exit path where discover_and_create_leads
    raises -- aclose() must still run (it's in a `finally` block)."""
    import api.endpoints.discovery as discovery_endpoint

    calls = {"aclose": 0}

    class _RecordingFailingStub:
        def __init__(self, db):
            pass

        async def discover_and_create_leads(self, **kwargs):
            raise RuntimeError("simulated failure")

        async def aclose(self):
            calls["aclose"] += 1

    monkeypatch.setattr(discovery_endpoint, "DiscoveryService", _RecordingFailingStub)

    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        r = client.post(
            "/api/v2/register",
            json={"email": "discovery_leak_err@example.com", "password": "TestPass123!", "first_name": "A"},
        )
        assert r.status_code == 200, r.text
        token = client.post(
            "/api/v2/login", data={"username": "discovery_leak_err@example.com", "password": "TestPass123!"}
        ).json()["access_token"]

        r = client.post(
            "/api/v2/discovery/search",
            json={"query": "Coffee shops in Pune"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 502

    assert calls["aclose"] == 1
