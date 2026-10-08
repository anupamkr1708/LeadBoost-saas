"""
Cross-service E2E fixtures.

The LeadBoost test environment (throwaway SQLite DATABASE_URL, test Fernet key, no AI key,
job worker off, ...) is defined once, in tests/application/conftest.py. Importing it here runs
those `os.environ.setdefault(...)` calls and the `sys.path` insert, so the two suites can never
drift apart -- nothing is duplicated.
"""

from tests.application import (
    conftest as _application_test_environment,
)  # noqa: F401  (imported for its side effects)
