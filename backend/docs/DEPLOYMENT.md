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

`init_db()` (called at startup, see `main.py`'s `lifespan`) creates any
missing tables via SQLAlchemy's `Base.metadata.create_all()` -- there is
no separate migration tool (e.g. Alembic) wired up in this codebase.
For a first deploy this is sufficient; if you need to evolve the schema
later on a live database with existing data, evaluate adding Alembic at
that point (marked as an optional future improvement -- introducing a
migration framework now, with no schema changes currently pending, would
be exactly the kind of unnecessary abstraction the brief asks to avoid).

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
