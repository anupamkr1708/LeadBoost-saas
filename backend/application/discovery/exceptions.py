"""
Discovery-layer exceptions.

Mirrors application.exceptions.errors in spirit (a small, flat hierarchy),
kept separate because discovery failures are handled differently: most are
caught and turned into graceful "skip this business" outcomes rather than
propagating, per the spec's error-handling table (provider unavailable ->
retry -> fallback -> graceful failure; resolution failure -> continue;
validation failure -> skip; pipeline failure -> continue remaining leads).
"""

from typing import Any, Dict, Optional


class DiscoveryError(Exception):
    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class QueryParseError(DiscoveryError):
    """Raised when a natural-language query cannot be parsed into a
    category + location. Not retryable -- the caller should return a
    clear 400 to the user."""


class ProviderError(DiscoveryError):
    """Raised when a search/resolution provider fails after retries."""

    def __init__(self, provider: str, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(f"[{provider}] {message}", details=details)
        self.provider = provider


class WebsiteValidationError(DiscoveryError):
    """Raised internally by the validator; normally caught and turned into
    a WebsiteResolution(validated=False, rejection_reason=...) rather than
    propagated."""
