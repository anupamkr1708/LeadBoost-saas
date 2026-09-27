"""
P1.4: tests for the Mailing Agent HTTP client itself --
core/infrastructure/mailing_agent/client.py.

Under test, all fail-closed BEFORE any network call is attempted unless
otherwise noted:

1. Transport security (_is_secure_transport / DispatchErrorCode.INSECURE_TRANSPORT)
   -- a remote http:// destination must never be used to carry a
   plaintext SMTP credential; only https:// or a handful of local
   loopback hosts over http:// are accepted.

2. The idempotency-key hard requirement (DispatchErrorCode.MISSING_IDEMPOTENCY_KEY)
   -- see CONTRACT.md's "Idempotency" section for why this is a
   contract requirement, not an optional nicety.

3. Authentication for remote destinations (DispatchErrorCode.AUTH_NOT_CONFIGURED)
   -- HTTPS protects the transport; a remote Mailing Agent call carrying
   a plaintext credential must also not be anonymous.

4. Response validation (DispatchErrorCode.INVALID_RESPONSE / REJECTED) --
   this one runs an actual (local, mocked-transport) HTTP round trip, so
   these tests patch httpx.AsyncClient.post rather than relying on a
   fail-closed short-circuit.

5. Timeout parsing -- MAILING_AGENT_TIMEOUT_SECONDS must be finite and
   positive or the client falls back to DEFAULT_TIMEOUT_SECONDS.

NEVER SENDS REAL MAIL: fail-closed tests never reach httpx at all; the
loopback-connectivity test fails fast against an intentionally-closed
local port (no external network); the response-validation tests mock
httpx.AsyncClient.post directly, so no network call happens there either.
"""

import math
from unittest.mock import AsyncMock, patch

import pytest

from core.infrastructure.mailing_agent.client import (
    DEFAULT_TIMEOUT_SECONDS,
    DispatchErrorCode,
    _is_secure_transport,
    _timeout_seconds,
    dispatch_outreach_action,
)

_CALL_KWARGS = dict(
    outreach_action_id=1,
    organization_id=1,
    correlation_id=None,
    sender_email_address="sender@example.com",
    sender_display_name=None,
    smtp_host="smtp.example.com",
    smtp_port=587,
    security_mode="starttls",
    smtp_username="sender@example.com",
    credential_type="smtp_password",
    plaintext_credential="do-not-leak-me",
    recipient_email="lead@example.com",
    recipient_name=None,
    subject="Hi",
    body="Body",
)


