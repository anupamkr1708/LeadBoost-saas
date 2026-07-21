"""
Business memory interface.

Not conversational memory -- this recalls *business* facts across pipeline
runs: what we previously concluded about a company, what we previously
decided about a lead, and what we previously sent them. Keeping this behind
an interface means the storage backend (today: the AIDecisionLog table) can
be swapped later (e.g. a vector store for semantic recall across companies)
without touching any agent code.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BusinessMemory(ABC):
    @abstractmethod
    def get_previous_company_analysis(self, lead_id: int) -> Optional[Dict[str, Any]]:
        """Most recent Company Intelligence Agent output for this lead, if any."""

    @abstractmethod
    def get_previous_decisions(self, lead_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Previous Decision Agent outputs for this lead, most recent first."""

    @abstractmethod
    def get_previous_outreach(self, lead_id: int, limit: int = 5) -> List[Dict[str, Any]]:
        """Previous Messaging Agent outputs for this lead, most recent first."""

    @abstractmethod
    def store(
        self,
        lead_id: int,
        organization_id: int,
        stage: str,
        agent_name: str,
        **kwargs: Any,
    ) -> None:
        """Persist a new memory record for a pipeline stage."""
