"""
Mailer HTTP client (L1).

LeadBoost is the customer-facing control plane (authorization + business
context); the separate Mailer service (LeadBoost-mail-agent, its own
repository/deployment) owns generation, communication, delivery and the
send-time SMTP credential. This module is LeadBoost's ONLY transport to it.
See CONTRACT.md for the wire contract.

WHAT CHANGED FROM P1.4: the old `dispatch_outreach_action` decrypted the
sender's SMTP credential on every dispatch and POSTed it (plus SMTP host/
port/username and a pre-written subject/body) to a `/outreach-actions` route
with a Bearer token. That contract has been removed, not preserved: the
function no longer exists, so no caller can send a credential on dispatch by
accident. Dispatch now calls `submit_generated_outreach`, which carries ONLY
recipient + business context + operation identity.

WHEN A CREDENTIAL MAY CROSS THIS BOUNDARY: only through mailbox provisioning
(`mailbox_client.py`: create / activate), never through dispatch. Both go
through `send_request` below, so they share one set of transport rules.

TRANSPORT RULES (fail closed, before any network attempt):
  - MAILING_AGENT_BASE_URL must be set (NOT_CONFIGURED).
  - It must be https://, or http:// to a loopback host only
    (INSECURE_TRANSPORT) -- the same request path can carry an SMTP
    credential during provisioning, so there is no plain-http-to-remote
    exception.
  - The caller's organization must have its own Mailer API key in
    MAILING_AGENT_ORG_API_KEYS (AUTH_NOT_CONFIGURED).

TENANCY: Mailer is the tenant authority -- it maps the presented `X-API-Key`
to its organization. LeadBoost therefore resolves the key from ITS OWN
organization id (`org_api_key`) and never puts an organization id in a
request body. There is deliberately no single shared key: one key would map
every LeadBoost customer to one Mailer organization. The legacy
MAILING_AGENT_API_KEY is no longer read.

Redirects are not followed (httpx default), so a 3xx can never carry the
key or a credential somewhere unexpected; it is treated as a rejection.

TESTS: never make a real HTTP call. Tests inject an `httpx.MockTransport`/
monkeypatch `httpx.AsyncClient`, or patch `send_request`.
"""

import json
import math
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from core.infrastructure.logging import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15

# Hosts where plain http:// is tolerated -- no network segment to leak across.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

GENERATED_OUTREACH_PATH = "/integrations/leadboost/outreach-requests"

# Mailer M2 request bounds (mailer_agent/schemas.py). Enforced here so a long
# but legitimate value is shortened instead of turning into a 422.
MAX_VALUE_PROPOSITION_CHARS = 2000
MAX_FACT_CHARS = 500
MAX_FACTS = 8
MAX_RECIPIENT_FIELD_CHARS = 200


class DispatchErrorCode:
    """Small, closed vocabulary of SAFE failure classifications. Never the raw
    exception / HTTP body text."""

    NOT_CONFIGURED = "mailing_agent_not_configured"
    INSECURE_TRANSPORT = "mailing_agent_insecure_transport"
    # L1: no Mailer API key mapped for this LeadBoost organization.
    AUTH_NOT_CONFIGURED = "mailing_agent_auth_not_configured"
    MISSING_IDEMPOTENCY_KEY = "mailing_agent_missing_idempotency_key"
    UNREACHABLE = "mailing_agent_unreachable"
    TIMEOUT = "mailing_agent_timeout"
    REJECTED = "mailing_agent_rejected"
    # L1: Mailer answered 409 -- e.g. zero/multiple ACTIVE mailboxes, or the
    # idempotency key was reused for a different operation.
    CONFLICT = "mailing_agent_conflict"
    INVALID_RESPONSE = "mailing_agent_invalid_response"
    UNKNOWN = "mailing_agent_unknown_error"


@dataclass(frozen=True)
class DispatchResult:
    accepted: bool
    mailing_agent_reference: Optional[str] = None
    error_code: Optional[str] = None  # one of DispatchErrorCode.*, or None when accepted


