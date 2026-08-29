"""
Centralized LLM provider.

`core/infrastructure/enrichment/enricher.py` and
`core/infrastructure/messaging/messenger.py` each independently construct a
`ChatGroq` client and each independently guard against a missing API key.
This module centralizes that pattern for the new Application-layer agents
so it exists in exactly one place, following the same conventions (same
env vars, same graceful-degradation-on-missing-key behaviour) as the
existing infrastructure -- it does not introduce a new LLM integration
pattern, it reuses the existing one.

Production-robustness hardening (Phase B4, LLM concurrency): all three of
the call sites above share one Groq API key/account and therefore one
TPM (tokens-per-minute) budget, but until this change none of them
coordinated with each other or bounded how many calls could be in flight
at once -- multiple pipeline stages (company intelligence, decision,
messaging) and multiple concurrently-processing leads could all reach
Groq at the same moment, which is exactly the failure mode real testing
against this codebase already hit (Groq TPM limits). `llm_call_slot()`
below is one process-wide `threading.Semaphore` (safe to acquire/release
from the worker threads every one of these three call sites actually
runs on, since each is reached via `asyncio.to_thread` -- see
application/workflows/graph_nodes.py) that all three now acquire around
their actual `.invoke()` call, bounding total concurrent Groq calls to
`LLM_MAX_CONCURRENT_CALLS` regardless of which code path triggers them.
This does not touch retry/backoff, JSON parsing, or fallback behavior in
any of the three call sites -- it only wraps the network call itself.
"""

import json
import os
import re
import threading
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from core.infrastructure.logging import get_logger

logger = get_logger("application.llm_provider")

_LLM_MAX_CONCURRENT_CALLS = max(1, int(os.getenv("LLM_MAX_CONCURRENT_CALLS", "4")))
_llm_call_semaphore = threading.Semaphore(_LLM_MAX_CONCURRENT_CALLS)


@contextmanager
def llm_call_slot():
    """Blocks until fewer than `LLM_MAX_CONCURRENT_CALLS` Groq calls are
    in flight process-wide, then yields. Use as a `with` block around the
    actual `.invoke()`/`.invoke({})` call -- see this module's docstring
    for why this exists and where else it's used
    (core/infrastructure/enrichment/enricher.py,
    core/infrastructure/messaging/messenger.py)."""
    _llm_call_semaphore.acquire()
    try:
        yield
    finally:
        _llm_call_semaphore.release()


def is_llm_available() -> bool:
    """Mirrors the exact check already used in enricher.py / messenger.py."""
    api_key = os.getenv("GROQ_API_KEY")
    return bool(api_key) and api_key != "local_test_mode"


def get_model_name() -> str:
    return os.getenv("LLM_MODEL", "openai/gpt-oss-120b")


def get_llm(temperature: float = 0.0, max_tokens: int = 600):
    """
    Construct a ChatGroq client. Returns None (never raises) if the LLM is
    unavailable or langchain-groq isn't installed, so callers can fall back
    to deterministic logic -- the same pattern already used throughout
    core/infrastructure.
    """
    if not is_llm_available():
        return None
    try:
        from langchain_groq import ChatGroq

        return ChatGroq(
            api_key=os.getenv("GROQ_API_KEY"),
            model=get_model_name(),
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except ImportError:
        logger.warning("langchain-groq not installed, LLM features disabled")
        return None
    except Exception as e:
        logger.error(f"Failed to initialize LLM client: {e}")
        return None


def _invoke_chain_with_retry(chain, inputs: Dict[str, Any]) -> Tuple[Any, int]:
    """
    Invokes `chain.invoke(inputs)` with retry/backoff, returning
    `(response, retry_count)`.

    Uses a fresh `tenacity.Retrying` instance per call (rather than the
    `@retry` decorator) so its attempt-count statistics are isolated to
    this single invocation -- safe under concurrent/threaded execution,
    where a decorator-level shared statistics dict would be a race
    condition. retry_count = attempts - 1 (0 when it succeeded first try).
    """
    retryer = Retrying(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=1.0, max=4.0),
        retry=retry_if_exception_type(Exception),
    )
    with llm_call_slot():
        response = retryer(chain.invoke, inputs)
    attempts = retryer.statistics.get("attempt_number", 1)
    return response, max(0, attempts - 1)


def safe_invoke_json(
    prompt_messages: List[Tuple[str, str]],
    inputs: Dict[str, Any],
    *,
    temperature: float = 0.0,
    max_tokens: int = 600,
) -> Tuple[Optional[Dict[str, Any]], int]:
    """
    Build a ChatPromptTemplate from `prompt_messages`, invoke it once against
    the LLM with retry, and parse a JSON object out of the response.

    Returns `(payload, retry_count)`. `payload` is None (never raises) on
    any failure -- every agent using this must have a deterministic
    fallback, which is enforced by convention throughout application/agents/.
    `retry_count` reflects how many retries the LLM call needed (0 if it
    was never attempted, e.g. llm is None) and is surfaced by callers for
    stage logging and prompt-execution tracking (see
    application.observability).
    """
    llm = get_llm(temperature=temperature, max_tokens=max_tokens)
    if llm is None:
        return None, 0

    retry_count = 0
    try:
        from langchain_core.prompts import ChatPromptTemplate

        prompt = ChatPromptTemplate.from_messages(prompt_messages)
        chain = prompt | llm
        response, retry_count = _invoke_chain_with_retry(chain, inputs)
        content = response.content if hasattr(response, "content") else str(response)

        print("\n" + "=" * 80)
        print("RAW LLM RESPONSE")
        print("=" * 80)
        print("MODEL:", get_model_name())

        print("CONTENT LENGTH:", len(content))

        print("RAW:")
        print(repr(content))

        print()

        print("RESPONSE METADATA:")
        print(getattr(response, "response_metadata", None))

        print()

        print("ADDITIONAL KWARGS:")
        print(getattr(response, "additional_kwargs", None))

        print("=" * 80 + "\n")

        json_match = re.search(r"\{.*\}", content, re.DOTALL)
        if not json_match:
            logger.warning("No JSON object found in LLM response")
            return None, retry_count

        return json.loads(json_match.group()), retry_count

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM response as JSON: {e}")
        return None, retry_count
    except Exception as e:
        logger.error(f"LLM invocation failed: {e}")
        return None, retry_count
