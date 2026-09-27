"""
Mailing Agent HTTP client (P1.4).

This is the ENTIRE contract between LeadBoost and the separate Mailing
Agent service (a different repository/deployment -- LeadBoost-mail-agent
-- not part of this codebase and not yet deployed at the time this module
was written). LeadBoost sends one HTTP request per dispatch attempt and
gets one acknowledgment back; nothing else crosses the boundary. See
CONTRACT.md in this package for the full wire-format writeup intended to
be handed to that other repository's implementation.

WHAT IS DELIBERATELY NOT SENT (brief #15, #16):
  - no SQLAlchemy model objects or ORM relationships
  - no raw DB connection details
  - EMAIL_CREDENTIAL_ENCRYPTION_KEY, or any encryption key material
  - unrelated lead intelligence (score, qualification reasoning,
    company-intelligence output, etc.) -- the Mailing Agent sends mail,
    it does not need to know why this lead was qualified
  - internal ids the Mailing Agent has no use for (organization_id is the
    one exception: it is included purely so the Mailing Agent's own logs/
    audit trail can group by tenant, matching this codebase's existing
    organization_id-everywhere convention)

WHAT IS SENT, AND WHY THE CREDENTIAL IS ONE OF THOSE THINGS: the Mailing
Agent has no access to LeadBoost's database, ORM models or
EMAIL_CREDENTIAL_ENCRYPTION_KEY (brief #16's explicit constraint), yet it
is the system that must actually authenticate to the sender's SMTP server
to send this one message. The only way to satisfy both constraints at
once is for LeadBoost to decrypt the credential exactly once, at the
moment of dispatch, and hand the plaintext secret to the Mailing Agent
over this one-shot HTTPS request -- the same trust boundary an SMTP relay
or transactional-email provider's API always crosses (you must give the
sender/relay the credential or API key it will actually authenticate
with). This is NOT a shortcut around P1.3's encryption: the credential
never touches this codebase's disk or logs in plaintext, decryption
happens in application/services/outreach_service.py using the exact same
narrowly-scoped pattern already used by api/endpoints/email_accounts.py's
verify_email_account (decrypt immediately before use, `del` immediately
after, never logged) -- see that function's call site for the mirrored
pattern. The Mailing Agent is documented (its own README/contract) as
never persisting a credential it receives this way.

TRANSPORT SECURITY (required -- see _is_secure_transport): because the
request body carries a plaintext SMTP credential, MAILING_AGENT_BASE_URL
MUST resolve to `https://`, with a narrow exception for local development
(`http://localhost`, `http://127.0.0.1`, `http://[::1]`, with or without
an explicit port) where there is no network segment for a credential to
leak across. Any other `http://` destination is refused before any
network call is attempted -- see DispatchErrorCode.INSECURE_TRANSPORT.
This is a fail-closed allowlist, not a warning: an insecure remote
MAILING_AGENT_BASE_URL is treated exactly like a missing one.

MAILING AGENT AUTHENTICATION IS REQUIRED FOR REMOTE DESTINATIONS: HTTPS
alone protects the transport, but a credential-bearing service-to-service
call to a *remote* Mailing Agent should not also be anonymous. For any
MAILING_AGENT_BASE_URL that isn't one of the local-loopback hosts above,
MAILING_AGENT_API_KEY MUST be set -- if it isn't, this fails closed with
DispatchErrorCode.AUTH_NOT_CONFIGURED before any network attempt, the
same fail-closed treatment as an unconfigured or insecure base URL. Local
development (loopback) keeps the API key optional, since there is no
network segment for an unauthenticated request to be intercepted on. This
requires no OAuth flow, mTLS, or service-mesh -- it is the same static
bearer token already supported, just no longer optional once the
destination is remote.

RESPONSE VALIDATION IS STRICT, NOT PERMISSIVE: a successful dispatch
requires a 2xx status AND a JSON object body with an explicit boolean
`accepted` field. Earlier drafts of this client defaulted a missing
`accepted` key to `True`, which meant a Mailing Agent that accidentally
returned `{}` (or any body without that key) would be silently treated
as a successful send -- exactly backwards for a boundary this
safety-sensitive. Two distinct failure categories, both mapping the
OutreachAction to DISPATCH_FAILED rather than SUBMITTED:
DispatchErrorCode.REJECTED for any non-2xx status of any kind (1xx/3xx
included -- httpx does not follow redirects by default, so a 3xx here
means something unexpected, not a benign hop -- as well as an explicit
`accepted: false` in an otherwise-2xx body), and
DispatchErrorCode.INVALID_RESPONSE specifically for a 2xx response whose
body doesn't conform to the contract: not valid JSON, not a JSON object,
or an object whose `accepted` key is missing or not a boolean.

IDEMPOTENCY IS A HARD CONTRACT REQUIREMENT, NOT A HINT: every request
this client sends includes `idempotency_key` (see OutreachAction.idempotency_key
and CONTRACT.md's "Idempotency" section). This client refuses to send a
request at all if it would somehow be empty (DispatchErrorCode.MISSING_IDEMPOTENCY_KEY)
rather than ever letting an unkeyed request reach the Mailing Agent. On
LeadBoost's own side, dispatch_action's atomic DISPATCHING claim (see
application/services/outreach_service.py) already prevents this process
from making two concurrent calls for the same OutreachAction; the
Mailing Agent contract's idempotency requirement is the second half of
that guarantee, covering the case this client's own claim CANNOT cover:
a network timeout after the Mailing Agent has already accepted or sent
the mail, followed by a legitimate LeadBoost-side retry (a fresh
dispatch_action call, e.g. after a DISPATCH_FAILED classification of
what was actually a response-delivery failure, not a send failure). The
Mailing Agent MUST treat two requests bearing the same idempotency_key as
the same logical delivery operation and must not send twice -- see
CONTRACT.md.

CONFIGURATION: MAILING_AGENT_BASE_URL / MAILING_AGENT_API_KEY /
MAILING_AGENT_TIMEOUT_SECONDS (env vars, see backend/.env.example). If
MAILING_AGENT_BASE_URL is unset -- true today, since the Mailing Agent
is not yet deployed -- `is_configured()` returns False and
`dispatch_outreach_action` short-circuits to DispatchErrorCode.NOT_CONFIGURED
without attempting a network call. This lets the rest of P1.4 (the
authorization lifecycle up through APPROVED) be fully built, tested and
demoed before the Mailing Agent exists, exactly as the brief requires
("If the external Mailing Agent is not yet part of this repository, do
NOT invent a second mail service inside LeadBoost").

TESTS: never make a real HTTP call to a Mailing Agent (brief #32). Tests
monkeypatch `dispatch_outreach_action` (or the module-level `httpx.AsyncClient`)
directly -- see tests/application/test_outreach_actions_api.py.
"""

