"""
Generic SMTP mailbox verification (P1.3).

Tests authentication/connectivity ONLY -- this module never sends an
email (no `sendmail`/`send_message` call anywhere below; the only SMTP
verbs used are the ones `smtplib` issues internally for connect/EHLO/
STARTTLS/AUTH/QUIT). See `verify_smtp_mailbox`'s docstring for the exact
protocol sequence.

Runs on a worker thread via `asyncio.to_thread` (the same
blocking-I/O-offload convention already used by
core.infrastructure.scraping.scraper for synchronous HTTP calls) because
Python's standard `smtplib` is synchronous and this application's FastAPI
event loop must never block on network I/O.

One attempt, one bounded timeout, no retries -- see `verify_smtp_mailbox`'s
`timeout_seconds` parameter. A transient network blip should ask the user
to click "Verify" again, not have this module silently retry on their
behalf (the P1.3 brief is explicit: "Do NOT add generic retry loops. Do
NOT retry authentication failures.").
"""

import smtplib
import socket
import ssl
import asyncio
from dataclasses import dataclass
from typing import Optional

from core.domain.models.email_account import VerificationStatus, SecurityMode
from core.infrastructure.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 10


class VerificationErrorCode:
    """Small, closed vocabulary of SAFE failure classifications -- these,
    not raw exception text or SMTP server responses, are what the API/
    frontend ever see (see core/domain/schemas/email_account.py and the
    P1.3 brief's "map provider/network/credential errors into safe
    application-level error categories" / "do not expose raw exception
    strings" requirements)."""

    AUTH_FAILED = "auth_failed"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    TLS_ERROR = "tls_error"
    UNKNOWN = "unknown_error"


@dataclass(frozen=True)
class VerificationResult:
    status: str  # one of VerificationStatus.{VERIFIED, FAILED, REQUIRES_REAUTH}
    error_code: Optional[str] = None  # one of VerificationErrorCode.*, or None when status == VERIFIED


def _connect_and_authenticate(
    host: str,
    port: int,
    security_mode: str,
    username: str,
    password: str,
    timeout_seconds: int,
) -> None:
    """The actual blocking SMTP work -- runs on a worker thread, never
    directly on the event loop (see verify_smtp_mailbox below). Raises on
    any failure; returns normally only if authentication succeeded.
    Always closes the connection via try/finally, and never calls any
    SMTP verb beyond connect/EHLO/STARTTLS/LOGIN/QUIT -- specifically,
    never `sendmail`/`send_message`.
    """
    client: Optional[smtplib.SMTP] = None
    try:
        if security_mode == SecurityMode.TLS:
            # Implicit TLS: the connection is inside a TLS session from
            # the first byte -- no STARTTLS negotiation.
            client = smtplib.SMTP_SSL(host=host, port=port, timeout=timeout_seconds)
        else:
            # STARTTLS: connect in plaintext, then explicitly upgrade.
            # SecurityMode has exactly two values (validated at the
            # schema layer -- see email_account.py), so this covers both;
            # there is no silent third "just connect in plaintext and
            # authenticate" path.
            client = smtplib.SMTP(host=host, port=port, timeout=timeout_seconds)
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()

        client.login(username, password)
        # Deliberately no further calls here -- login() succeeding is the
        # entire test. No sendmail(), no send_message(), no NOOP loop.
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                # The mailbox is already verified (or already failed) by
                # this point -- a failure to cleanly close the connection
                # must not change the verification outcome or raise past
                # this function.
                pass


