"""Tests that actually run `alembic upgrade`/`stamp`/`check` against real,
disposable PostgreSQL databases.

These are intentionally separate from test_alembic_metadata.py (which needs
no live database) and are SKIPPED unless ALEMBIC_TEST_DATABASE_URL is set
to a PostgreSQL server the test runner is allowed to create/drop databases
on -- e.g. `postgresql://user:pass@localhost:5432/postgres`. This must
never point at a real development or production database: every test here
creates a brand-new, uniquely-named database and drops it afterward.

CI sets ALEMBIC_TEST_DATABASE_URL against the Postgres service container
added for this purpose (see .github/workflows/ci.yml's `migrations` job).
Locally, `docker-compose up -d postgres` (already in backend/docker-compose.yml)
then exporting the URL is enough -- see docs/DEPLOYMENT.md's "Testing
migrations" section.

Each test invokes the real `alembic` CLI via subprocess (not the Python API
in-process) specifically to avoid Python module-import caching across
tests: core.infrastructure.database binds an `engine` at import time from
whatever DATABASE_URL was set when it was first imported anywhere in the
pytest session, and that binding cannot be un-done by changing the env var
later in-process. A subprocess gets a clean interpreter and therefore a
clean, correct DATABASE_URL every time -- this is also exactly how a real
developer or a CI/deployment step actually runs these commands, so it is
the more faithful test besides.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ADMIN_URL = os.environ.get("ALEMBIC_TEST_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not _ADMIN_URL,
    reason=(
        "Set ALEMBIC_TEST_DATABASE_URL to a disposable PostgreSQL server "
        "(e.g. postgresql://leadboost:leadboost@localhost:5432/postgres) "
        "to run the Alembic/PostgreSQL migration tests. Skipped by default "
        "so the rest of the suite continues to run on SQLite alone -- see "
        "docs/DEPLOYMENT.md's 'Testing migrations' section."
    ),
)

_BASELINE_REVISION = "eaa40e596fcc"

_ALL_TABLES_AFTER_P1_2 = {
    "organizations", "plans", "daily_lead_quota_usage", "invoices",
    "subscriptions", "usage_records", "users", "api_keys", "leads",
    "active_pipeline_locks", "ai_decision_logs", "jobs",
    "lead_enrichment_logs", "scraping_logs", "pipeline_execution_logs",
    "evaluation_report_logs", "prompt_execution_logs", "discovery_run_logs",
    # P1.2 (see core/domain/models/qualification_settings.py)
    "organization_qualification_settings",
}

_P1_2_REVISION = "61258798a87a"


def _database_url_with_name(admin_url: str, db_name: str) -> str:
    """Swap the database name in a URL like postgresql://u:p@host:port/anything."""
    base = admin_url.rsplit("/", 1)[0]
    return f"{base}/{db_name}"


