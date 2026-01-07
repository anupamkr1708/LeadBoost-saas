"""
Database initialization module
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL", "sqlite:///./leadboost_local.db"  # Use SQLite for local development
)

# Create engine
if DATABASE_URL.startswith("sqlite"):
    # SQLite doesn't support pool_pre_ping and other advanced features
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},  # Required for SQLite
        echo=os.getenv("DB_ECHO", "false").lower()
        == "true",  # Enable SQL logging in development
    )
else:
    # PostgreSQL configuration
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # Verify connections before use
        pool_recycle=300,  # Recycle connections after 5 minutes
        echo=os.getenv("DB_ECHO", "false").lower()
        == "true",  # Enable SQL logging in development
    )

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()


def init_db():
    """Initialize the database by creating all tables"""
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
