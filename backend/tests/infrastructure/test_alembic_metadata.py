"""Tests for Alembic's model-metadata completeness.

These run without a live database connection (pure Python-side metadata
inspection), so they run in the ordinary SQLite-backed test job -- they do
not require PostgreSQL. See test_alembic_postgres_migrations.py for the
tests that actually run `alembic upgrade`/`stamp`/`check` against real
PostgreSQL databases.

The central thing under test: alembic/env.py's target_metadata must see
ALL 19 current ORM tables, not just the 15 registered through
core.domain.models. application/observability/models.py defines 4 more
tables using the same Base, but outside that central registry -- today
those 4 only get registered because main.py happens to import the
analytics router (which transitively imports them) before anything reads
Base.metadata. If someone later "cleans up" that import in main.py, or
removes the explicit import from alembic/env.py, autogenerate would stop
seeing those 4 tables and would propose DROPPING them next time someone
runs `alembic revision --autogenerate`. That failure mode is exactly what
this file guards against.
"""

from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_PY = _BACKEND_ROOT / "alembic" / "env.py"
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"

# The complete, current table set. Intentionally spelled out (not derived
# from Base.metadata) so this test fails loudly -- rather than trivially
# matching whatever Base.metadata happens to contain -- if a table is ever
# silently dropped from the registry or from application/observability.
_CORE_REGISTRY_TABLES = {
    "organizations", "plans", "daily_lead_quota_usage", "invoices",
    "subscriptions", "usage_records", "users", "api_keys", "leads",
    "active_pipeline_locks", "ai_decision_logs", "jobs",
    "lead_enrichment_logs", "scraping_logs",
    # P1.2: organization-scoped qualification policy (see
    # core/domain/models/qualification_settings.py). Registered through
    # core.domain.models like every other table in this set.
    "organization_qualification_settings",
}
_OBSERVABILITY_TABLES = {
    "pipeline_execution_logs", "evaluation_report_logs",
    "prompt_execution_logs", "discovery_run_logs",
}
_ALL_EXPECTED_TABLES = _CORE_REGISTRY_TABLES | _OBSERVABILITY_TABLES


def _import_target_metadata():
    """Reproduces exactly what alembic/env.py does to build target_metadata,
    without going through Alembic's Config/context machinery (which wants a
    live DB connection). If this drifts from env.py's actual imports, the
    test_env_py_imports_both_registries test below will catch it.
    """
    import core.infrastructure.database as database
    import application.observability.models  # noqa: F401

    return database.Base.metadata


def test_core_registry_is_structurally_separate_from_observability_models():
    """Documents the actual hazard, statically (not via runtime import
    order, which can't be trusted in a shared pytest process once any
    other test has already imported application.observability.models):
    core/domain/models/__init__.py -- the codebase's documented central
    model registry -- does not itself import
    application.observability.models. The two are separate registries
    that both happen to share the same declarative Base. This is exactly
    why alembic/env.py (and verify_baseline_schema.py) must import both
    explicitly rather than relying on the core registry alone.
    """
    registry_init = _BACKEND_ROOT / "core" / "domain" / "models" / "__init__.py"
    source = registry_init.read_text()
    assert "observability" not in source, (
        "core/domain/models/__init__.py now references "
        "application.observability.models -- if the two registries were "
        "merged, this is good news, but alembic/env.py's docstring and "
        "verify_baseline_schema.py's comments describing them as separate "
        "should be updated to match."
    )


def test_target_metadata_contains_all_19_tables():
    """The actual regression test: with BOTH imports env.py performs, every
    currently-known table must be present in target_metadata."""
    metadata = _import_target_metadata()
    tables = set(metadata.tables.keys())

    missing = _ALL_EXPECTED_TABLES - tables
    assert not missing, f"target_metadata is missing expected tables: {sorted(missing)}"

    unexpected = tables - _ALL_EXPECTED_TABLES
    assert not unexpected, (
        f"target_metadata has tables not accounted for in this test's expected "
        f"set -- update _ALL_EXPECTED_TABLES if this is an intentional new "
        f"table: {sorted(unexpected)}"
    )

    assert len(tables) == 19


def test_observability_tables_specifically_visible():
    """Names the exact 4 tables the hazard is about, so a failure here
    points straight at the cause instead of a generic count mismatch."""
    metadata = _import_target_metadata()
    tables = set(metadata.tables.keys())
    for table in _OBSERVABILITY_TABLES:
        assert table in tables, (
            f"{table} is missing from target_metadata -- if "
            f"application.observability.models was removed from "
            f"alembic/env.py's imports, autogenerate will think this table "
            f"needs to be DROPPED. Restore that import."
        )


def test_leads_unique_constraint_present_in_metadata():
    """uq_leads_org_website is the org+website uniqueness guard added
    outside the P0 SQL migration -- confirm it's part of target_metadata
    (and therefore part of the P1.1 baseline revision) rather than
    something only enforced by create_all() historically."""
    metadata = _import_target_metadata()
    leads = metadata.tables["leads"]
    constraint_names = {
        c.name for c in leads.constraints if c.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_leads_org_website" in constraint_names


def test_env_py_imports_both_registries():
    """Structural guard on alembic/env.py itself: fails if either import
    is ever removed, independent of whatever Base.metadata happens to
    contain when this test runs (so it can't be fooled by import ordering
    from an earlier test in the same session)."""
    source = _ENV_PY.read_text()
    tree = ast.parse(source, filename=str(_ENV_PY))

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)

    assert "core.infrastructure.database" in imported_modules
    assert "core.domain.models" in imported_modules
    assert "application.observability.models" in imported_modules


def test_alembic_ini_has_no_hardcoded_database_url():
    """alembic.ini must never carry a real (or even placeholder) connection
    string -- env.py is solely responsible for resolving DATABASE_URL from
    the environment, the same way core/infrastructure/database does."""
    text = _ALEMBIC_INI.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.lower().startswith("sqlalchemy.url"), (
            "alembic.ini must not set sqlalchemy.url -- DATABASE_URL is "
            "resolved by alembic/env.py at runtime instead."
        )


def test_single_migration_head():
    """There must be exactly one revision file with down_revision = None
    (one root) and, transitively, one head. For P1.1 there is exactly one
    revision total (the baseline), so this also just confirms that."""
    versions_dir = _BACKEND_ROOT / "alembic" / "versions"
    revision_files = sorted(versions_dir.glob("*.py"))
    assert len(revision_files) >= 1, "no Alembic revisions found"

    roots = 0
    for f in revision_files:
        source = f.read_text()
        tree = ast.parse(source, filename=str(f))
        for node in ast.walk(tree):
            # Generated revisions use annotated assignment:
            # down_revision: Union[str, Sequence[str], None] = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id == "down_revision":
                    value = node.value
                    if isinstance(value, ast.Constant) and value.value is None:
                        roots += 1
            # Also handle plain assignment, in case a future revision
            # (or a different Alembic template) doesn't use annotations.
            elif isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if "down_revision" in targets:
                    value = node.value
                    if isinstance(value, ast.Constant) and value.value is None:
                        roots += 1

    assert roots == 1, (
        f"expected exactly one revision with down_revision = None (one root), "
        f"found {roots} -- multiple roots means multiple independent migration "
        f"histories, which `alembic heads` would report as multiple heads."
    )
