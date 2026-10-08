"""
C9.3 -- LeadBoost's server-side Mailer conversation client.

Runs the REAL client and transport (core/infrastructure/mailing_agent) against a
scripted httpx.MockTransport, so request construction (method, encoded path,
headers, absence of a body) and every response-handling branch are exercised
for real. Hostile and malformed Mailer responses are fed in directly: the
client must turn each into a closed, safe outcome -- never pass through
anything outside the schema, and never log third-party message text.
"""

import copy
import json
import logging
from typing import Callable, List

import httpx
import pytest

from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent import conversation_client as cc
from core.infrastructure.mailing_agent.conversation_client import (
    MAILER_STATE_ERROR_CODES,
    MailerStateErrorCode,
    conversation_path,
    fetch_conversation,
)
from core.domain.schemas.outreach_mailer_state import MailerStateErrorCodeLiteral
from typing import get_args

ORG = 7
API_KEY = "key-org-7-SECRET"
REAL_ASYNC_CLIENT = httpx.AsyncClient

GOOD = {
    "action": {
        "accepted": True,
        "state": "sent",
        "mailing_agent_reference": "dsp_abc",
        "created_at": "2026-01-01T12:00:00Z",
        "updated_at": "2026-01-01T12:05:00Z",
        "mailbox_reference": "mbx_1",
    },
    "messages": [
        {
            "direction": "outbound",
            "message_type": "initial_outreach",
            "subject": "Quick question",
            "body": "Hello Jamie",
            "body_truncated": False,
            "created_at": "2026-01-01T12:00:00Z",
            "delivery_state": "sent",
            "mailing_agent_reference": "dsp_abc",
            "mailbox_reference": "mbx_1",
        },
        {
            "direction": "inbound",
            "message_type": None,
            "subject": "Re: Quick question",
            "body": "Sounds good",
            "body_truncated": False,
            "created_at": "2026-01-01T13:00:00Z",
            "delivery_state": None,
            "mailing_agent_reference": None,
            "mailbox_reference": "mbx_1",
        },
    ],
    "has_more": False,
}


@pytest.fixture()
def wire(monkeypatch):
    """Install a scripted Mailer. Returns the list of requests the client made."""
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailer.test")
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({str(ORG): API_KEY}))
    requests: List[httpx.Request] = []
    state = {"handler": lambda req: httpx.Response(200, json=GOOD)}

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return state["handler"](request)

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(mc.httpx, "AsyncClient", _factory)

    class Wire:
        def respond(self, fn: Callable[[httpx.Request], httpx.Response]):
            state["handler"] = fn

        def json(self, payload, status=200):
            state["handler"] = lambda req: httpx.Response(status, json=payload)

        def raw(self, content: bytes, status=200):
            state["handler"] = lambda req: httpx.Response(status, content=content)

    w = Wire()
    w.requests = requests
    return w


def run(coro):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(coro)


def fetch(**kw):
    kw.setdefault("organization_id", ORG)
    kw.setdefault("idempotency_key", "idem-1")
    return run(fetch_conversation(**kw))


# ---------------------------------------------------------------- request construction
def test_valid_response_parses_into_strict_models(wire):
    r = fetch()
    assert r.status == "ok" and r.error_code is None
    assert r.conversation.action.state == "sent"
    assert [m.direction for m in r.conversation.messages] == ["outbound", "inbound"]
    assert (
        r.conversation.messages[0].delivery_state == "sent"
        and r.conversation.messages[1].delivery_state is None
    )
    assert r.conversation.has_more is False


def test_request_is_a_bodyless_get_with_only_the_org_key(wire):
    fetch()
    (req,) = wire.requests
    assert req.method == "GET" and req.content == b""
    assert req.headers["x-api-key"] == API_KEY
    assert "content-type" not in req.headers and "authorization" not in req.headers
    assert req.url.host == "mailer.test" and req.url.scheme == "https"
    assert (
        req.url.raw_path.decode() == "/integrations/leadboost/outreach-actions/idem-1/conversation?limit=20"
    )


@pytest.mark.parametrize(
    "key,encoded",
    [
        ("a/b", "a%2Fb"),
        ("a?b#c", "a%3Fb%23c"),
        ("100%", "100%25"),
        ("../../mailboxes", "..%2F..%2Fmailboxes"),
        ("sp ace", "sp%20ace"),
        ("é", "%C3%A9"),
    ],
)
def test_idempotency_key_is_percent_encoded_with_no_safe_characters(wire, key, encoded):
    fetch(idempotency_key=key)
    (req,) = wire.requests
    target = req.url.raw_path.decode()
    assert target == f"/integrations/leadboost/outreach-actions/{encoded}/conversation?limit=20"
    assert conversation_path(key, 20) == target
    assert target.count("/conversation") == 1  # the key can never add or remove a path segment


