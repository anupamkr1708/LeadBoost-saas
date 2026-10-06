"""
L1: tests for the Mailer HTTP client -- core/infrastructure/mailing_agent/client.py.

Real httpx request objects flow through an httpx.MockTransport, so these assert
the ACTUAL bytes that would go on the wire (method, path, headers, JSON body),
not a mock's call arguments. No network is touched.

Proves:
  * the credential-bearing P1.4 contract is gone (no function, no parameter)
  * the dispatch body is exactly Mailer's M2 shape and contains no sender /
    SMTP / credential / message / tenant / mailbox field, at any depth
  * per-organization key resolution (fail closed), X-API-Key, no Bearer
  * transport rules (https or loopback only), no redirects, safe error codes
  * strict response handling and idempotent, byte-stable payloads
"""

import json
import math
from typing import Callable, List

import httpx
import pytest

from core.infrastructure.mailing_agent import client as mc
from core.infrastructure.mailing_agent.client import (
    DEFAULT_TIMEOUT_SECONDS,
    DispatchErrorCode,
    _is_secure_transport,
    _timeout_seconds,
    build_generated_outreach_payload,
    org_api_key,
    send_request,
    submit_generated_outreach,
)

ORG = 7
KEY = "mailer-key-for-org-7"
_REAL_ASYNC_CLIENT = httpx.AsyncClient  # captured before any test patches it


@pytest.fixture()
def wire(monkeypatch):
    """Route every httpx.AsyncClient in the client module through a MockTransport.
    Returns (requests_seen, set_handler)."""
    seen: List[httpx.Request] = []
    state = {"handler": lambda req: httpx.Response(202, json={"accepted": True, "mailing_agent_reference": "ref-1"})}

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return state["handler"](request)

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(mc.httpx, "AsyncClient", _factory)
    monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailer.example.com")
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({str(ORG): KEY, "8": "other-key"}))
    monkeypatch.delenv("MAILING_AGENT_API_KEY", raising=False)

    def set_handler(fn: Callable[[httpx.Request], httpx.Response]):
        state["handler"] = fn

    return seen, set_handler


def _kwargs(**over):
    base = dict(
        organization_id=ORG,
        outreach_action_id=42,
        idempotency_key="auto:abc123",
        correlation_id="corr-1",
        recipient_email="lead@example.com",
        recipient_name="Ada Lovelace",
        recipient_title="CTO",
        recipient_company="Analytical Engines",
        value_proposition="We help teams ship reliable software.",
        recipient_facts=["Industry: software", "Founded: 1843"],
    )
    base.update(over)
    return base


def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


# --------------------------------------------------------------------------
# The old credential-bearing contract no longer exists
# --------------------------------------------------------------------------
class TestLegacyContractRemoved:
    def test_old_dispatch_function_is_gone(self):
        import core.infrastructure.mailing_agent as pkg

        assert not hasattr(mc, "dispatch_outreach_action")
        assert not hasattr(pkg, "dispatch_outreach_action")

    def test_new_function_cannot_even_accept_a_credential_or_sender(self):
        import inspect

        params = set(inspect.signature(submit_generated_outreach).parameters)
        forbidden = {"plaintext_credential", "credential", "smtp_host", "smtp_username", "security_mode",
                     "sender_email_address", "subject", "body"}
        assert not (params & forbidden)

    async def test_legacy_single_api_key_is_ignored(self, wire, monkeypatch):
        seen, _ = wire
        monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS")
        monkeypatch.setenv("MAILING_AGENT_API_KEY", "legacy-shared-key")
        res = await submit_generated_outreach(**_kwargs())
        assert res.error_code == DispatchErrorCode.AUTH_NOT_CONFIGURED
        assert seen == []


