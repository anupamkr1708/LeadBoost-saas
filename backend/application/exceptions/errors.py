"""
Application-layer exception hierarchy.

These are raised internally by agents/services and are almost always
*caught* at the workflow-node boundary (see application.utils.stage_logger)
so that a single stage failure degrades gracefully instead of crashing the
whole pipeline, per the "never crash the entire pipeline" requirement.
"""

from typing import Any, Dict, Optional


class ApplicationError(Exception):
    """Base class for all Application-layer errors."""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class AgentExecutionError(ApplicationError):
    """Raised when an agent fails to produce a usable result."""

    def __init__(
        self, agent_name: str, message: str, *, details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(f"[{agent_name}] {message}", details=details)
        self.agent_name = agent_name


class PromptError(ApplicationError):
    """Raised on prompt loading/rendering/validation failures."""


class LLMUnavailableError(ApplicationError):
    """Raised when an LLM call is required but no provider is configured."""


class WorkflowStageError(ApplicationError):
    """Raised (and normally caught) when a single workflow stage fails."""

    def __init__(
        self, stage: str, message: str, *, details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(f"[{stage}] {message}", details=details)
        self.stage = stage


class ContextBuildError(ApplicationError):
    """Raised when the context builder cannot assemble a usable context."""