class TestSecureTransportValidation:
    """Direct unit tests of the URL allowlist itself -- see
    client.py's module docstring for the exact rule: https:// always
    qualifies; http:// only for localhost/127.0.0.1/::1."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://mailing-agent.example.com",
            "https://mailing-agent.example.com:8443",
            "http://localhost",
            "http://localhost:8001",
            "http://127.0.0.1",
            "http://127.0.0.1:8001",
            "http://[::1]:8001",
        ],
    )
    def test_accepted_urls(self, url):
        assert _is_secure_transport(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "http://mailing-agent.example.com",
            "http://10.0.0.50:8001",
            "http://remote-host:8001",
            "ftp://mailing-agent.example.com",
            "",
            "not-a-url",
        ],
    )
    def test_rejected_urls(self, url):
        assert _is_secure_transport(url) is False


class TestDispatchFailsClosed:
    """dispatch_outreach_action's own fail-closed behavior -- these three
    checks happen, in order, before any httpx call is constructed."""

    async def test_not_configured_never_attempts_network_call(self, monkeypatch):
        monkeypatch.delenv("MAILING_AGENT_BASE_URL", raising=False)
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.NOT_CONFIGURED

    async def test_missing_idempotency_key_never_attempts_network_call(self, monkeypatch):
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailing-agent.example.com")
        result = await dispatch_outreach_action(idempotency_key="", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.MISSING_IDEMPOTENCY_KEY

    async def test_insecure_remote_http_never_attempts_network_call(self, monkeypatch):
        # If this ever tried a real network call it would have to
        # actually resolve/connect to a nonexistent host -- the whole
        # point of this test is that it must never try.
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://remote-mailing-agent.example.com")
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.INSECURE_TRANSPORT

    async def test_insecure_remote_ip_never_attempts_network_call(self, monkeypatch):
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://10.0.0.50:8001")
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.INSECURE_TRANSPORT

    async def test_local_http_passes_transport_check_and_actually_attempts_the_call(self, monkeypatch):
        """Distinguishes "rejected transport, no attempt made" (the
        tests above) from "accepted transport, attempt made" -- the
        local-loopback exception must actually let a request through to
        httpx, not silently no-op. Port 1 on loopback has nothing
        listening, so this fails fast with UNREACHABLE -- proving the
        call was attempted, without needing a real Mailing Agent or
        touching any external network."""
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://127.0.0.1:1")
        monkeypatch.setenv("MAILING_AGENT_TIMEOUT_SECONDS", "2")
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.UNREACHABLE


class TestAuthenticationRequiredForRemote:
    """A remote (non-loopback) Mailing Agent call carries a plaintext
    SMTP credential -- HTTPS protects the transport, but the call must
    also not be anonymous. Loopback keeps the key optional (local dev)."""

    async def test_remote_https_without_api_key_fails_closed(self, monkeypatch):
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailing-agent.example.com")
        monkeypatch.delenv("MAILING_AGENT_API_KEY", raising=False)
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.AUTH_NOT_CONFIGURED

    async def test_remote_https_with_api_key_passes_the_auth_check(self, monkeypatch):
        """Proves the auth check is actually passed (not just always
        failing) when a key is present -- same "fails fast against a
        real but closed connection" technique as the loopback test
        above, this time against a remote-shaped host that simply won't
        resolve/connect from this sandbox, so it's UNREACHABLE rather
        than AUTH_NOT_CONFIGURED."""
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailing-agent.invalid")
        monkeypatch.setenv("MAILING_AGENT_API_KEY", "test-key")
        monkeypatch.setenv("MAILING_AGENT_TIMEOUT_SECONDS", "2")
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.UNREACHABLE

    async def test_local_http_without_api_key_does_not_fail_closed_on_auth(self, monkeypatch):
        """Loopback is exempt from the auth requirement -- this reaches
        the same UNREACHABLE-via-closed-port outcome as
        TestDispatchFailsClosed's loopback test, proving AUTH_NOT_CONFIGURED
        was never raised for it."""
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "http://127.0.0.1:1")
        monkeypatch.delenv("MAILING_AGENT_API_KEY", raising=False)
        monkeypatch.setenv("MAILING_AGENT_TIMEOUT_SECONDS", "2")
        result = await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.UNREACHABLE


def _mock_response(status_code, json_body=None, json_raises=False):
    """Builds a minimal stand-in for an httpx.Response -- just enough
    surface (.status_code, .json()) for dispatch_outreach_action's own
    response-handling code, without a real HTTP round trip."""

    class _Resp:
        def __init__(self):
            self.status_code = status_code

        def json(self):
            if json_raises:
                raise ValueError("not valid JSON")
            return json_body

    return _Resp()


class TestResponseValidation:
    """dispatch_outreach_action's handling of what the Mailing Agent
    actually sends back -- httpx.AsyncClient.post is mocked directly so
    these exercise the real response-parsing code without a network
    call. See client.py's module docstring for the REJECTED (bad status
    code) vs INVALID_RESPONSE (bad body) split."""

    async def _dispatch_with_mocked_response(self, monkeypatch, response):
        monkeypatch.setenv("MAILING_AGENT_BASE_URL", "https://mailing-agent.example.com")
        monkeypatch.setenv("MAILING_AGENT_API_KEY", "test-key")
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)):
            return await dispatch_outreach_action(idempotency_key="key-1", **_CALL_KWARGS)

    async def test_empty_object_is_never_treated_as_accepted(self, monkeypatch):
        """The exact bug being fixed: {} used to default to accepted=True."""
        result = await self._dispatch_with_mocked_response(monkeypatch, _mock_response(200, {}))
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.INVALID_RESPONSE

    async def test_non_boolean_accepted_is_invalid(self, monkeypatch):
        result = await self._dispatch_with_mocked_response(
            monkeypatch, _mock_response(200, {"accepted": "yes"})
        )
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.INVALID_RESPONSE

    async def test_non_object_json_body_is_invalid(self, monkeypatch):
        result = await self._dispatch_with_mocked_response(monkeypatch, _mock_response(200, [True]))
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.INVALID_RESPONSE

    async def test_malformed_json_body_is_invalid(self, monkeypatch):
        result = await self._dispatch_with_mocked_response(
            monkeypatch, _mock_response(200, json_raises=True)
        )
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.INVALID_RESPONSE

    async def test_well_formed_accepted_true_succeeds(self, monkeypatch):
        result = await self._dispatch_with_mocked_response(
            monkeypatch, _mock_response(200, {"accepted": True, "mailing_agent_reference": "ref-123"})
        )
        assert result.accepted is True
        assert result.mailing_agent_reference == "ref-123"

    async def test_explicit_accepted_false_is_rejected_not_invalid(self, monkeypatch):
        result = await self._dispatch_with_mocked_response(monkeypatch, _mock_response(200, {"accepted": False}))
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.REJECTED

    @pytest.mark.parametrize("status_code", [100, 302, 400, 429, 500, 503])
    async def test_every_non_2xx_status_is_rejected(self, monkeypatch, status_code):
        result = await self._dispatch_with_mocked_response(
            monkeypatch, _mock_response(status_code, {"accepted": True})
        )
        assert result.accepted is False
        assert result.error_code == DispatchErrorCode.REJECTED


class TestTimeoutValidation:
    """MAILING_AGENT_TIMEOUT_SECONDS must parse to a finite, positive
    number or the client silently falls back to DEFAULT_TIMEOUT_SECONDS
    -- float() itself accepts "nan"/"inf" without raising, and would
    also accept a nonsensical 0 or negative value, none of which are a
    usable httpx timeout."""

    @pytest.mark.parametrize("raw", ["7", "0.5", "120"])
    def test_valid_values_are_used_as_is(self, monkeypatch, raw):
        monkeypatch.setenv("MAILING_AGENT_TIMEOUT_SECONDS", raw)
        assert _timeout_seconds() == float(raw)

    @pytest.mark.parametrize("raw", ["0", "-1", "nan", "inf", "-inf", "not-a-number", ""])
    def test_invalid_values_fall_back_to_default(self, monkeypatch, raw):
        monkeypatch.setenv("MAILING_AGENT_TIMEOUT_SECONDS", raw)
        assert _timeout_seconds() == float(DEFAULT_TIMEOUT_SECONDS)

    def test_unset_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("MAILING_AGENT_TIMEOUT_SECONDS", raising=False)
        assert _timeout_seconds() == float(DEFAULT_TIMEOUT_SECONDS)

    def test_default_itself_is_sane(self):
        assert math.isfinite(DEFAULT_TIMEOUT_SECONDS)
        assert DEFAULT_TIMEOUT_SECONDS > 0