# --------------------------------------------------------------------------
# Payload: exact M2 shape, nothing else
# --------------------------------------------------------------------------
class TestGeneratedOutreachPayload:
    async def test_exact_wire_request(self, wire):
        seen, _ = wire
        res = await submit_generated_outreach(**_kwargs())
        assert res.accepted is True and res.mailing_agent_reference == "ref-1"

        assert len(seen) == 1
        req = seen[0]
        assert req.method == "POST"
        assert str(req.url) == "https://mailer.example.com/integrations/leadboost/outreach-requests"
        assert req.headers["x-api-key"] == KEY
        assert "authorization" not in req.headers
        assert json.loads(req.content) == {
            "external_action_id": "42",
            "idempotency_key": "auto:abc123",
            "correlation_id": "corr-1",
            "recipient": {"email": "lead@example.com", "name": "Ada Lovelace", "title": "CTO",
                          "company": "Analytical Engines"},
            "context": {"value_proposition": "We help teams ship reliable software.",
                        "recipient_facts": ["Industry: software", "Founded: 1843"]},
        }

    async def test_no_credential_sender_message_tenant_or_mailbox_field_anywhere(self, wire):
        seen, _ = wire
        await submit_generated_outreach(**_kwargs())
        body = json.loads(seen[0].content)
        keys = {k.lower() for k in _all_keys(body)}
        banned = {"sender", "smtp_host", "smtp_port", "smtp_username", "smtp_password", "password", "credential",
                  "encrypted_credential", "credential_type", "security_mode", "subject", "body", "message",
                  "organization_id", "tenant", "mailbox", "mailbox_reference", "mailbox_ref", "imap_password"}
        assert keys.isdisjoint(banned), keys & banned
        # and the wire headers carry no secret other than the Mailer API key
        assert set(seen[0].headers) <= {"host", "accept", "accept-encoding", "connection", "user-agent",
                                        "x-api-key", "content-type", "content-length"}

    def test_optional_fields_are_omitted_not_sent_empty(self):
        body = build_generated_outreach_payload(
            outreach_action_id=1, idempotency_key="k", correlation_id=None, recipient_email="a@b.co",
            recipient_name="  ", recipient_title=None, recipient_company="", value_proposition="vp",
            recipient_facts=["", "  ", "real fact"],
        )
        assert body == {
            "external_action_id": "1", "idempotency_key": "k",
            "recipient": {"email": "a@b.co"},
            "context": {"value_proposition": "vp", "recipient_facts": ["real fact"]},
        }

    def test_values_are_bounded_to_mailers_limits_instead_of_422(self):
        body = build_generated_outreach_payload(
            outreach_action_id=1, idempotency_key="k", correlation_id=None, recipient_email="a@b.co",
            recipient_name="n" * 500, recipient_title="t" * 500, recipient_company="c" * 500,
            value_proposition="v" * 5000, recipient_facts=["f" * 900] * 12,
        )
        assert len(body["context"]["value_proposition"]) == 2000
        assert len(body["context"]["recipient_facts"]) == 8
        assert all(len(f) == 500 for f in body["context"]["recipient_facts"])
        assert all(len(body["recipient"][k]) == 200 for k in ("name", "title", "company"))

    async def test_retry_sends_a_byte_identical_idempotent_request(self, wire):
        seen, _ = wire
        await submit_generated_outreach(**_kwargs())
        await submit_generated_outreach(**_kwargs())
        assert seen[0].content == seen[1].content
        assert json.loads(seen[0].content)["idempotency_key"] == "auto:abc123"

    async def test_empty_idempotency_key_is_refused_before_any_request(self, wire):
        seen, _ = wire
        res = await submit_generated_outreach(**_kwargs(idempotency_key=""))
        assert res.error_code == DispatchErrorCode.MISSING_IDEMPOTENCY_KEY and seen == []


# --------------------------------------------------------------------------
# Responses are validated strictly
# --------------------------------------------------------------------------
class TestResponseHandling:
    @pytest.mark.parametrize(
        "resp, expected",
        [
            (httpx.Response(202, json={}), DispatchErrorCode.INVALID_RESPONSE),
            (httpx.Response(202, json={"accepted": "yes"}), DispatchErrorCode.INVALID_RESPONSE),
            (httpx.Response(202, json=["accepted"]), DispatchErrorCode.INVALID_RESPONSE),
            (httpx.Response(202, content=b"not json"), DispatchErrorCode.INVALID_RESPONSE),
            (httpx.Response(202, json={"accepted": False}), DispatchErrorCode.REJECTED),
            (httpx.Response(409, json={"detail": "x"}), DispatchErrorCode.CONFLICT),
            (httpx.Response(422, json={"detail": "x"}), DispatchErrorCode.REJECTED),
            (httpx.Response(401, json={"detail": "x"}), DispatchErrorCode.REJECTED),
            (httpx.Response(503, json={"detail": "x"}), DispatchErrorCode.REJECTED),
            (httpx.Response(302, headers={"location": "https://evil.example/"}), DispatchErrorCode.REJECTED),
        ],
    )
    async def test_non_success_shapes_map_to_safe_codes(self, wire, resp, expected):
        seen, set_handler = wire
        set_handler(lambda req: resp)
        res = await submit_generated_outreach(**_kwargs())
        assert res.accepted is False and res.error_code == expected
        assert len(seen) == 1  # a 302 is never followed

    async def test_non_string_reference_is_tolerated_as_none(self, wire):
        _, set_handler = wire
        set_handler(lambda req: httpx.Response(202, json={"accepted": True, "mailing_agent_reference": 5}))
        res = await submit_generated_outreach(**_kwargs())
        assert res.accepted is True and res.mailing_agent_reference is None

    async def test_timeout_and_connection_failure_are_safe_codes_and_never_leak(self, wire, caplog):
        _, set_handler = wire

        def boom(req):
            raise httpx.ReadTimeout("secret-detail-in-timeout")

        set_handler(boom)
        assert (await submit_generated_outreach(**_kwargs())).error_code == DispatchErrorCode.TIMEOUT

        def refused(req):
            raise httpx.ConnectError("secret-detail-in-connect")

        set_handler(refused)
        assert (await submit_generated_outreach(**_kwargs())).error_code == DispatchErrorCode.UNREACHABLE
        assert "secret-detail" not in caplog.text and KEY not in caplog.text


