"""
Retry/backoff helpers for the Application layer.

Thin wrapper over `tenacity` so agents/services get consistent retry
behaviour (exponential backoff + jitter, bounded attempts) without each
one hand-rolling its own loop.
"""

from typing import Callable, Tuple, Type

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from core.infrastructure.logging import get_logger

logger = get_logger("application.retry")


def with_retry(
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    attempts: int = 2,
    min_wait: float = 1.0,
    max_wait: float = 6.0,
) -> Callable:
    """
    Decorator factory for retrying transient failures (e.g. LLM API calls).

    Kept intentionally conservative (2 attempts by default) -- this is for
    transient network/rate-limit issues, not a substitute for the pipeline's
    own graceful degradation (a stage that keeps failing should fall back
    to a deterministic path, not retry forever).
    """

    return retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_random_exponential(multiplier=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
    )
