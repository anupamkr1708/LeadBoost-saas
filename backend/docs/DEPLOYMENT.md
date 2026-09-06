# Deployment Guide

The target deployment shape described for this project is:

- **Frontend → Vercel**
- **Backend → Render**

Neither of those requires Docker Compose, Nginx, or Kubernetes -- both
platforms build and run your service directly. The Docker/Compose
artifacts in this repo (`Dockerfile`, `docker-compose.yml`,
`docker-compose.prod.yml`, `deploy/nginx.conf`) exist for local
development and as an *optional* self-hosted/VPS path if you ever need
one -- see the bottom of this document for that case.

## Backend on Render

1. **Create a new Web Service**, pointing at this repository. Render
   detects the `Dockerfile` automatically (Build Command / Start Command
   fields can be left blank -- the Dockerfile's own `CMD` handles it).

2. **Instance type**: Render's Starter tier (~512MB RAM) is the target
   this backend was tuned for -- see SECTION 3 of the production-polish
   brief and `docs/DOCKER.md`'s note on Playwright's memory footprint.
   Start there; move up only if you see actual memory pressure in
   Render's metrics.

3. **Environment variables** (Render's dashboard → Environment): set
   every variable listed as "required" in `docs/ENVIRONMENT.md`, at
   minimum:
   - `ENVIRONMENT=production`
   - `SECRET_KEY` — a real random value. **The app will refuse to start
     in production with the placeholder value or anything under 32
     characters** (see `core/config.py`) -- this is intentional, not a
     bug to work around.
   - `DATABASE_URL` — use Render's managed Postgres and paste its
     internal connection string here (must start with `postgresql://`;
     the app also refuses to start on sqlite in production).
   - `ALLOWED_ORIGINS` — your actual Vercel frontend URL(s), comma
     separated. Never `*` in production (also enforced at startup).
   - `GROQ_API_KEY`, `SERPER_API_KEY` — optional but recommended; AI
     enrichment/qualification/outreach and the website-resolution
     fallback both degrade to deterministic behavior without them, they
     don't error.

4. **Health checks**: point Render's health check at `/live` (no
   dependencies checked, always fast) rather than `/health` (checks
   DB/Redis, appropriate for your own monitoring but not for a load
   balancer deciding whether to route traffic to this instance at all).

5. **Redis**: only used today as a legacy Celery broker health-check
   target (`/health`'s Redis check) -- the active lead pipeline is
   LangGraph-based, not Celery-based, and doesn't require Redis to
   function. If you don't run a Redis instance, `/health` will report
   Redis as unhealthy but the API itself works fine; either provision a
   small managed Redis or ignore that one health-check field.

## Frontend on Vercel

This repository doesn't include frontend source, so specifics depend on
your frontend framework -- generically:

1. Set the build's API base URL environment variable (e.g. `VITE_API_URL`
   or `NEXT_PUBLIC_API_URL`, depending on your framework) to your Render
   backend's public URL.
2. Make sure that exact frontend origin is in the backend's
   `ALLOWED_ORIGINS`.

## Database migrations

Alembic (`backend/alembic/`) is the canonical mechanism for all *future*
schema changes. `init_db()` (`main.py`'s `lifespan`, calling
`Base.metadata.create_all()`) still runs on every startup, but as of P1.1
its role is narrower: it's a convenience for tests and fresh local/dev
databases with nothing in them yet, not the production schema-evolution
mechanism. `create_all()` only ever adds missing tables -- it never alters
an existing table (add a column, add a constraint) -- so once a database
has any real data in it, Alembic is the only thing that can safely move
its schema forward.

**Historical note:** `backend/migrations/001_p0_durable_execution.sql` is
the hand-written SQL that took a pre-P0 database to the P0 schema. It is
preserved as-is, for history -- it is not run again, and it is not a
second migration system running alongside Alembic. See the note added to
its own header. Do not add a `002_*.sql` file next to it; use Alembic.

### Everyday workflow (developer machine)

1. Change an ORM model in `core/domain/models/` (or, less commonly,
   `application/observability/models.py`).
2. Generate a revision:
   ```
   cd backend
   alembic revision --autogenerate -m "describe the change"
   ```
3. **Open the generated file in `alembic/versions/` and read it.** Never
   commit autogenerate output unread. Look specifically for anything you
   didn't expect: a `drop_table`/`drop_column` you didn't intend, a type
   change on an unrelated column, a duplicate index. Autogenerate is a
   diffing tool, not a decision-maker.
4. Apply it locally: `alembic upgrade head`
5. Run the backend test suite: `python -m pytest`
6. Commit the ORM change and the migration file together, in the same PR.

Useful commands while working on a migration:

| Command | Shows |
|---|---|
| `alembic current` | the revision your local database is actually at |
| `alembic heads` | should always print exactly one revision |
| `alembic history` | the full chain of revisions, oldest to newest |
| `alembic check` | whether the database still matches the ORM models (no pending changes) |

### Existing-database adoption -- read this before ever running `alembic stamp`

`alembic stamp <revision>` records "this database is already at
`<revision>`" **without running any DDL at all**. That's exactly what you
want when adopting a database that a prior process (`create_all()`, or
historically, `001_p0_durable_execution.sql`) already built to match the
current schema -- running the baseline revision's `CREATE TABLE`
statements against it would just fail on "already exists" errors. But
stamping a database whose schema *doesn't* actually match is worse than
doing nothing: Alembic will believe the migration history and the real
database agree from then on, when they don't, and nothing will
surface that until something breaks in production.

**So: never run `alembic stamp` against a database you haven't verified.**
The required procedure is:

```
cd backend
DATABASE_URL=<the database you're adopting> python scripts/verify_baseline_schema.py
```

This script reflects the live database and compares it, table by table
and column by column, against the current ORM models -- including named
checks for `uq_leads_org_website` and every P0 column from
`001_p0_durable_execution.sql`. It exits `0` only if everything matches.

- **Exit code 0** ("Schema verification passed"): safe to run
  `alembic stamp eaa40e596fcc` (the baseline revision ID), then
  `alembic upgrade head` (a no-op, since you're already at head).
- **Exit code 1**: **do not stamp.** The script prints exactly what's
  missing or mismatched. Bring the database to the expected schema
  through an explicitly reviewed migration/procedure, or -- for a
  database you don't actually need to keep -- discard it and start a
  fresh one with a normal `alembic upgrade head` instead.

**There is currently no verified, live production database this has been
run against.** If/when a real production database needs to be adopted,
run `verify_baseline_schema.py` against it first, exactly as above, before
ever stamping it -- and back it up first regardless of what the script
says.

### Testing migrations

`tests/infrastructure/test_alembic_metadata.py` needs no live database
(pure metadata inspection) and runs as part of the normal suite.
`tests/infrastructure/test_alembic_postgres_migrations.py` actually runs
`alembic upgrade`/`stamp`/`check` against real, disposable PostgreSQL
databases it creates and drops itself -- it's skipped by default and only
runs when `ALEMBIC_TEST_DATABASE_URL` is set to a Postgres server you're
comfortable letting it create/drop databases on:

```
docker-compose up -d postgres   # already defined in backend/docker-compose.yml
export ALEMBIC_TEST_DATABASE_URL=postgresql://leadboost:leadboost@localhost:5432/postgres
python -m pytest tests/infrastructure/
```

### Production

Run `alembic upgrade head` as a **separate deploy step that happens once
per deploy**, before the new application code starts serving traffic --
never inside FastAPI's `lifespan`/startup. On the current single-Render-
Web-Service setup, that's one instance either way, but the app should
never assume that: `alembic upgrade head` racing against itself from
multiple starting replicas is exactly the failure mode a release-step
migration avoids, and there's no reason to give it up.

Render supports this natively via **Pre-Deploy Command** (Dashboard →
your service → Settings → "Pre-Deploy Command"; generally available,
works for Docker-image services, runs once before the new deploy's start
command). Set it to:

```
cd backend && alembic upgrade head
```

This repository doesn't currently define a `render.yaml` blueprint (the
Render service is configured directly via the dashboard, as described
above in "Backend on Render") -- if one is introduced later, the
equivalent is the `preDeployCommand` key. Either way, do not add
`alembic upgrade head` to the Dockerfile's `CMD` or to `main.py`'s
`lifespan`: both run once per replica, which is exactly what a release-
step migration is meant to avoid.

For the self-hosted/VPS `docker-compose.prod.yml` path (below), run
`alembic upgrade head` manually (or via a one-shot deploy script) before
restarting the `backend` service -- there's no separate release-step
hook in a plain Compose setup.

## Billing

Stripe is intentionally **not** wired up to actually process payments yet
(see `core/infrastructure/billing/stripe_service.py`, which is a ready,
unused scaffold, and PART 8 / SECTION 10 of the brief). The `/upgrade`
endpoint always returns "Online payments coming soon" without changing
anyone's plan. Manually moving an organization to Pro/Enterprise today
means calling `SubscriptionService.assign_plan_to_organization` directly
-- e.g. from a one-off script or a shell into the running container --
which is deliberately not exposed over the API to any regular user.

## Optional: self-hosted / VPS deployment

If you'd rather run everything on a single VPS instead of Vercel+Render:

```bash
cp .env.example .env   # fill in real values
docker compose -f docker-compose.prod.yml up -d
```

This brings up the backend, Postgres, Redis, MinIO, pgAdmin, Prometheus,
and Grafana (see `docs/DOCKER.md` and `docs/MONITORING.md`). Put
`deploy/nginx.conf` (with a real domain and TLS certs) in front of it for
TLS termination and basic edge rate-limiting -- see the comments in that
file.
