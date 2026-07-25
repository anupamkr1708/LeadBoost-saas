"""
Tests for core.config.validate_startup_environment (SECTION 9 of the
production-polish brief: "validate required variables, helpful startup
errors").
"""

import pytest

from core.config import validate_startup_environment


def test_development_environment_never_raises_even_with_defaults(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    validate_startup_environment()  # must not raise


def test_production_with_placeholder_secret_key_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "your-super-secret-key-change-in-production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_startup_environment()


def test_production_with_short_secret_key_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "too-short")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")

    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        validate_startup_environment()


def test_production_with_sqlite_database_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a" * 40)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        validate_startup_environment()


def test_production_with_wildcard_cors_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS"):
        validate_startup_environment()


def test_production_with_proper_config_starts_cleanly(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "a" * 40)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host/db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")

    validate_startup_environment()  # must not raise


def test_all_problems_reported_together_not_one_at_a_time(monkeypatch):
    """Aggregated errors save a fix-rerun-fail cycle for every single
    misconfigured variable."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "your-super-secret-key-change-in-production")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./test.db")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError) as exc_info:
        validate_startup_environment()

    message = str(exc_info.value)
    assert "SECRET_KEY" in message
    assert "DATABASE_URL" in message
    assert "ALLOWED_ORIGINS" in message