@pytest.mark.parametrize("asked,sent", [(1, 1), (20, 20), (50, 50), (0, 1), (-5, 1), (999, 50)])
def test_limit_is_clamped_to_the_contract(wire, asked, sent):
    fetch(limit=asked)
    assert wire.requests[0].url.params["limit"] == str(sent)


def test_empty_key_is_refused_before_any_network_call(wire):
    r = fetch(idempotency_key="")
    assert (r.status, r.error_code) == ("error", MailerStateErrorCode.MISSING_IDEMPOTENCY_KEY)
    assert wire.requests == []


# ---------------------------------------------------------------- status mapping
def test_404_is_not_found(wire):
    wire.json({"detail": "Outreach action not found"}, 404)
    assert fetch().status == "not_found"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_is_server_error(wire, status):
    wire.json({"detail": "boom: Traceback ... SMTPAuthenticationError"}, status)
    r = fetch()
    assert (r.status, r.error_code) == ("error", MailerStateErrorCode.SERVER_ERROR)


@pytest.mark.parametrize("status", [400, 401, 403, 409, 422, 429])
def test_other_4xx_is_rejected(wire, status):
    wire.json({"detail": "nope"}, status)
    assert fetch().error_code == MailerStateErrorCode.REJECTED


def test_redirects_are_never_followed(wire):
    wire.respond(lambda req: httpx.Response(302, headers={"Location": "https://evil.example/steal"}))
    r = fetch()
    assert r.error_code == MailerStateErrorCode.REJECTED
    assert len(wire.requests) == 1 and wire.requests[0].url.host == "mailer.test"  # the key never left


def test_timeout_and_unreachable_are_safe_codes_without_raw_text(wire, caplog):
    def boom_timeout(req):
        raise httpx.ReadTimeout("secret-internal-detail-1")

    def boom_connect(req):
        raise httpx.ConnectError("secret-internal-detail-2")

    caplog.set_level(logging.DEBUG)
    wire.respond(boom_timeout)
    assert fetch().error_code == MailerStateErrorCode.TIMEOUT
    wire.respond(boom_connect)
    assert fetch().error_code == MailerStateErrorCode.UNREACHABLE
    assert "secret-internal-detail" not in caplog.text and API_KEY not in caplog.text


def test_not_configured_unmapped_org_and_insecure_transport_fail_closed(wire, monkeypatch):
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"999": "other"}))
    r = fetch()
    assert r.error_code == MailerStateErrorCode.AUTH_NOT_CONFIGURED and wire.requests == []

    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({str(ORG): API_KEY}))
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://mailer.internal")  # plain http, non-local
    assert fetch().error_code == MailerStateErrorCode.INSECURE_TRANSPORT and wire.requests == []

    monkeypatch.delenv("MAILING_AGENT_BASE_URL")
    assert fetch().error_code == MailerStateErrorCode.NOT_CONFIGURED and wire.requests == []


# ---------------------------------------------------------------- strict response validation
def _mutated(fn):
    doc = copy.deepcopy(GOOD)
    fn(doc)
    return doc


BAD_DOCS = {
    "extra top-level field": _mutated(lambda d: d.update(organization_id="o")),
    "extra action field": _mutated(lambda d: d["action"].update(error_message="x")),
    "extra message field": _mutated(lambda d: d["messages"][0].update(message_id_header="<x>")),
    "missing has_more": _mutated(lambda d: d.pop("has_more")),
    "missing action": _mutated(lambda d: d.pop("action")),
    "messages null": _mutated(lambda d: d.update(messages=None)),
    "unknown action state": _mutated(lambda d: d["action"].update(state="generating")),
    "unknown message state": _mutated(lambda d: d["messages"][0].update(delivery_state="delivered")),
    "unknown direction": _mutated(lambda d: d["messages"][0].update(direction="sideways")),
    "unknown message type": _mutated(lambda d: d["messages"][0].update(message_type="spam")),
    "accepted false": _mutated(lambda d: d["action"].update(accepted=False)),
    "accepted missing": _mutated(lambda d: d["action"].pop("accepted")),
    "has_more as int": _mutated(lambda d: d.update(has_more=1)),
    "has_more as string": _mutated(lambda d: d.update(has_more="false")),
    "body_truncated as int": _mutated(lambda d: d["messages"][0].update(body_truncated=0)),
    "body as number": _mutated(lambda d: d["messages"][0].update(body=123)),
    "reference as number": _mutated(lambda d: d["action"].update(mailing_agent_reference=5)),
    "created_at garbage": _mutated(lambda d: d["action"].update(created_at="not-a-date")),
    "body over the 20k cap": _mutated(lambda d: d["messages"][0].update(body="x" * 20_001)),
    "outbound without delivery_state": _mutated(lambda d: d["messages"][0].update(delivery_state=None)),
    "inbound with delivery_state": _mutated(lambda d: d["messages"][1].update(delivery_state="sent")),
    "more than 50 messages": _mutated(lambda d: d.update(messages=[d["messages"][1]] * 51)),
    "more messages than requested (20)": _mutated(lambda d: d.update(messages=[d["messages"][1]] * 21)),
}


