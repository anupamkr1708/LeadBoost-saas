"""
Shared fixtures for application-layer tests.

Uses a throwaway file-based SQLite database (never Postgres) so these
tests run anywhere with no external services, matching the existing
codebase's use of `core.infrastructure.database`.
"""

import os
import sys
import uuid

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///./test_application_{uuid.uuid4().hex}.db")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "")  # force deterministic/heuristic agent paths
os.environ.setdefault("CAN_USE_AI_FREE", "true")
os.environ.setdefault("CAN_USE_AI_PRO", "true")
os.environ.setdefault("CAN_USE_AI_ENTERPRISE", "true")
# P0-A (durable job execution): the background poll loop is disabled by
# default for the whole test suite -- tests exercise job claim/lease/
# execute/reclaim logic directly and deterministically (see
# tests/application/test_durable_jobs.py) rather than racing a live
# timer-driven loop against test assertions on a shared test database.
os.environ.setdefault("JOB_WORKER_ENABLED", "false")
# Neutral by default so no test's outcome silently depends on whatever
# ENABLE_AI_FOR_ALL_PLANS happens to be in the developer's local .env
# (core.infrastructure.database.__init__ calls load_dotenv() the first
# time anything imports it, which happens lazily from inside fixtures --
# after this module's own setdefault calls, so this wins as long as it's
# not already exported in the shell before pytest starts; the one test
# that specifically needs this pinned regardless of the shell too also
# monkeypatches it directly -- see test_subscription_service.py).
os.environ.setdefault("ENABLE_AI_FOR_ALL_PLANS", "false")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def db_engine():
    from core.infrastructure.database import Base, engine

    Base.metadata.create_all(bind=engine)
    yield engine


@pytest.fixture()
def db_session(db_engine):
    from core.infrastructure.database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def sample_org(db_session):
    from core.domain.models.organization import Organization

    org = Organization(name="Test Org")
    db_session.add(org)
    db_session.commit()
    db_session.refresh(org)
    return org


@pytest.fixture()
def sample_user(db_session, sample_org):
    from core.domain.models.user import User
    from core.infrastructure.auth.security import get_password_hash

    user = User(
        email=f"user_{uuid.uuid4().hex[:8]}@test.com",
        hashed_password=get_password_hash("x"),
        organization_id=sample_org.id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def sample_lead(db_session, sample_org, sample_user):
    from core.domain.models.lead import Lead

    lead = Lead(
        organization_id=sample_org.id,
        owner_id=sample_user.id,
        website="https://example.com",
        company_name="Example Co",
        industry="Software",
        about_text="Example Co builds developer tools.",
        email="hello@example.com",
    )
    db_session.add(lead)
    db_session.commit()
    db_session.refresh(lead)
    return lead
