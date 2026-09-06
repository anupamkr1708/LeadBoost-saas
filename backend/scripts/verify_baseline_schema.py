"""Preflight schema verification for adopting an existing database into
Alembic (P1.1 safety requirement).

`alembic stamp <baseline>` records "this database is already at this
revision" WITHOUT running any DDL. Run it against a database whose schema
doesn't actually match the baseline, and Alembic and reality silently
disagree from that point on -- the repository looks migrated while the
real database is at a different, unknown state. This script is the gate
that prevents that: it inspects the ACTUAL live database and compares it,
table by table and column by column, against the current SQLAlchemy
`Base.metadata` (== the schema the P1.1 baseline revision creates from
scratch). See alembic/versions/<baseline>.py's docstring for the two
adoption paths (fresh vs. existing database) this feeds into.

DO NOT run `alembic stamp <baseline>` against ANY existing database
without first running this script against it and confirming it exits 0.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db python scripts/verify_baseline_schema.py

Exit code 0 -> schema matches; safe to `alembic stamp <baseline>`.
Exit code 1 -> mismatch found; DO NOT STAMP. See docs/DEPLOYMENT.md's
               "Existing DB adoption" section for what to do instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Same sys.path shim used by the other backend/scripts/*.py entrypoints
# (see scripts/test_pipeline.py) -- makes `python scripts/verify_baseline_schema.py`
# and `python -m scripts.verify_baseline_schema` both work from `backend/`.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

# Explicit imports -- these must exactly mirror alembic/env.py's imports so
# this script and Alembic itself always agree on what "the current schema"
# means. See alembic/env.py's docstring for why BOTH lines are required:
# application.observability.models sits outside the core.domain.models
# registry and is not picked up by importing core.infrastructure.database
# alone.
import core.infrastructure.database as db  # noqa: E402
import application.observability.models  # noqa: E402,F401


# Named, safety-critical objects the P1.1 brief calls out explicitly. This
# is a deliberately redundant "at minimum" list on top of the fully generic
# table/column diff below (verify() checks every table and every column
# regardless of this list) -- so a failure here always names the exact
# historical thing (a P0 column, the leads uniqueness guard) that's missing,
# rather than leaving a reader to infer it from a generic column diff.
_REQUIRED_UNIQUE_CONSTRAINTS: dict[str, set[str]] = {
    "leads": {"uq_leads_org_website"},
    "active_pipeline_locks": {"uq_active_pipeline_lock_lead_id"},
}

_REQUIRED_COLUMNS: dict[str, set[str]] = {
    # backend/migrations/001_p0_durable_execution.sql, section 1: durable jobs
    "jobs": {
        "id", "organization_id", "lead_id", "job_type", "status",
        "pipeline_id", "attempt_count", "max_attempts", "available_at",
        "claimed_at", "lease_expires_at", "worker_id", "started_at",
        "completed_at", "last_error", "last_error_category",
        "created_at", "updated_at",
    },
    # 001_p0_durable_execution.sql, section 3: stage execution correlation
    "scraping_logs": {"pipeline_id", "organization_id"},
    "lead_enrichment_logs": {"pipeline_id", "organization_id", "success"},
    # 001_p0_durable_execution.sql, section 4: AI provenance
    "ai_decision_logs": {"pipeline_id", "source", "evaluation_version"},
    # 001_p0_durable_execution.sql, section 5: pipeline execution state
    "pipeline_execution_logs": {"error_message"},
    # 001_p0_durable_execution.sql, section 6: evaluation provenance
    "evaluation_report_logs": {"evaluation_version"},
    # 001_p0_durable_execution.sql, section 7: prompt/model provenance
    "prompt_execution_logs": {"model"},
}


def verify(engine: Engine) -> list[str]:
    """Compare the live database at `engine` against the current
    Base.metadata. Returns a list of human-readable problems; an empty
    list means the schema matches and it is safe to `alembic stamp` this
    database at the baseline revision.
    """
    problems: list[str] = []
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())
    expected_tables = set(db.Base.metadata.tables.keys())

    missing_tables = expected_tables - live_tables
    for t in sorted(missing_tables):
        problems.append(f"missing table: {t}")

    # Named P0 / AI-provenance columns (see _REQUIRED_COLUMNS above).
    for table, cols in _REQUIRED_COLUMNS.items():
        if table in missing_tables:
            continue  # already reported as a missing table
        live_cols = {c["name"] for c in inspector.get_columns(table)}
        for col in sorted(cols - live_cols):
            problems.append(f"missing column: {table}.{col}")

    # Named unique constraints (leads org+website guard, one-lock-per-lead).
    for table, names in _REQUIRED_UNIQUE_CONSTRAINTS.items():
        if table in missing_tables:
            continue
        live_uqs = {uq["name"] for uq in inspector.get_unique_constraints(table)}
        for name in sorted(names - live_uqs):
            problems.append(f"missing unique constraint: {name} on {table}")

    # Generic completeness check: every column the ORM defines, for every
    # table, must actually exist -- not just the named subset above. This
    # is what catches drift the named list didn't anticipate.
    for table_name, table in db.Base.metadata.tables.items():
        if table_name in missing_tables:
            continue
        live_cols = {c["name"] for c in inspector.get_columns(table_name)}
        expected_cols = {c.name for c in table.columns}
        for col in sorted(expected_cols - live_cols):
            msg = f"missing column: {table_name}.{col}"
            if msg not in problems:  # avoid duplicating the named check above
                problems.append(msg)

        # Primary key sanity check -- a table that exists but with the
        # wrong (or no) primary key is unsafe to treat as "matching".
        expected_pk = {c.name for c in table.primary_key.columns}
        live_pk = set(inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
        if expected_pk and expected_pk != live_pk:
            problems.append(
                f"primary key mismatch on {table_name}: "
                f"expected {sorted(expected_pk)}, found {sorted(live_pk)}"
            )

    return problems


def main() -> int:
    problems = verify(db.engine)

    if problems:
        print("SCHEMA VERIFICATION FAILED -- DO NOT STAMP THE BASELINE.")
        print()
        print("This database does not match the expected P1.1 baseline schema:")
        for p in problems:
            print(f"  - {p}")
        print()
        print("Do not run `alembic stamp <baseline>` against this database.")
        print("Bring it to the expected schema through an explicitly reviewed")
        print("migration/procedure first, or discard it and initialize a fresh")
        print("database instead (`alembic upgrade head` on an empty database).")
        return 1

    print("Schema verification passed.")
    print(f"All {len(db.Base.metadata.tables)} expected tables, their required")
    print("columns, and the critical P0/uniqueness constraints are present.")
    print("Safe to run: alembic stamp <baseline-revision-id>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
