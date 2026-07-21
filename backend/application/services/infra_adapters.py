"""
Infrastructure adapters.

Every function here is a thin delegation to an existing, unmodified
`core.infrastructure` component. Nothing here reimplements business logic --
it only adapts calling conventions (e.g. opening a scraper's async context)
so that workflow nodes can depend on application.interfaces.ports rather
than importing core classes directly.

Also hosts small read-only query helpers used by the context builder and
memory implementation. These are plain SQLAlchemy queries against the
existing domain models (the same style crud.py already uses) -- not a new
repository abstraction, just colocated here because they are
application/AI-context-specific reads rather than general-purpose CRUD.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from core.domain.models.lead import AIDecisionLog, Lead, LeadEnrichmentLog, ScrapingLog
from core.domain.models.organization import Organization
from core.domain.services.scoring import LeadScoringService, ScoreResult
from core.infrastructure.billing.subscription_service import SubscriptionService
from core.infrastructure.enrichment.enricher import EnrichmentResult, WaterfallEnricher
from core.infrastructure.logging import get_logger
from core.infrastructure.messaging.messenger import Messenger
from core.infrastructure.scraping.scraper import ScrapingResult, TieredScraper

logger = get_logger("application.infra_adapters")


# -- Scraper -------------------------------------------------------------


async def scrape_lead(url: str) -> ScrapingResult:
    """Runs the existing hybrid TieredScraper for a single URL."""
    async with TieredScraper() as scraper:
        return await scraper.scrape(url)


# -- Enrichment ------------------------------------------------------------


def enrich_lead(lead: Lead, scraped_data: Dict[str, Any]) -> Optional[EnrichmentResult]:
    """Delegates to the existing WaterfallEnricher, unchanged."""
    enricher = WaterfallEnricher()
    return enricher.enrich_lead_data(lead, scraped_data)


# -- Scoring -----------------------------------------------------------------


def score_lead(lead: Lead) -> ScoreResult:
    """Delegates to the existing deterministic LeadScoringService."""
    scoring_service = LeadScoringService()
    return scoring_service.score_lead(lead)


# -- Messaging (deterministic fallback path) ----------------------------------


def generate_template_message(lead: Lead) -> Optional[str]:
    """
    Delegates to the existing data-locked Messenger. Used by the Messaging
    Agent as its guaranteed-safe fallback when the LLM is unavailable, and
    reused directly (not duplicated) rather than re-implementing template
    logic in the Application layer.
    """
    messenger = Messenger()
    return messenger.generate_message(lead)


# -- Subscription / AI feature gating ------------------------------------------


def check_ai_features_enabled(db: Session, organization_id: int) -> bool:
    """Delegates to the existing SubscriptionService gating logic."""
    subscription_service = SubscriptionService(db)
    return subscription_service.can_use_ai_features(organization_id)


# -- Read-only context/memory queries ------------------------------------------


def get_organization(db: Session, organization_id: int) -> Optional[Organization]:
    return db.query(Organization).filter(Organization.id == organization_id).first()


def get_recent_scraping_logs(db: Session, lead_id: int, limit: int = 5) -> List[ScrapingLog]:
    return (
        db.query(ScrapingLog)
        .filter(ScrapingLog.lead_id == lead_id)
        .order_by(ScrapingLog.created_at.desc())
        .limit(limit)
        .all()
    )


def get_recent_enrichment_logs(
    db: Session, lead_id: int, limit: int = 5
) -> List[LeadEnrichmentLog]:
    return (
        db.query(LeadEnrichmentLog)
        .filter(LeadEnrichmentLog.lead_id == lead_id)
        .order_by(LeadEnrichmentLog.created_at.desc())
        .limit(limit)
        .all()
    )


def get_recent_ai_decision_logs(
    db: Session, lead_id: int, stage: Optional[str] = None, limit: int = 10
) -> List[AIDecisionLog]:
    query = db.query(AIDecisionLog).filter(AIDecisionLog.lead_id == lead_id)
    if stage:
        query = query.filter(AIDecisionLog.stage == stage)
    return query.order_by(AIDecisionLog.created_at.desc()).limit(limit).all()


def get_previous_leads_for_organization(
    db: Session, organization_id: int, exclude_lead_id: int, limit: int = 5
) -> List[Lead]:
    """Small amount of org-level history for context (e.g. recently
    processed companies in the same industry) -- a pluggable extension
    point that a future CRM integration can enrich further."""
    return (
        db.query(Lead)
        .filter(Lead.organization_id == organization_id, Lead.id != exclude_lead_id)
        .order_by(Lead.created_at.desc())
        .limit(limit)
        .all()
    )