async def verify_smtp_mailbox(
    host: str,
    port: int,
    security_mode: str,
    username: str,
    password: str,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> VerificationResult:
    """Attempts to authenticate to `host:port` using `security_mode` and
    the given credentials. Never sends an email. Never raises -- every
    failure mode below is caught and mapped to a VerificationResult, so
    callers (the API endpoint) never need their own broad except clause
    around this function to stay safe.

    `password` is decrypted plaintext, held only for the duration of this
    call -- see the caller in api/endpoints/email_accounts.py, which
    decrypts immediately before calling this function and lets the local
    variable go out of scope immediately after; this function itself
    never logs, returns, or otherwise persists it anywhere.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(
                _connect_and_authenticate, host, port, security_mode, username, password, timeout_seconds
            ),
            # Belt-and-suspenders alongside smtplib's own socket timeout:
            # guarantees this coroutine returns even if a thread's
            # blocking call somehow ignores its socket timeout, so a
            # single slow mailbox can never hang a request indefinitely.
            timeout=timeout_seconds + 2,
        )
        return VerificationResult(status=VerificationStatus.VERIFIED)

    except smtplib.SMTPAuthenticationError:
        # Authentication rejected by the server (bad password/app
        # password). Always FAILED, never REQUIRES_REAUTH, for the only
        # two credential types P1.3 implements (SMTP_PASSWORD,
        # APP_PASSWORD) -- REQUIRES_REAUTH names a distinct
        # re-authorization *flow* (e.g. an expired OAuth consent) that
        # doesn't exist for a plain password. See
        # core/domain/models/email_account.py::VerificationStatus.
        return VerificationResult(status=VerificationStatus.FAILED, error_code=VerificationErrorCode.AUTH_FAILED)

    except (socket.timeout, TimeoutError, asyncio.TimeoutError):
        return VerificationResult(status=VerificationStatus.FAILED, error_code=VerificationErrorCode.TIMEOUT)

    except (ssl.SSLError, smtplib.SMTPNotSupportedError):
        # STARTTLS not offered/negotiation failed, or SMTP_SSL couldn't
        # establish the implicit-TLS session. Must be checked before the
        # generic OSError/SMTPException branches below: both
        # ssl.SSLError and smtplib.SMTPNotSupportedError are themselves
        # OSError subclasses in the standard library (SMTPNotSupportedError
        # via smtplib.SMTPException, which also derives from OSError), so
        # a broader except placed first would silently swallow these into
        # the wrong category.
        return VerificationResult(status=VerificationStatus.FAILED, error_code=VerificationErrorCode.TLS_ERROR)

    except smtplib.SMTPException as exc:
        # Any other SMTP-protocol-level failure this module didn't
        # anticipate a specific category for. Logged (safely -- see
        # below) so a genuinely new failure mode can be added a specific
        # category later, but the caller only ever sees UNKNOWN. Must
        # come before the OSError branch below for the same reason as
        # the TLS branch above: smtplib.SMTPException itself is an
        # OSError subclass.
        logger.warning(f"SMTP verification failed with an unclassified SMTPException: {type(exc).__name__}")
        return VerificationResult(status=VerificationStatus.FAILED, error_code=VerificationErrorCode.UNKNOWN)

    except (socket.gaierror, ConnectionRefusedError, OSError):
        # DNS resolution failure, connection refused, network unreachable,
        # etc. Deliberately last among the specific branches: this is the
        # most general one (OSError is a base class of several exceptions
        # already handled above), so it only catches genuine
        # connection-level failures that aren't already a more specific
        # TLS/SMTP-protocol error.
        return VerificationResult(status=VerificationStatus.FAILED, error_code=VerificationErrorCode.UNREACHABLE)

    except Exception as exc:
        # Deliberately last and deliberately broad: this function's
        # contract (see its docstring) is that it never raises, so a
        # genuinely unanticipated error (a bug in this module, a Python
        # version difference in exception types, etc.) still degrades to
        # a safe FAILED/UNKNOWN result instead of propagating an
        # arbitrary exception (and its message, which might contain
        # `password` from a local variable in a traceback) up through the
        # API layer. Only the exception's type name is logged, never
        # str(exc) or the exception object itself, since either could
        # capture `password` from the local scope in some Python
        # versions' traceback formatting.
        logger.error(f"SMTP verification failed with an unexpected error: {type(exc).__name__}")
        return VerificationResult(status=VerificationStatus.FAILED, error_code=VerificationErrorCode.UNKNOWN)
