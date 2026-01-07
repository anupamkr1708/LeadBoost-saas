"""
Async orchestration with Celery and Redis
"""

import asyncio
from typing import Dict, Any, Optional
from celery import Celery
from sqlalchemy.orm import Session
import os
from datetime import datetime

from core.infrastructure.database import SessionLocal
from core.domain.models.lead import Lead, ScrapingLog
from core.domain.models.user import User
from core.infrastructure.scraping.scraper import TieredScraper, ScrapingMethod
from core.infrastructure.enrichment.enricher import WaterfallEnricher
from core.infrastructure.messaging.messenger import Messenger
from core.infrastructure.database.crud import (
    get_lead,
    create_scraping_log,
    create_lead_enrichment_log,
    update_lead,
)
from core.domain.services.scoring import LeadScoringService
from core.infrastructure.logging import (
    get_logger,
    log_scraping_attempt,
    log_enrichment_attempt,
)

logger = get_logger(__name__)

# Initialize Celery
celery_app = Celery(
    "leadboost_orchestrator",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0"),
)

# Configure Celery
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def process_lead_task(self, lead_id: int) -> Dict[str, Any]:
    """
    Celery task to process a lead through the full pipeline
    """
    logger.info(f"Starting lead processing task for lead_id: {lead_id}")

    db = SessionLocal()
    try:
        # Get the lead
        lead = get_lead(db, lead_id)
        if not lead:
            logger.error(f"Lead not found: {lead_id}")
            return {"error": f"Lead {lead_id} not found"}

        # Step 1: Scrape the website
        # Run the async scrape function in a new event loop
        import concurrent.futures
        import threading

        def run_scrape():
            import asyncio
            import aiohttp
            from core.infrastructure.scraping.scraper import TieredScraper

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:

                async def scrape_directly():
                    scraper = TieredScraper()
                    # Initialize session directly in this event loop
                    scraper.session = aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=scraper.timeout)
                    )
                    try:
                        result = await scraper.scrape(lead.website)
                        return result
                    finally:
                        if scraper.session:
                            await scraper.session.close()

                return loop.run_until_complete(scrape_directly())
            finally:
                loop.close()

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(run_scrape)
            scraping_result = future.result()

        # Log scraping attempt
        create_scraping_log(
            db=db,
            lead_id=lead.id,
            scraping_method=scraping_result.method.value,
            success=scraping_result.success,
            confidence_score=scraping_result.confidence,
            error_message=scraping_result.error_message,
            processing_time_ms=(
                int(scraping_result.processing_time * 1000)
                if scraping_result.processing_time
                else None
            ),
            scraped_data=str(scraping_result.data) if scraping_result.data else None,
        )

        log_scraping_attempt(
            logger=logger,
            url=lead.website,
            method=scraping_result.method.value,
            success=scraping_result.success,
            confidence=scraping_result.confidence,
            processing_time=scraping_result.processing_time or 0,
            error_message=scraping_result.error_message,
        )

        if scraping_result.success:
            # Update lead with scraped data
            update_data = {
                "scrape_confidence": scraping_result.confidence,
                "scrape_source": scraping_result.method.value,
            }

            # Extract relevant data from scraping result
            scraped_data = scraping_result.data
            if "title" in scraped_data:
                update_data["company_name"] = scraped_data["title"]
            if "description" in scraped_data:
                update_data["about_text"] = scraped_data["description"]
            if "og_description" in scraped_data:
                update_data["about_text"] = scraped_data["og_description"]
            if "email" in scraped_data:
                update_data["email"] = scraped_data["email"]
            if "phone" in scraped_data:
                update_data["phone"] = scraped_data["phone"]
            if "links" in scraped_data:
                for link in scraped_data["links"]:
                    if "linkedin.com" in link:
                        update_data["linkedin_url"] = link
                        break

            # Update the lead
            from core.domain.schemas.lead import LeadUpdate

            lead_update_obj = LeadUpdate(**update_data)
            update_lead(db, lead_id, lead_update_obj)

        # Step 2: Enrich the data
        # Check if AI features are available for this organization
        from core.infrastructure.billing.subscription_service import SubscriptionService

        db_subscription = SessionLocal()
        subscription_service = SubscriptionService(db_subscription)

        if subscription_service.can_use_ai_features(lead.organization_id):
            enricher = WaterfallEnricher()
            enrichment_result = enricher.enrich_lead_data(
                lead, scraping_result.data if scraping_result.success else {}
            )
        else:
            # Skip enrichment if AI features are not available
            enrichment_result = None
            logger.info(
                f"AI features not available for organization {lead.organization_id}, skipping enrichment"
            )

        # Close the subscription DB session
        db_subscription.close()

        if enrichment_result:
            # Log enrichment attempt
            create_lead_enrichment_log(
                db=db,
                lead_id=lead.id,
                enrichment_type=enrichment_result.method,
                enrichment_data=str(enrichment_result.data),
                confidence_score=enrichment_result.confidence,
                processing_time_ms=enrichment_result.processing_time,
            )

            log_enrichment_attempt(
                logger=logger,
                lead_id=lead.id,
                method=enrichment_result.method,
                success=True,
                confidence=enrichment_result.confidence,
                processing_time=enrichment_result.processing_time or 0,
            )

            # Update lead with enrichment data
            update_data = {
                "enrichment_confidence": enrichment_result.confidence,
                "enrichment_source": enrichment_result.method,
            }

            # Extract relevant data from enrichment result
            enriched_data = enrichment_result.data
            if "industry" in enriched_data:
                update_data["industry"] = enriched_data["industry"]
            if "employees" in enriched_data:
                update_data["employees"] = enriched_data["employees"]
            if "revenue_band" in enriched_data:
                update_data["revenue_band"] = enriched_data["revenue_band"]
            if "founded_year" in enriched_data:
                update_data["founded_year"] = enriched_data["founded_year"]
            if "contact_name" in enriched_data:
                update_data["contact_name"] = enriched_data["contact_name"]
            if "contact_title" in enriched_data:
                update_data["contact_title"] = enriched_data["contact_title"]

            # Update the lead
            from core.domain.schemas.lead import LeadUpdate

            lead_update_obj = LeadUpdate(**update_data)
            update_lead(db, lead_id, lead_update_obj)

        # Step 3: Score the lead
        scoring_service = LeadScoringService()
        score_result = scoring_service.score_lead(lead)

        # Update lead with score
        update_data = {
            "score": score_result.total_score,
            "qualification_label": score_result.qualification_label,
        }
        from core.domain.schemas.lead import LeadUpdate

        lead_update_obj = LeadUpdate(**update_data)
        update_lead(db, lead_id, lead_update_obj)

        # Step 4: Generate outreach message
        # Check if AI features are available for this organization
        db_subscription = SessionLocal()
        subscription_service = SubscriptionService(db_subscription)

        if subscription_service.can_use_ai_features(lead.organization_id):
            messenger = Messenger()
            outreach_message = messenger.generate_message(lead)
        else:
            # Use a basic message if AI features are not available
            outreach_message = (
                "No outreach message generated - AI features not available on your plan"
            )
            logger.info(
                f"AI features not available for organization {lead.organization_id}, using basic message"
            )

        # Close the subscription DB session
        db_subscription.close()

        if outreach_message:
            update_data = {"outreach_message": outreach_message}
            from core.domain.schemas.lead import LeadUpdate

            lead_update_obj = LeadUpdate(**update_data)
            update_lead(db, lead_id, lead_update_obj)

        # Commit all changes
        db.commit()

        logger.info(f"Lead processing completed successfully for lead_id: {lead_id}")
        return {
            "status": "success",
            "lead_id": lead_id,
            "scraping_success": scraping_result.success,
            "enrichment_success": bool(enrichment_result),
        }

    except Exception as exc:
        logger.error(f"Lead processing failed for lead_id {lead_id}: {str(exc)}")
        # Retry the task
        raise self.retry(exc=exc, countdown=60, max_retries=3)

    finally:
        db.close()


import asyncio
import concurrent.futures


async def process_lead_async(lead_id: int) -> Dict[str, Any]:
    """
    Asynchronous function to trigger lead processing
    """
    # Run the Celery task in a thread pool to avoid blocking
    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        result = await loop.run_in_executor(executor, process_lead_task, lead_id)
    return result


def get_task_status(task_id: str) -> Dict[str, Any]:
    """
    Get the status of a processing task
    """
    task = celery_app.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": task.status,
        "result": task.result if task.ready() else None,
    }
