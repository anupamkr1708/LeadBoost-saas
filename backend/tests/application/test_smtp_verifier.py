"""
P1.3: deterministic tests for core.infrastructure.email.smtp_verifier.

Per the P1.3 brief, the standard test suite must never depend on a real
SMTP server. This mocks `smtplib.SMTP`/`smtplib.SMTP_SSL` at the module
boundary `smtp_verifier` itself imports (`smtplib`) -- one of the two
approaches the brief explicitly names ("a small test SMTP server or
mocked provider boundary"). Each test drives `verify_smtp_mailbox` through
`asyncio.to_thread` exactly as production does; only the underlying
socket/TLS/SMTP-protocol behavior is faked, not the async/threading glue
this module actually uses.
"""

import smtplib
import socket
import ssl
from unittest.mock import MagicMock, patch

import pytest

from core.domain.models.email_account import VerificationStatus, SecurityMode
from core.infrastructure.email.smtp_verifier import (
    verify_smtp_mailbox,
    VerificationErrorCode,
)

HOST = "smtp.example.com"
PORT = 587
USERNAME = "sender@example.com"
PASSWORD = "correct-horse-battery-staple"


async def _run(security_mode=SecurityMode.STARTTLS, timeout_seconds=5):
    return await verify_smtp_mailbox(
        host=HOST, port=PORT, security_mode=security_mode, username=USERNAME, password=PASSWORD,
        timeout_seconds=timeout_seconds,
    )


def _fake_smtp_client():
    """A MagicMock standing in for smtplib.SMTP/SMTP_SSL -- login()
    succeeds by default; individual tests override .login.side_effect or
    swap the whole client for a failure scenario."""
    client = MagicMock()
    client.ehlo.return_value = None
    client.starttls.return_value = None
    client.login.return_value = None
    client.quit.return_value = None
    return client