@pytest.fixture()
def disposable_db():
    """Creates a brand-new, uniquely-named PostgreSQL database for one
    test and drops it afterward, regardless of test outcome."""
    admin_engine = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
    db_name = f"p11_pytest_{uuid.uuid4().hex[:12]}"
    with admin_engine.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    db_url = _database_url_with_name(_ADMIN_URL, db_name)
    try:
        yield db_url
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin_engine.dispose()


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SECRET_KEY": "alembic-test-secret-not-used-for-anything-real",
        "ENVIRONMENT": "development",
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _run_verify_script(database_url: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SECRET_KEY": "alembic-test-secret-not-used-for-anything-real",
        "ENVIRONMENT": "development",
    }
    return subprocess.run(
        [sys.executable, "scripts/verify_baseline_schema.py"],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _create_all_via_orm(database_url: str) -> None:
    """Builds the schema the way the app has always built it (create_all),
    to stand in for 'a database that already exists today' without any
    Alembic involvement -- exactly the adoption scenario P1.1 must handle
    safely."""
    script = (
        "import core.infrastructure.database as db\n"
        "import application.observability.models\n"
        "db.Base.metadata.create_all(bind=db.engine)\n"
    )
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "SECRET_KEY": "alembic-test-secret-not-used-for-anything-real",
        "ENVIRONMENT": "development",
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(_BACKEND_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


class TestFreshPostgresUpgrade:
    def test_upgrade_head_creates_all_current_tables(self, disposable_db):
        result = _run_alembic("upgrade", "head", database_url=disposable_db)
        assert result.returncode == 0, result.stderr

        engine = create_engine(disposable_db)
        inspector = inspect(engine)
        live_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert live_tables == _ALL_TABLES_AFTER_P1_2

    def test_upgrade_head_is_idempotent(self, disposable_db):
        first = _run_alembic("upgrade", "head", database_url=disposable_db)
        assert first.returncode == 0, first.stderr

        second = _run_alembic("upgrade", "head", database_url=disposable_db)
        assert second.returncode == 0, second.stderr
        # No new operations the second time -- nothing in stdout/stderr
        # about running an upgrade step.
        assert "Running upgrade" not in second.stderr

    def test_current_heads_history(self, disposable_db):
        _run_alembic("upgrade", "head", database_url=disposable_db)

        current = _run_alembic("current", database_url=disposable_db)
        assert _P1_2_REVISION in current.stdout

        heads = _run_alembic("heads", database_url=disposable_db)
        assert _P1_2_REVISION in heads.stdout
        assert heads.stdout.strip().count("\n") == 0  # exactly one head line

    def test_alembic_check_clean_after_fresh_upgrade(self, disposable_db):
        _run_alembic("upgrade", "head", database_url=disposable_db)
        result = _run_alembic("check", database_url=disposable_db)
        assert result.returncode == 0, result.stderr
        assert "No new upgrade operations detected" in result.stdout

    def test_leads_unique_constraint_created(self, disposable_db):
        _run_alembic("upgrade", "head", database_url=disposable_db)
        engine = create_engine(disposable_db)
        inspector = inspect(engine)
        names = {uq["name"] for uq in inspector.get_unique_constraints("leads")}
        assert "uq_leads_org_website" in names


class TestExistingSchemaAdoption:
    """The scenario the mandatory safety refinement is about: an existing,
    already-populated-by-create_all() database, not an empty one."""

    def test_verify_script_passes_on_matching_schema(self, disposable_db):
        _create_all_via_orm(disposable_db)
        result = _run_verify_script(disposable_db)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Schema verification passed" in result.stdout

    def test_verify_script_fails_on_empty_database(self, disposable_db):
        """An empty database should NOT pass verification -- it should be
        upgraded normally, not stamped."""
        result = _run_verify_script(disposable_db)
        assert result.returncode == 1
        assert "DO NOT STAMP" in result.stdout

    def test_verify_script_fails_on_missing_unique_constraint(self, disposable_db):
        _create_all_via_orm(disposable_db)
        engine = create_engine(disposable_db)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE leads DROP CONSTRAINT uq_leads_org_website"))

        result = _run_verify_script(disposable_db)
        assert result.returncode == 1
        assert "uq_leads_org_website" in result.stdout
        assert "DO NOT STAMP" in result.stdout

    def test_verify_script_fails_on_missing_p0_column(self, disposable_db):
        _create_all_via_orm(disposable_db)
        engine = create_engine(disposable_db)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE ai_decision_logs DROP COLUMN evaluation_version"))

        result = _run_verify_script(disposable_db)
        assert result.returncode == 1
        assert "ai_decision_logs.evaluation_version" in result.stdout

    def test_stamp_after_verification_then_upgrade_is_noop(self, disposable_db):
        _create_all_via_orm(disposable_db)

        verify_result = _run_verify_script(disposable_db)
        assert verify_result.returncode == 0, "precondition: schema must verify clean"

        # verify_baseline_schema.py compares the live DB against whatever
        # db.Base.metadata currently is (see that script's docstring) --
        # which, now that P1.2 exists on top of the P1.1 baseline, is the
        # schema at HEAD, not literally the baseline revision alone. A
        # database built by _create_all_via_orm() (today's models) must
        # therefore be stamped at "head", the revision whose cumulative
        # effect actually matches it -- stamping it at the P1.1 baseline
        # revision here would be asserting something false (that this DB
        # matches a schema one migration behind what it actually has),
        # and the upgrade below would then try to (re)create P1.2's table/
        # columns and fail against objects that already exist.
        stamp_result = _run_alembic("stamp", "head", database_url=disposable_db)
        assert stamp_result.returncode == 0, stamp_result.stderr

        current = _run_alembic("current", database_url=disposable_db)
        assert _P1_2_REVISION in current.stdout

        upgrade_result = _run_alembic("upgrade", "head", database_url=disposable_db)
        assert upgrade_result.returncode == 0
        assert "Running upgrade" not in upgrade_result.stderr  # no-op

        check_result = _run_alembic("check", database_url=disposable_db)
        assert check_result.returncode == 0
        assert "No new upgrade operations detected" in check_result.stdout

    def test_stamping_an_unverified_drifted_database_is_never_attempted(self, disposable_db):
        """This test is the mandatory safety refinement made concrete: it
        proves the documented procedure (verify, then only stamp if
        verify passed) would have refused to proceed on a drifted
        database, by asserting the verify step itself -- the actual gate
        -- fails clearly rather than silently. It deliberately does NOT
        call `alembic stamp` here: per the approved procedure, stamp is
        never reached when verification fails.
        """
        _create_all_via_orm(disposable_db)
        engine = create_engine(disposable_db)
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE leads DROP CONSTRAINT uq_leads_org_website"))

        verify_result = _run_verify_script(disposable_db)
        assert verify_result.returncode != 0, (
            "verification must fail on a drifted database -- if it silently "
            "passed here, the safety gate is broken"
        )


class TestBaselineMigrationSafety:
    def test_baseline_upgrade_body_has_no_drops(self):
        """Static check on the committed baseline revision file itself:
        upgrade() must be additive only. (The runtime tests above prove
        the same thing behaviorally; this pins it at the source level so
        an edit to the file is caught even before anyone runs it.)"""
        versions_dir = _BACKEND_ROOT / "alembic" / "versions"
        baseline_files = [f for f in versions_dir.glob("*.py") if _BASELINE_REVISION in f.name]
        assert len(baseline_files) == 1
        source = baseline_files[0].read_text()

        upgrade_start = source.index("def upgrade()")
        downgrade_start = source.index("def downgrade()")
        upgrade_body = source[upgrade_start:downgrade_start]

        assert "drop_table" not in upgrade_body
        assert "drop_column" not in upgrade_body
        assert "drop_constraint" not in upgrade_body
        assert upgrade_body.count("create_table") == 18

    def test_p1_2_upgrade_body_has_no_drops(self):
        """Same static check as the baseline test above, for the P1.2
        migration: additive-only (one new table, four new columns on
        existing tables), no drops of any kind."""
        versions_dir = _BACKEND_ROOT / "alembic" / "versions"
        p1_2_files = [f for f in versions_dir.glob("*.py") if _P1_2_REVISION in f.name]
        assert len(p1_2_files) == 1
        source = p1_2_files[0].read_text()

        upgrade_start = source.index("def upgrade()")
        downgrade_start = source.index("def downgrade()")
        upgrade_body = source[upgrade_start:downgrade_start]

        assert "drop_table" not in upgrade_body
        assert "drop_column" not in upgrade_body
        assert "drop_constraint" not in upgrade_body
        assert upgrade_body.count("create_table") == 1
        assert upgrade_body.count("add_column") == 4