@pytest.mark.parametrize("name", list(BAD_DOCS))
def test_schema_drift_is_invalid_response_never_passed_through(wire, name):
    wire.json(BAD_DOCS[name])
    r = fetch()
    assert (r.status, r.error_code, r.conversation) == ("error", MailerStateErrorCode.INVALID_RESPONSE, None)


@pytest.mark.parametrize(
    "content", [b"", b"not json", b"[]", b"null", b'"a string"', b"123", b"<html>502</html>"]
)
def test_non_object_or_non_json_bodies_are_invalid_response(wire, content):
    wire.raw(content)
    assert fetch().error_code == MailerStateErrorCode.INVALID_RESPONSE


def test_the_requested_window_boundary_is_accepted(wire):
    wire.json(_mutated(lambda d: d.update(messages=[d["messages"][1]] * 20)))
    assert len(fetch(limit=20).conversation.messages) == 20
    wire.json(_mutated(lambda d: d.update(messages=[d["messages"][1]] * 50)))
    assert len(fetch(limit=50).conversation.messages) == 50
    assert len(copy.deepcopy(GOOD)["messages"]) <= 20


def test_a_body_exactly_at_the_cap_is_accepted(wire):
    wire.json(_mutated(lambda d: d["messages"][0].update(body="x" * 20_000, body_truncated=True)))
    assert len(fetch().conversation.messages[0].body) == 20_000


def test_third_party_message_text_is_never_logged(wire, caplog):
    sentinel = "INBOUND-ATTACKER-TEXT-SENTINEL"
    wire.json(_mutated(lambda d: d["messages"][1].update(body=sentinel, direction="sideways")))
    caplog.set_level(logging.DEBUG)
    assert fetch().error_code == MailerStateErrorCode.INVALID_RESPONSE
    assert sentinel not in caplog.text and API_KEY not in caplog.text
    # ...nor in the structured fields of any log record
    assert all(sentinel not in repr(vars(rec)) for rec in caplog.records)


# ---------------------------------------------------------------- vocabularies
def test_error_codes_are_a_closed_set_matching_the_public_schema_literal():
    assert set(MAILER_STATE_ERROR_CODES) == set(get_args(MailerStateErrorCodeLiteral))
    assert len(set(MAILER_STATE_ERROR_CODES)) == len(MAILER_STATE_ERROR_CODES)
    assert all(c.startswith("mailing_agent_") for c in MAILER_STATE_ERROR_CODES)


def test_every_failure_path_emits_only_a_member_of_the_closed_set(wire):
    outcomes = set()
    for status in (500, 401, 422):
        wire.json({"detail": "x"}, status)
        outcomes.add(fetch().error_code)
    wire.raw(b"junk")
    outcomes.add(fetch().error_code)
    wire.respond(lambda req: (_ for _ in ()).throw(httpx.ConnectError("x")))
    outcomes.add(fetch().error_code)
    assert outcomes <= set(MAILER_STATE_ERROR_CODES)


def test_wire_models_match_the_mailers_declared_shape():
    assert set(cc.MailerConversation.model_fields) == {"action", "messages", "has_more"}
    assert set(cc.MailerConversationAction.model_fields) == {
        "accepted",
        "state",
        "mailing_agent_reference",
        "created_at",
        "updated_at",
        "mailbox_reference",
    }
    assert set(cc.MailerConversationMessage.model_fields) == {
        "direction",
        "message_type",
        "subject",
        "body",
        "body_truncated",
        "created_at",
        "delivery_state",
        "mailing_agent_reference",
        "mailbox_reference",
    }
    for model in (cc.MailerConversation, cc.MailerConversationAction, cc.MailerConversationMessage):
        assert model.model_config.get("extra") == "forbid"