# --------------------------------------------------------------------------
# Per-organization key resolution (tenant authority stays with Mailer)
# --------------------------------------------------------------------------
class TestOrganizationKeys:
    def test_resolves_only_the_callers_own_key(self, monkeypatch):
        monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"7": "k7", "8": "k8"}))
        assert org_api_key(7) == "k7" and org_api_key(8) == "k8"
        assert org_api_key(9) is None

    @pytest.mark.parametrize(
        "raw",
        ["", "   ", "not json", "[]", '"k"', "null", '{"7": ""}', '{"7": "   "}', '{"7": 5}', '{"07": "k"}'],
    )
    def test_everything_malformed_fails_closed(self, monkeypatch, raw):
        monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", raw)
        assert org_api_key(7) is None

    async def test_each_org_presents_its_own_key_never_anothers(self, wire):
        seen, _ = wire
        await submit_generated_outreach(**_kwargs(organization_id=7))
        await submit_generated_outreach(**_kwargs(organization_id=8))
        assert [r.headers["x-api-key"] for r in seen] == [KEY, "other-key"]

    async def test_org_without_a_key_never_borrows_another_orgs(self, wire):
        seen, _ = wire
        res = await submit_generated_outreach(**_kwargs(organization_id=9))
        assert res.error_code == DispatchErrorCode.AUTH_NOT_CONFIGURED and seen == []

    async def test_organization_id_is_never_in_the_body_even_to_identify_the_tenant(self, wire):
        seen, _ = wire
        await submit_generated_outreach(**_kwargs())
        assert b"organization" not in seen[0].content and b'"7"' not in seen[0].content


# --------------------------------------------------------------------------
# Transport rules -- fail closed before any network attempt
# --------------------------------------------------------------------------
class TestTransport:
    @pytest.mark.parametrize(
        "url, ok",
        [
            ("https://mailer.example.com", True),
            ("http://localhost:8000", True),
            ("http://127.0.0.1", True),
            ("http://[::1]:8000", True),
            ("http://mailer.example.com", False),
            ("http://10.0.0.5:8000", False),
            ("http://localhost.evil.com", False),
            ("ftp://mailer.example.com", False),
            ("mailer.example.com", False),
        ],
    )
    def test_secure_transport_allowlist(self, url, ok):
        assert _is_secure_transport(url) is ok

    async def test_unconfigured_is_refused(self, wire, monkeypatch):
        seen, _ = wire
        monkeypatch.delenv("MAILING_AGENT_BASE_URL")
        res = await submit_generated_outreach(**_kwargs())
        assert res.error_code == DispatchErrorCode.NOT_CONFIGURED and seen == []

    @pytest.mark.parametrize("url", ["http://mailer.example.com", "http://10.1.2.3", "http://localhost.evil.com"])
    async def test_insecure_remote_is_refused_even_with_a_valid_key(self, wire, monkeypatch, url):
        seen, _ = wire
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", url)
        res = await submit_generated_outreach(**_kwargs())
        assert res.error_code == DispatchErrorCode.INSECURE_TRANSPORT and seen == []

    async def test_loopback_http_is_allowed(self, wire, monkeypatch):
        seen, _ = wire
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://127.0.0.1:8000/")
        res = await submit_generated_outreach(**_kwargs())
        assert res.accepted is True
        assert str(seen[0].url) == "http://127.0.0.1:8000/integrations/leadboost/outreach-requests"

    @pytest.mark.parametrize(
        "raw, expected",
        [("5", 5.0), ("0.5", 0.5), ("abc", DEFAULT_TIMEOUT_SECONDS), ("0", DEFAULT_TIMEOUT_SECONDS),
         ("-3", DEFAULT_TIMEOUT_SECONDS), ("nan", DEFAULT_TIMEOUT_SECONDS), ("inf", DEFAULT_TIMEOUT_SECONDS)],
    )
    def test_timeout_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("MAILING_AGENT_TIMEOUT_SECONDS", raw)
        assert _timeout_seconds() == expected and math.isfinite(_timeout_seconds())

    async def test_send_request_get_has_no_body_or_content_type(self, wire):
        seen, _ = wire
        await send_request("GET", "/mailboxes", organization_id=ORG)
        assert seen[0].content == b"" and "content-type" not in seen[0].headers
