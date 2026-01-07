"""
LeadBoost SaaS - Production-grade Lead Intelligence Platform
API Gateway with FastAPI
"""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from core.infrastructure.database import init_db
from api.endpoints import leads, auth, organizations, billing
from core.infrastructure.logging import setup_logging

# Load environment variables
load_dotenv()

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager"""
    logger.info("Initializing LeadBoost SaaS API")
    # Initialize database
    init_db()

    # Initialize subscription plans
    from core.infrastructure.database import SessionLocal
    from core.infrastructure.billing.subscription_service import SubscriptionService

    db = SessionLocal()
    try:
        subscription_service = SubscriptionService(db)
        subscription_service.initialize_plans()
        logger.info("Subscription plans initialized")
    except Exception as e:
        logger.error(f"Error initializing subscription plans: {str(e)}")
    finally:
        db.close()

    yield
    logger.info("Shutting down LeadBoost SaaS API")


# Create FastAPI app
app = FastAPI(
    title="LeadBoost SaaS API",
    description="Production-grade Lead Intelligence Platform",
    version="2.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("ALLOWED_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(auth.router, prefix="/api/v2", tags=["auth"])
app.include_router(leads.router, prefix="/api/v2", tags=["leads"])
app.include_router(organizations.router, prefix="/api/v2", tags=["organizations"])
app.include_router(billing.router, prefix="/api/v2", tags=["billing"])


# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "LeadBoost SaaS API", "version": "2.0.0"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", 8000)),
        reload=os.getenv("ENVIRONMENT") == "development",
    )
