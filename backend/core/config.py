"""
Startup environment validation.

This does NOT replace the existing pattern of reading individual
`os.getenv(...)` calls with sane defaults throughout the codebase (that
would be a much larger refactor than this polish pass calls for, and
those call sites already work correctly). It adds one thing that was
missing: a single, fail-fast check at application startup that catches
the specific misconfigurations that would otherwise surface later as a
confusing runtime error (or, worse, a silent security hole) --
principally, forgetting to set a real SECRET_KEY before deploying to
production.

Two severities:
  - `errors`: startup must not proceed (e.g. an insecure default secret
    key in production). Raises RuntimeError with every problem listed at
    once, rather than failing once, getting fixed, and immediately
    failing again on the next missing variable.
  - `warnings`: features will visibly degrade rather than break (e.g. no
    GROQ_API_KEY means AI-powered enrichment/qualification/outreach
    quietly falls back to the existing deterministic paths, as designed)
    -- logged, not fatal.
"""

import os
from typing import List, Tuple

from core.infrastructure.logging import get_logger

logger = get_logger("core.config")

# The exact placeholder shipped in .env.example -- if this is still the
# value in a production environment, JWTs are effectively signed with a
# publicly-known key.
_INSECURE_DEFAULT_SECRET_KEY = "your-super-secret-key-change-in-production"
_MIN_SECRET_KEY_LENGTH = 32

# (env var, human-readable feature it powers) -- missing these degrades
# a feature gracefully rather than breaking anything, so they're
# warnings, not errors. Every one of these already has graceful-fallback
# handling at its call site; this is purely a "did you mean to leave this
# unset?" heads-up.
_RECOMMENDED_VARS: List[Tuple[str, str]] = [
    ("GROQ_API_KEY", "AI-powered enrichment, qualification, and outreach generation"),
    ("SERPER_API_KEY", "website-resolution fallback and the startup/SaaS discovery fallback"),
    ("STRIPE_SECRET_KEY", "future billing integration (not active yet -- see PART 10)"),
]


def validate_startup_environment() -> None:
    """Raises RuntimeError with an aggregated, actionable message if any
    hard requirement is violated. Logs a warning (does not raise) for
    recommended-but-optional configuration. Safe to call multiple times;
    intended to run once, first, at application startup -- before the
    database connection is attempted -- so a misconfiguration is reported
    clearly instead of surfacing as an opaque downstream failure.
    """
    environment = os.getenv("ENVIRONMENT", "development").lower()
    errors: List[str] = []

    secret_key = os.getenv("SECRET_KEY", _INSECURE_DEFAULT_SECRET_KEY)
    if environment == "production":
        if secret_key == _INSECURE_DEFAULT_SECRET_KEY:
            errors.append(
                "SECRET_KEY is still the placeholder value from .env.example. "
                "Set a real, random secret (e.g. `python -c \"import secrets; "
                "print(secrets.token_urlsafe(48))\"`) before running in production -- "
                "every JWT issued would otherwise be forgeable by anyone who has read "
                "the source code."
            )
        elif len(secret_key) < _MIN_SECRET_KEY_LENGTH:
            errors.append(
                f"SECRET_KEY is only {len(secret_key)} characters long. Use at least "
                f"{_MIN_SECRET_KEY_LENGTH} random characters in production."
            )

        if not os.getenv("DATABASE_URL", "").startswith("postgresql"):
            # core.infrastructure.database already enforces this at import
            # time; restated here so it appears in the same aggregated
            # error list instead of a separate crash earlier in the import
            # chain, if this function is ever called before that import.
            errors.append(
                "DATABASE_URL must be a postgresql:// URL in production (sqlite is "
                "for local development only)."
            )

        allowed_origins = os.getenv("ALLOWED_ORIGINS", "")
        if "*" in allowed_origins:
            errors.append(
                "ALLOWED_ORIGINS contains '*' in production -- CORS would accept "
                "requests from any origin. List the specific frontend origin(s) instead."
            )

    if errors:
        message = "Refusing to start due to configuration problem(s):\n" + "\n".join(
            f"  - {e}" for e in errors
        )
        logger.error(message)
        raise RuntimeError(message)

    for var_name, feature in _RECOMMENDED_VARS:
        if not os.getenv(var_name):
            logger.warning(
                f"{var_name} is not set -- {feature} will run in its degraded/"
                f"deterministic fallback mode instead. This is fine for local "
                f"development or a deliberately AI-free deployment."
            )
