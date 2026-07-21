"""
Base agent interface.

Every agent owns exactly one business capability (see each agent's
docstring) and returns a Pydantic DTO (application.dto.models) that always
embeds an Explanation. Agents never talk to `core.infrastructure` directly
-- they go through application.services.infra_adapters / llm_provider, and
depend on application.interfaces.ports rather than concrete infra classes.
"""

from abc import ABC, abstractmethod
from typing import Any


class BaseAgent(ABC):
    name: str = "base_agent"

    @abstractmethod
    def run(self, *args: Any, **kwargs: Any) -> Any:
        """Synchronous agents implement `run`; async agents implement
        `arun` instead and may leave `run` unimplemented if not needed."""
        raise NotImplementedError