import math
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from core.infrastructure.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15

# Hosts where a plain http:// destination is tolerated -- there is no
# network segment between this process and one of these for a credential
# to leak across. Anything else must be https://.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class DispatchErrorCode:
    """Small, closed vocabulary of SAFE failure classifications -- the
    same approach as core/infrastructure/email/smtp_verifier.py's
    VerificationErrorCode. Never the raw exception / HTTP body text."""

    NOT_CONFIGURED = "mailing_agent_not_configured"
    INSECURE_TRANSPORT = "mailing_agent_insecure_transport"
    AUTH_NOT_CONFIGURED = "mailing_agent_auth_not_configured"
    MISSING_IDEMPOTENCY_KEY = "mailing_agent_missing_idempotency_key"
    UNREACHABLE = "mailing_agent_unreachable"
    TIMEOUT = "mailing_agent_timeout"
    REJECTED = "mailing_agent_rejected"
    INVALID_RESPONSE = "mailing_agent_invalid_response"
    UNKNOWN = "mailing_agent_unknown_error"


@dataclass(frozen=True)
class DispatchResult:
    accepted: bool
    mailing_agent_reference: Optional[str] = None
    error_code: Optional[str] = None  # one of DispatchErrorCode.*, or None when accepted


def is_configured() -> bool:
    return bool(os.getenv("MAILING_AGENT_BASE_URL"))


