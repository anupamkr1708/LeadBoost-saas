"""Alembic environment for LeadBoost SaaS.

Two things here are deliberate and load-bearing, not boilerplate:

1. DATABASE_URL / engine reuse
   -------------------------------------------------------------------
   This module imports `core.infrastructure.database` and migrates using
   *that exact* `engine` object -- the same one the running application
   uses, built from the same DATABASE_URL, loaded via the same
   `load_dotenv()` / `os.getenv("DATABASE_URL")` convention. There is no
   second, parallel DATABASE_URL system here, no separate connection
   config, and alembic.ini intentionally contains no `sqlalchemy.url` --
   see that file's header comment. This also means the existing
   production/SQLite guardrails in `core/infrastructure/database/__init__.py`
   (e.g. "production requires PostgreSQL") apply automatically to Alembic
   runs too, since importing that module re-runs those checks.

2. target_metadata completeness
   -------------------------------------------------------------------
   `core.infrastructure.database` imports `core.domain.models`, which is
   this codebase's central model registry (see that module's own
   docstring) -- 14 tables. But `application/observability/models.py`
   defines 4 more tables (pipeline_execution_logs, evaluation_report_logs,
   prompt_execution_logs, discovery_run_logs) that reuse the same `Base`
   *without* going through that registry. Today those 4 tables only ever
   get registered because `main.py` imports the analytics endpoint (which
   imports application.observability.metrics_service -> ...repository ->
   ...models) before `init_db()` runs -- an import-order accident, not a
   guarantee. If Alembic relied on that same accident, target_metadata
   would silently be missing those 4 tables, and `alembic revision
   --autogenerate` would see them as "in the database but not in
   metadata" and propose DROPPING them (see docs/DEPLOYMENT.md and the
   P1.1 baseline revision's docstring for the fuller writeup, and
   tests/infrastructure/test_alembic_metadata.py, which fails if this
   import is ever removed).

   So both are imported explicitly, here, regardless of import order
   anywhere else in the app.
"""

from logging.config import fileConfig

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# --- Reuse the application's own database configuration -------------------
# Importing this raises a clear error if DATABASE_URL is unset, and enforces
# the same production-must-be-PostgreSQL rule the app itself enforces.
import core.infrastructure.database as _database  # noqa: E402

# --- Ensure the COMPLETE model set is registered before target_metadata is
# --- read (see module docstring above). Do not remove either import.
import core.domain.models  # noqa: E402,F401 (already imported by the line
# above via core.infrastructure.database, re-imported here to make the
# dependency explicit rather than implicit/transitive)
import application.observability.models  # noqa: E402,F401 (NOT part of the
# core.domain.models registry -- see module docstring above)

target_metadata = _database.Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`alembic upgrade
    head --sql`). Kept working for cases where reviewing raw SQL before
    ever touching a real database is useful, per P1.1 section 13 -- the
    primary supported mechanism is still `run_migrations_online` below."""
    context.configure(
        url=_database.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the application's real engine."""
    with _database.engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type changes too, not just add/drop -- helps
            # `alembic check` / autogenerate catch drift precisely rather
            # than only at the table/column-existence level.
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
