"""
LeadBoost SaaS - Production-grade Lead Intelligence Platform
API Gateway with FastAPI
"""

import os
import logging
import uuid
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import time

from core.infrastructure.database import init_db, get_db
from api.endpoints import leads, auth, organizations, billing, analytics, discovery
from core.infrastructure.logging import setup_logging
from core.observability import prometheus_metrics

# Load environment variables
load_dotenv()

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager with retry logic"""
    logger.info("Initializing LeadBoost SaaS API")

    from core.config import validate_startup_environment

    validate_startup_environment()

    # Initialize database with retry
    max_retries = 5
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            init_db()
            logger.info("Database initialized successfully")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database initialization attempt {attempt + 1} failed: {e}. Retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay *= 2
            else:
                logger.error(f"Database initialization failed after {max_retries} attempts: {e}")
                raise

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

    logger.info("LeadBoost SaaS API started successfully")

    # P0-A (durable job execution): start the in-process job worker loop.
    # See application/execution/worker_loop.py for why this is
    # deliberately not a separate deployment/process.
    from application.execution import worker_loop

    worker_loop.start()

    yield

    # Graceful shutdown
    logger.info("Shutting down LeadBoost SaaS API")

    from application.execution import worker_loop

    await worker_loop.stop()

    # Close the Application layer's shared Playwright browser pool (see
    # core/infrastructure/scraping/scraper.py). Safe no-op if no browser
    # was ever launched during this process's lifetime.
    try:
        from core.infrastructure.scraping.scraper import close_scraper_resources

        await close_scraper_resources()
    except Exception as e:
        logger.warning(f"Error closing scraper resources: {e}")
    # Engine cleanup happens automatically via SQLAlchemy


# Create FastAPI app
app = FastAPI(
    title="LeadBoost SaaS API",
    description="Production-grade Lead Intelligence Platform",
    version="2.0.0",
    lifespan=lifespan,
)

# Get allowed origins, default to empty list (no CORS) if not set
allowed_origins = os.getenv("ALLOWED_ORIGINS", "")
origins_list = [origin.strip() for origin in allowed_origins.split(",") if origin.strip()]

if origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,  # Cache preflight for 10 minutes
    )
    logger.info(f"CORS enabled for origins: {origins_list}")
else:
    logger.warning("CORS not configured - no ALLOWED_ORIGINS set")


# Request ID Middleware
@app.middleware("http")
async def add_request_id_and_timing(request: Request, call_next):
    """Add request ID and measure execution time"""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id

    start_time = time.time()

    # Process request
    response = await call_next(request)

    # Calculate duration
    duration_seconds = time.time() - start_time
    duration_ms = int(duration_seconds * 1000)

    # Add headers
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time"] = f"{duration_ms}ms"

    # Prometheus metrics (labeled by route *template*, e.g.
    # "/leads/{lead_id}", never the raw path -- see
    # core.observability.prometheus_metrics.route_template for why).
    try:
        path_label = prometheus_metrics.route_template(request)
        prometheus_metrics.http_requests_total.labels(
            method=request.method, path=path_label, status_code=str(response.status_code)
        ).inc()
        prometheus_metrics.http_request_duration_seconds.labels(
            method=request.method, path=path_label
        ).observe(duration_seconds)
    except Exception:
        # Metrics must never be able to break a real request.
        logger.debug("Failed to record request metrics", exc_info=True)

    # Log request
    logger.info(
        "Request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        }
    )

    return response


# Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions"""
    request_id = getattr(request.state, "request_id", "unknown")

    logger.error(
        "Unhandled exception",
        exc_info=True,
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "method": request.method,
            "error_type": type(exc).__name__,
        }
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "request_id": request_id,
        }
    )

# Include API routes
app.include_router(auth.router, prefix="/api/v2", tags=["auth"])
app.include_router(leads.router, prefix="/api/v2", tags=["leads"])
app.include_router(organizations.router, prefix="/api/v2", tags=["organizations"])
app.include_router(billing.router, prefix="/api/v2", tags=["billing"])
app.include_router(analytics.router, prefix="/api/v2", tags=["analytics"])
app.include_router(discovery.router, prefix="/api/v2", tags=["discovery"])


# Health check endpoints
@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """
    Comprehensive health check - checks all dependencies
    Returns 503 if any dependency is unhealthy
    """
    health_status = {
        "status": "healthy",
        "service": "LeadBoost SaaS API",
        "version": "2.0.0",
        "checks": {}
    }

    is_healthy = True

    # Check database
    try:
        db.execute(text("SELECT 1"))
        health_status["checks"]["database"] = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_status["checks"]["database"] = f"unhealthy: {str(e)}"
        is_healthy = False

    # Check Redis
    try:
        import redis
        redis_url = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
        redis_client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
        redis_client.ping()
        redis_client.close()
        health_status["checks"]["redis"] = "healthy"
    except Exception as e:
        logger.error(f"Redis health check failed: {e}")
        health_status["checks"]["redis"] = f"unhealthy: {str(e)}"
        is_healthy = False

    if not is_healthy:
        health_status["status"] = "unhealthy"
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=health_status
        )

    return health_status


@app.get("/ready")
async def readiness_check(db: Session = Depends(get_db)):
    """
    Readiness check for Kubernetes
    Returns 200 if service is ready to accept traffic
    """
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not ready", "error": str(e)}
        )


@app.get("/live")
async def liveness_check():
    """
    Liveness check for Kubernetes
    Returns 200 if service is alive (doesn't check dependencies)
    """
    return {"status": "alive"}


@app.get("/metrics")
async def metrics(db: Session = Depends(get_db)):
    """
    Prometheus scrape endpoint (SECTION 7 of the production-polish brief).
    No auth by design -- this is standard practice for Prometheus targets
    and matches how /health, /ready, /live are already exposed; put this
    behind network-level access control (a private network / VPC, or an
    IP allowlist at the reverse proxy) in production rather than
    application-level auth, so Prometheus itself doesn't need credentials.
    Contains only counts, durations, and status codes -- no customer
    content of any kind.
    """
    from fastapi import Response

    body, content_type = prometheus_metrics.render_latest(db)
    return Response(content=body, media_type=content_type)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("API_PORT", 8000)),
        reload=os.getenv("ENVIRONMENT") == "development",
    )