def _is_local_host(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return (parsed.hostname or "").lower() in _LOCAL_HOSTS


def _is_secure_transport(base_url: str) -> bool:
    """True if `base_url` is safe to carry a plaintext SMTP credential
    over. https:// always qualifies; http:// only qualifies when it
    points at this same machine (local development / the two services
    running as sibling processes/containers on one host) -- never a
    remote hostname or IP, however trusted it might seem."""
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return True
    if parsed.scheme == "http" and _is_local_host(base_url):
        return True
    return False


def _timeout_seconds() -> float:
    """Parses MAILING_AGENT_TIMEOUT_SECONDS, falling back to
    DEFAULT_TIMEOUT_SECONDS for anything that isn't a finite, positive
    number -- not just values float() itself rejects. float() happily
    parses "nan"/"inf"/"-inf" without raising, and would also accept a
    nonsensical "0" or a negative value; none of those are a usable
    httpx timeout, so they're treated the same as a missing/malformed
    value rather than passed through."""
    raw = os.getenv("MAILING_AGENT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_TIMEOUT_SECONDS)
    if not math.isfinite(value) or value <= 0:
        return float(DEFAULT_TIMEOUT_SECONDS)
    return value


async def dispatch_outreach_action(
    *,
    outreach_action_id: int,
    organization_id: int,
    idempotency_key: str,
    correlation_id: Optional[str],
    sender_email_address: str,
    sender_display_name: Optional[str],
    smtp_host: str,
    smtp_port: int,
    security_mode: str,
    smtp_username: str,
    credential_type: str,
    plaintext_credential: str,
    recipient_email: str,
    recipient_name: Optional[str],
    subject: Optional[str],
    body: str,
) -> DispatchResult:
    """Sends exactly one outreach action to the Mailing Agent. Never
    raises for an ordinary network/HTTP failure -- those are mapped to a
    DispatchResult with accepted=False and a safe error_code, matching
    this codebase's existing "agents/verifiers report a result, they
    don't raise for expected failure modes" convention (see
    smtp_verifier.py). Only a genuine programming error would raise.

    `plaintext_credential` must be decrypted by the caller immediately
    before this call and discarded immediately after -- this function
    does not hold a reference to it beyond building the one outbound
    request body.

    Fails closed, before any network attempt, in four cases: the
    Mailing Agent isn't configured (NOT_CONFIGURED), it's configured
    over an insecure remote transport (INSECURE_TRANSPORT -- see
    _is_secure_transport), it's a remote destination with no
    MAILING_AGENT_API_KEY configured (AUTH_NOT_CONFIGURED -- see this
    module's docstring), or `idempotency_key` is somehow empty
    (MISSING_IDEMPOTENCY_KEY -- see this module's docstring on why
    idempotency is a hard requirement of this contract, not optional).
    """
    if not idempotency_key:
        logger.error(
            "Refusing to dispatch an outreach action with no idempotency_key",
            extra={"organization_id": organization_id, "outreach_action_id": outreach_action_id},
        )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.MISSING_IDEMPOTENCY_KEY)

    if not is_configured():
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.NOT_CONFIGURED)

    base_url = os.getenv("MAILING_AGENT_BASE_URL", "").rstrip("/")

    if not _is_secure_transport(base_url):
        logger.error(
            "Refusing to dispatch over an insecure Mailing Agent transport",
            extra={"organization_id": organization_id, "outreach_action_id": outreach_action_id},
        )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.INSECURE_TRANSPORT)

    api_key = os.getenv("MAILING_AGENT_API_KEY", "")

    if not api_key and not _is_local_host(base_url):
        logger.error(
            "Refusing to dispatch to a remote Mailing Agent with no API key configured",
            extra={"organization_id": organization_id, "outreach_action_id": outreach_action_id},
        )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.AUTH_NOT_CONFIGURED)

    payload = {
        "outreach_action_id": outreach_action_id,
        "organization_id": organization_id,
        "idempotency_key": idempotency_key,
        "correlation_id": correlation_id,
        "sender": {
            "email_address": sender_email_address,
            "display_name": sender_display_name,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "security_mode": security_mode,
            "username": smtp_username,
            "credential_type": credential_type,
            "credential": plaintext_credential,
        },
        "recipient": {
            "email": recipient_email,
            "name": recipient_name,
        },
        "message": {
            "subject": subject,
            "body": body,
        },
    }

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as http_client:
            response = await http_client.post(f"{base_url}/outreach-actions", json=payload, headers=headers)
    except httpx.TimeoutException:
        logger.warning(
            "Mailing Agent dispatch timed out",
            extra={"organization_id": organization_id, "outreach_action_id": outreach_action_id},
        )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.TIMEOUT)
    except httpx.HTTPError as exc:
        # Never log exc's raw text if it could embed request content --
        # httpx transport errors here are connection-level (refused,
        # DNS, TLS), not response bodies, so this is safe; still keep it
        # to the exception's type/class, not str(exc), to be certain.
        logger.warning(
            f"Mailing Agent dispatch unreachable ({type(exc).__name__})",
            extra={"organization_id": organization_id, "outreach_action_id": outreach_action_id},
        )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.UNREACHABLE)
    finally:
        del payload  # drop the credential-bearing dict as soon as the request is sent

    # Strict, not permissive: ANY non-2xx (1xx/3xx included -- httpx does
    # not auto-follow redirects, so a 3xx here is unexpected, not benign)
    # is a rejection. The 401/403/429/5xx branch below exists only for
    # more specific logging, not different DispatchResult handling.
    if not (200 <= response.status_code < 300):
        if response.status_code >= 500 or response.status_code in (401, 403, 429):
            logger.warning(
                "Mailing Agent dispatch rejected",
                extra={
                    "organization_id": organization_id,
                    "outreach_action_id": outreach_action_id,
                    "status_code": response.status_code,
                },
            )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)

    try:
        data = response.json()
    except ValueError:
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.INVALID_RESPONSE)

    if not isinstance(data, dict) or not isinstance(data.get("accepted"), bool):
        # A missing/non-boolean `accepted` is never treated as success --
        # see this module's docstring on why an earlier draft's
        # data.get("accepted", True) default was unsafe.
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.INVALID_RESPONSE)

    if not data["accepted"]:
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)

    reference = data.get("mailing_agent_reference")
    return DispatchResult(accepted=True, mailing_agent_reference=reference if isinstance(reference, str) else None)
