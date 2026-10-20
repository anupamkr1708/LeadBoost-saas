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


def test_rejected_mailer_key_map_is_a_value_free_startup_warning_not_a_crash(monkeypatch, caplog):
    """A duplicate/malformed MAILING_AGENT_ORG_API_KEYS already fails closed per call; startup
    surfaces it once, naming only a fixed reason -- never a key, an org id or the JSON."""
    import json
    import logging

    shared = "dup-key-must-never-appear-in-logs"
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", json.dumps({"7": shared, "8": shared}))

    with caplog.at_level(logging.WARNING):
        validate_startup_environment()  # must not raise

    assert "MAILING_AGENT_ORG_API_KEYS is set but rejected (duplicate_key_value)" in caplog.text
    assert shared not in caplog.text


def test_valid_or_unset_mailer_key_map_adds_no_startup_warning(monkeypatch, caplog):
    import json
    import logging

    monkeypatch.setenv("ENVIRONMENT", "development")
    for value in (None, json.dumps({"7": "k7", "8": "k8"})):
        if value is None:
            monkeypatch.delenv("MAILING_AGENT_ORG_API_KEYS", raising=False)
        else:
            monkeypatch.setenv("MAILING_AGENT_ORG_API_KEYS", value)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            validate_startup_environment()
        assert "MAILING_AGENT_ORG_API_KEYS is set but rejected" not in caplog.text
