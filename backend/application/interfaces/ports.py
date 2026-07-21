"""
Ports: structural interfaces (typing.Protocol) that agents and workflow
nodes depend on, instead of importing concrete `core.infrastructure.*`
classes directly.

Python Protocols are structurally typed, so the existing infrastructure
classes (TieredScraper, WaterfallEnricher, LeadScoringService, Messenger)
already satisfy these Ports without needing to be modified or wrapped --
this gives us dependency-inversion without introducing a new abstraction
layer or repository pattern over the existing infrastructure.

Where a signature genuinely needs adapting (e.g. sync -> async, or
DB-session threading), a thin function lives in
application.services.infra_adapters -- never a rewrite of the infra class
itself.
"""

from typing import Any, Dict, Optional, Protocol, runtime_checkable


@runtime_checkable
class ScraperPort(Protocol):
    async def scrape(self, url: str) -> Any: ...


@runtime_checkable
class EnricherPort(Protocol):
    def enrich_lead_data(self, lead: Any, scraped_data: Dict[str, Any]) -> Any: ...


@runtime_checkable
class ScorerPort(Protocol):
    def score_lead(self, lead: Any) -> Any: ...


@runtime_checkable
class MessengerPort(Protocol):
    def generate_message(self, lead: Any) -> Optional[str]: ...


@runtime_checkable
class LLMClientPort(Protocol):
    def invoke(self, inputs: Dict[str, Any]) -> Any: ...


@runtime_checkable
class BusinessMemoryPort(Protocol):
    def get_previous_company_analysis(self, lead_id: int) -> Optional[Dict[str, Any]]: ...

    def get_previous_decisions(self, lead_id: int) -> Any: ...

    def get_previous_outreach(self, lead_id: int) -> Any: ...

    def store(
        self,
        lead_id: int,
        organization_id: int,
        stage: str,
        agent_name: str,
        **kwargs: Any,
    ) -> None: ...
