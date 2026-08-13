"""
Database initialization module with production-grade configuration
"""

from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
import os
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Get database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable is required")
    raise ValueError("DATABASE_URL environment variable is required")

# Determine if we're using PostgreSQL
is_postgresql = DATABASE_URL.startswith("postgresql")
is_sqlite = DATABASE_URL.startswith("sqlite")

# Production mode check
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
is_production = ENVIRONMENT == "production"

# Validate database choice in production
if is_production and not is_postgresql:
    logger.error("Production requires PostgreSQL database")
    raise ValueError("Production environment requires PostgreSQL (DATABASE_URL must start with postgresql://)")

# Create engine with appropriate configuration
if is_postgresql:
    logger.info("Configuring PostgreSQL database connection")
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # Verify connections before use
        pool_size=int(os.getenv("DB_POOL_SIZE", "20")),  # Connection pool size
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "40")),  # Additional connections
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "3600")),  # Recycle after 1 hour
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),  # Wait 30s for connection
        echo=os.getenv("DB_ECHO", "false").lower() == "true",
        connect_args={
            "connect_timeout": 10,  # Connection timeout
            "options": "-c statement_timeout=30000"  # 30s query timeout
        }
    )
    logger.info(f"PostgreSQL pool configured: size={engine.pool.size()}, max_overflow={engine.pool._max_overflow}")
elif is_sqlite:
    logger.warning("Using SQLite database - not recommended for production")
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=NullPool if is_production else None,  # No pooling in production
        echo=os.getenv("DB_ECHO", "false").lower() == "true",
    )
else:
    raise ValueError(f"Unsupported database URL: {DATABASE_URL}")


# Add connection pool logging
@event.listens_for(engine, "connect")
def receive_connect(dbapi_conn, connection_record):
    logger.debug("Database connection established")


@event.listens_for(engine, "checkout")
def receive_checkout(dbapi_conn, connection_record, connection_proxy):
    logger.debug("Connection checked out from pool")


# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
    expire_on_commit=False  # Prevent lazy loading issues after commit
)

# Create base class for models
Base = declarative_base()

# Bug fix (found via a real pytest run: running any single test in
# tests/application/test_subscription_service.py in isolation raised
# `sqlalchemy.exc.InvalidRequestError: ... failed to locate a name
# ('APIKey')` at mapper configuration). Every model module is imported
# here, right after Base exists, so that SQLAlchemy's declarative class
# registry always has every model registered before any mapper gets
# configured -- regardless of which module happens to import
# `core.infrastructure.database` first, which test runs first, or
# whether a test is run in isolation. Without this, whether string-based
# `relationship("APIKey")` references (organization.py, user.py) resolve
# correctly was silently dependent on unrelated import order. Placed
# after `Base = declarative_base()` (not at the top of the file) because
# every model module does `from core.infrastructure.database import
# Base`, which would otherwise be a circular import; by this point Base
# already exists on this (partially-initialized) module.
import core.domain.models  # noqa: F401,E402  (import-for-side-effect: model registration)


def init_db():
    """Initialize the database by creating all tables"""
    try:
        logger.info("Initializing database schema")
        Base.metadata.create_all(bind=engine)
        logger.info("Database schema initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)
        raise


def get_db():
    """
    Dependency to get database session with proper error handling
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}", exc_info=True)
        db.rollback()
        raise
    finally:
        db.close()