class TestHappyPath:
    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_starttls_success_returns_verified(self, mock_smtp_cls):
        mock_smtp_cls.return_value = _fake_smtp_client()

        result = await _run(security_mode=SecurityMode.STARTTLS)

        assert result.status == VerificationStatus.VERIFIED
        assert result.error_code is None
        mock_smtp_cls.return_value.starttls.assert_called_once()
        mock_smtp_cls.return_value.login.assert_called_once_with(USERNAME, PASSWORD)
        mock_smtp_cls.return_value.quit.assert_called_once()

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP_SSL")
    async def test_implicit_tls_success_returns_verified(self, mock_smtp_ssl_cls):
        mock_smtp_ssl_cls.return_value = _fake_smtp_client()

        result = await _run(security_mode=SecurityMode.TLS)

        assert result.status == VerificationStatus.VERIFIED
        # Implicit TLS never calls starttls() -- the whole connection is
        # already inside TLS.
        mock_smtp_ssl_cls.return_value.starttls.assert_not_called()
        mock_smtp_ssl_cls.return_value.login.assert_called_once_with(USERNAME, PASSWORD)

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_verification_never_sends_mail(self, mock_smtp_cls):
        """The core safety property: no code path in this module may call
        sendmail/send_message. Explicitly asserted, not just "not
        mentioned in the source"."""
        client = _fake_smtp_client()
        mock_smtp_cls.return_value = client

        await _run()

        client.sendmail.assert_not_called()
        client.send_message.assert_not_called()

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_connection_is_always_closed_even_on_quit_failure(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.quit.side_effect = smtplib.SMTPServerDisconnected()
        mock_smtp_cls.return_value = client

        # A failure to cleanly quit() must not surface as an exception or
        # change the (successful) outcome.
        result = await _run()
        assert result.status == VerificationStatus.VERIFIED


class TestFailureMapping:
    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_bad_credentials_returns_failed_auth_failed(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"Authentication failed")
        mock_smtp_cls.return_value = client

        result = await _run()

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.AUTH_FAILED
        # Never REQUIRES_REAUTH for a plain password -- see
        # smtp_verifier.py's docstring on why that status is reserved.
        assert result.status != VerificationStatus.REQUIRES_REAUTH

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_unreachable_host_returns_failed_unreachable(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = socket.gaierror("Name or service not known")

        result = await _run()

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.UNREACHABLE

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_connection_refused_returns_failed_unreachable(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = ConnectionRefusedError()

        result = await _run()

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.UNREACHABLE

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_connection_timeout_returns_failed_timeout(self, mock_smtp_cls):
        mock_smtp_cls.side_effect = socket.timeout()

        result = await _run(timeout_seconds=1)

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.TIMEOUT

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_slow_server_is_bounded_by_the_outer_timeout(self, mock_smtp_cls):
        """Even if the underlying blocking call somehow ignores its own
        socket timeout, the outer asyncio.wait_for must still bound total
        verification time -- proven here with a client whose login()
        blocks far longer than the requested timeout."""
        import time

        def slow_login(*args, **kwargs):
            time.sleep(5)

        client = _fake_smtp_client()
        client.login.side_effect = slow_login
        mock_smtp_cls.return_value = client

        result = await _run(timeout_seconds=1)

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.TIMEOUT

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_starttls_negotiation_failure_returns_failed_tls_error(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.starttls.side_effect = ssl.SSLError("TLS negotiation failed")
        mock_smtp_cls.return_value = client

        result = await _run(security_mode=SecurityMode.STARTTLS)

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.TLS_ERROR

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_starttls_not_supported_by_server_returns_failed_tls_error(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.starttls.side_effect = smtplib.SMTPNotSupportedError("STARTTLS not supported")
        mock_smtp_cls.return_value = client

        result = await _run(security_mode=SecurityMode.STARTTLS)

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.TLS_ERROR

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_unclassified_smtp_exception_returns_failed_unknown(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.login.side_effect = smtplib.SMTPException("something the module didn't anticipate")
        mock_smtp_cls.return_value = client

        result = await _run()

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.UNKNOWN

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_pure_socket_level_failure_not_wrapped_in_smtp_exception_is_unreachable(self, mock_smtp_cls):
        """Regression guard for a real stdlib gotcha this module's
        exception ordering depends on: smtplib.SMTPException (and
        smtplib.SMTPNotSupportedError) are themselves OSError subclasses.
        A generic `except OSError` placed before the SMTP/TLS-specific
        branches would silently swallow SMTPAuthenticationError/
        SMTPNotSupportedError/SMTPException into UNREACHABLE. This test
        uses a raw OSError (not an SMTP-specific subclass) to confirm the
        UNREACHABLE branch is still reachable at all once the more
        specific branches are checked first."""
        mock_smtp_cls.side_effect = OSError("Network is unreachable")

        result = await _run()

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.UNREACHABLE


class TestNoRetries:
    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_verification_makes_exactly_one_connection_attempt(self, mock_smtp_cls):
        """No retry loop anywhere in this module -- a failure must result
        in exactly one SMTP() construction, not several."""
        mock_smtp_cls.side_effect = socket.timeout()

        await _run()

        assert mock_smtp_cls.call_count == 1

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_repeated_verification_calls_are_each_independent_and_deterministic(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
        mock_smtp_cls.return_value = client

        first = await _run()
        second = await _run()

        assert first == second
        assert mock_smtp_cls.call_count == 2  # one connection per call, not shared/cached


class TestSafeErrorSurface:
    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_failure_result_never_contains_the_password(self, mock_smtp_cls):
        client = _fake_smtp_client()
        client.login.side_effect = smtplib.SMTPAuthenticationError(
            535, f"Authentication failed for password {PASSWORD}".encode()
        )
        mock_smtp_cls.return_value = client

        result = await _run()

        assert PASSWORD not in (result.error_code or "")
        assert PASSWORD not in result.status

    @patch("core.infrastructure.email.smtp_verifier.smtplib.SMTP")
    async def test_completely_unanticipated_exception_still_returns_a_safe_result(self, mock_smtp_cls):
        """verify_smtp_mailbox's contract is that it never raises -- even
        a bug or an exception type this module didn't specifically
        anticipate must still come back as a safe FAILED/UNKNOWN result,
        not propagate arbitrary exception text up through the API layer."""
        mock_smtp_cls.side_effect = RuntimeError("some completely unrelated bug")

        result = await _run()

        assert result.status == VerificationStatus.FAILED
        assert result.error_code == VerificationErrorCode.UNKNOWN
