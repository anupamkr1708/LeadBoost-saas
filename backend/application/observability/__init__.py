"""
Observability: operational persistence for the Application layer.

Distinct from application.memory (business memory agents reason over) and
application.evaluation (deterministic scoring logic) -- this module owns
*platform* operational data: pipeline execution records, persisted
evaluation reports, and prompt-version execution history. It exists to
support the production-polish requirements (metrics, analytics API)
without touching Core or changing any existing evaluation/decision logic.

Reuses the existing `core.infrastructure.database` engine/Base/session
machinery -- no new database, no new ORM, no caching layer.
"""
