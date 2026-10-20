"""
Owner/operator-only authentication for the global Prometheus ``/metrics`` route.

``/metrics`` exposes process-global telemetry (all organizations: totals, success
rates, request counts) -- not tenant data -- so it must be readable only by the
operator's scraper. It uses ONE dedicated bearer credential, ``METRICS_BEARER_TOKEN``,
which is deliberately independent of every other secret in the application:

* It is never ``SECRET_KEY``, a customer JWT, an email-credential encryption key or a
  Mailer tenant API key. A configured value that equals any of those is treated as NOT
  configured (fail closed) rather than being accepted.
* It is generated and provisioned by the operator only. Nothing here generates,
  stores, logs or returns it.

Behaviour (the route dependency below runs BEFORE the route's ``get_db`` dependency, so a
rejected request never opens a database session):

* token not configured / blank / shorter than 32 characters / not distinct -> 403 (the
  route is effectively disabled; never an anonymous body)
* configured, header missing / not ``Bearer`` / empty / wrong -> 401 + ``WWW-Authenticate: Bearer``
* configured and correct -> request proceeds

The token is read from the environment on each request (like the other ``os.getenv``
call sites in this codebase), so tests can set it with ``monkeypatch``; changing it in a
deployment is a restart-and-redeploy operation anyway.
"""

import hmac
import json
import os
from typing import Optional

from fastapi import HTTPException, Request, status

METRICS_TOKEN_ENV = "METRICS_BEARER_TOKEN"
MIN_METRICS_TOKEN_LENGTH = 32

# Other credentials the metrics token must never equal. Only variables that this
# application actually reads are listed; extend deliberately, never with a wildcard.
_DISTINCT_FROM_ENV = (
    "SECRET_KEY",
    "EMAIL_CREDENTIAL_ENCRYPTION_KEY",
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "GROQ_API_KEY",
    "SERPER_API_KEY",
)


def _other_credentials() -> set:
    """Values of every unrelated credential the metrics token must differ from."""
    values = set()
    for name in _DISTINCT_FROM_ENV:
        value = os.getenv(name, "")
        if value:
            values.add(value)
    # Mailer tenant API keys: {"<leadboost_org_id>": "<key>"}. Malformed JSON is ignored
    # here (the Mailer client reports it); this only collects usable string values.
    raw = os.getenv("MAILING_AGENT_ORG_API_KEYS", "")
    if raw.strip():
        try:
            mapping = json.loads(raw)
        except ValueError:
            mapping = None
        if isinstance(mapping, dict):
            values.update(v for v in mapping.values() if isinstance(v, str) and v)
    return values


def configured_metrics_token_problem() -> Optional[str]:
    """Why the configured token is unusable, or None if it is usable (or unset).

    Returns a short, value-free reason. Used for the startup warning so an operator who
    sets an invalid token learns why scrapes get 403 -- never include the token itself.
    """
    token = os.getenv(METRICS_TOKEN_ENV, "")
    if not token.strip():
        return None  # unset == deliberately disabled; not a misconfiguration
    if token != token.strip():
        return "has leading or trailing whitespace"
    if len(token) < MIN_METRICS_TOKEN_LENGTH:
        return f"is shorter than {MIN_METRICS_TOKEN_LENGTH} characters"
    if token in _other_credentials():
        return "is identical to another configured credential"
    return None


def _usable_token() -> Optional[str]:
    """The configured token if (and only if) it is safe to use, else None."""
    token = os.getenv(METRICS_TOKEN_ENV, "")
    if not token.strip():
        return None
    if configured_metrics_token_problem() is not None:
        return None
    return token


def _presented_credential(request: Request) -> Optional[bytes]:
    """The bearer credential as bytes, or None when the header is absent/not Bearer/empty."""
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    credential = credential.strip()
    if not credential:
        return None
    # Header values arrive as latin-1 text; encode to bytes so any non-ASCII input is simply
    # "not equal" rather than an exception (compare_digest rejects non-ASCII str).
    return credential.encode("utf-8")


def require_metrics_token(request: Request) -> None:
    """FastAPI dependency guarding the global ``/metrics`` route. See module docstring."""
    expected = _usable_token()
    if expected is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Metrics endpoint is not enabled",
        )

    presented = _presented_credential(request)
    if presented is None or not hmac.compare_digest(presented, expected.encode("utf-8")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