@dataclass(frozen=True)
class MailerResponse:
    """Outcome of one Mailer HTTP call. Exactly one of (status_code set) or
    (error_code set by a pre-flight/transport failure)."""

    status_code: Optional[int] = None
    data: Any = None  # parsed JSON body, or None if absent/unparseable
    error_code: Optional[str] = None  # DispatchErrorCode.* for pre-flight/transport failures


def is_configured() -> bool:
    return bool(os.getenv("MAILING_AGENT_BASE_URL"))


def _is_local_host(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() in _LOCAL_HOSTS


def _is_secure_transport(base_url: str) -> bool:
    parsed = urlparse(base_url)
    if parsed.scheme == "https":
        return True
    return parsed.scheme == "http" and _is_local_host(base_url)


def _timeout_seconds() -> float:
    """Finite, positive MAILING_AGENT_TIMEOUT_SECONDS, else the default
    (float() accepts nan/inf/0/negatives, none of which are usable)."""
    raw = os.getenv("MAILING_AGENT_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_TIMEOUT_SECONDS)
    if not math.isfinite(value) or value <= 0:
        return float(DEFAULT_TIMEOUT_SECONDS)
    return value


def org_api_key(organization_id: int) -> Optional[str]:
    """The Mailer API key mapped to this LeadBoost organization, or None.

    MAILING_AGENT_ORG_API_KEYS is a JSON object {"<leadboost_org_id>": "<key>"}.
    Malformed JSON, a non-object, a missing org, or an empty/non-string key all
    resolve to None (fail closed). The value is never logged.
    """
    raw = os.getenv("MAILING_AGENT_ORG_API_KEYS", "")
    if not raw.strip():
        return None
    try:
        mapping = json.loads(raw)
    except ValueError:
        logger.error("MAILING_AGENT_ORG_API_KEYS is not valid JSON; no Mailer keys are usable")
        return None
    if not isinstance(mapping, dict):
        logger.error("MAILING_AGENT_ORG_API_KEYS must be a JSON object; no Mailer keys are usable")
        return None
    key = mapping.get(str(organization_id))
    return key if isinstance(key, str) and key.strip() else None


async def send_request(
    method: str,
    path: str,
    *,
    organization_id: int,
    json_body: Optional[dict] = None,
) -> MailerResponse:
    """One organization-scoped call to the Mailer. Never raises for ordinary
    network/HTTP failure; maps them to a safe `error_code`. `json_body` may
    carry a credential (mailbox provisioning only) -- it is never logged and is
    dropped as soon as the request is sent."""
    if not is_configured():
        return MailerResponse(error_code=DispatchErrorCode.NOT_CONFIGURED)

    base_url = os.getenv("MAILING_AGENT_BASE_URL", "").rstrip("/")
    if not _is_secure_transport(base_url):
        logger.error("Refusing Mailer call over an insecure transport", extra={"organization_id": organization_id})
        return MailerResponse(error_code=DispatchErrorCode.INSECURE_TRANSPORT)

    api_key = org_api_key(organization_id)
    if not api_key:
        logger.error("No Mailer API key mapped for organization", extra={"organization_id": organization_id})
        return MailerResponse(error_code=DispatchErrorCode.AUTH_NOT_CONFIGURED)

    headers = {"X-API-Key": api_key}
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=_timeout_seconds()) as http_client:
            response = await http_client.request(method, f"{base_url}{path}", json=json_body, headers=headers)
    except httpx.TimeoutException:
        logger.warning("Mailer call timed out", extra={"organization_id": organization_id, "method": method})
        return MailerResponse(error_code=DispatchErrorCode.TIMEOUT)
    except httpx.HTTPError as exc:
        # Type only, never str(exc): keep request content out of logs.
        logger.warning(
            f"Mailer call unreachable ({type(exc).__name__})",
            extra={"organization_id": organization_id, "method": method},
        )
        return MailerResponse(error_code=DispatchErrorCode.UNREACHABLE)
    finally:
        del json_body  # drop any credential-bearing dict as soon as the request is sent

    try:
        data = response.json()
    except ValueError:
        data = None
    if not (200 <= response.status_code < 300) and response.status_code >= 500:
        logger.warning(
            "Mailer call failed server-side",
            extra={"organization_id": organization_id, "method": method, "status_code": response.status_code},
        )
    return MailerResponse(status_code=response.status_code, data=data)


def _clean(value: Optional[str], limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def build_generated_outreach_payload(
    *,
    outreach_action_id: int,
    idempotency_key: str,
    correlation_id: Optional[str],
    recipient_email: str,
    recipient_name: Optional[str],
    recipient_title: Optional[str],
    recipient_company: Optional[str],
    value_proposition: str,
    recipient_facts: list[str],
) -> dict:
    """The EXACT Mailer M2 request body (strict, extra fields are 422 there).
    No sender, SMTP, credential, subject/body, organization or mailbox field."""
    recipient: dict = {"email": recipient_email}
    for field, raw in (("name", recipient_name), ("title", recipient_title), ("company", recipient_company)):
        cleaned = _clean(raw, MAX_RECIPIENT_FIELD_CHARS)
        if cleaned:
            recipient[field] = cleaned

    facts = [f for f in (_clean(f, MAX_FACT_CHARS) for f in recipient_facts) if f][:MAX_FACTS]

    payload: dict = {
        "external_action_id": str(outreach_action_id),
        "idempotency_key": idempotency_key,
    }
    if correlation_id:
        payload["correlation_id"] = correlation_id
    payload["recipient"] = recipient
    payload["context"] = {
        "value_proposition": value_proposition.strip()[:MAX_VALUE_PROPOSITION_CHARS],
        "recipient_facts": facts,
    }
    return payload


async def submit_generated_outreach(
    *,
    organization_id: int,
    outreach_action_id: int,
    idempotency_key: str,
    correlation_id: Optional[str],
    recipient_email: str,
    recipient_name: Optional[str],
    recipient_title: Optional[str],
    recipient_company: Optional[str],
    value_proposition: str,
    recipient_facts: list[str],
) -> DispatchResult:
    """Hand ONE authorized OutreachAction to Mailer's generated-outreach route.

    Mailer returns 202 and does all generation/grounding/sending later, using
    the organization's sole ACTIVE mailbox. Idempotency is a hard requirement:
    an empty key is refused before any network attempt, and a retry of the same
    action reuses the same key so Mailer replays instead of double-sending.

    Success requires a 2xx AND an explicit boolean `accepted`; a missing or
    non-boolean value is INVALID_RESPONSE, never success.
    """
    if not idempotency_key:
        logger.error(
            "Refusing to dispatch an outreach action with no idempotency_key",
            extra={"organization_id": organization_id, "outreach_action_id": outreach_action_id},
        )
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.MISSING_IDEMPOTENCY_KEY)

    payload = build_generated_outreach_payload(
        outreach_action_id=outreach_action_id,
        idempotency_key=idempotency_key,
        correlation_id=correlation_id,
        recipient_email=recipient_email,
        recipient_name=recipient_name,
        recipient_title=recipient_title,
        recipient_company=recipient_company,
        value_proposition=value_proposition,
        recipient_facts=recipient_facts,
    )
    resp = await send_request("POST", GENERATED_OUTREACH_PATH, organization_id=organization_id, json_body=payload)

    if resp.error_code:
        return DispatchResult(accepted=False, error_code=resp.error_code)
    if resp.status_code == 409:
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.CONFLICT)
    if not (200 <= resp.status_code < 300):
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)

    data = resp.data
    if not isinstance(data, dict) or not isinstance(data.get("accepted"), bool):
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.INVALID_RESPONSE)
    if not data["accepted"]:
        return DispatchResult(accepted=False, error_code=DispatchErrorCode.REJECTED)

    reference = data.get("mailing_agent_reference")
    return DispatchResult(accepted=True, mailing_agent_reference=reference if isinstance(reference, str) else None)
