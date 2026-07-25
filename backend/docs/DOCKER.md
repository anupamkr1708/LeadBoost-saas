# Docker Guide

## The backend image (`Dockerfile`)

Two-stage build:

1. **`builder`** — installs build tooling and Python dependencies into a
   venv at `/opt/venv`. Nothing from this stage ends up in the final
   image except that venv.
2. **`runtime`** (the default target) — a slim Python image with just the
   venv, the runtime system libraries `psycopg2-binary` and Playwright's
   Chromium actually need, and the app source, running as a non-root
   user.

```bash
docker build -t leadboost-backend .
docker run -p 8000:8000 --env-file .env leadboost-backend
```

### Why the image includes a full Chromium install

The existing scraper (`core/infrastructure/scraping/scraper.py`) already
uses Playwright's Chromium for its higher-fidelity fetch tiers -- that's
existing, working behavior this pass doesn't change or remove. Chromium
itself is a genuine, unavoidable weight cost (the single largest
contributor to this image's size). If memory becomes tight in
production, the option worth evaluating (not implemented here, since it
would be a real architectural change, not a polish) is splitting scraping
out into its own worker process/service so the main API process doesn't
need to hold a browser runtime in memory at all times.

### Why `--workers 1`

Each additional Uvicorn worker is a full second copy of the process --
including, if the scraper's browser tiers are exercised, its own
Chromium instance. On a memory-constrained target (Render's Starter
tier, ~512MB — see SECTION 3 of the production-polish brief) that
tradeoff isn't worth it: this API's workload is overwhelmingly I/O-bound
(HTTP calls to Overpass/Serper/Groq, database queries), which a single
process's async event loop already handles concurrently without needing
multiple OS processes. Scale by moving to a larger instance type first if
you hit CPU-bound limits, not by adding workers.

## Development compose (`docker-compose.yml`)

```bash
docker compose up
```

Starts just `backend` + `postgres` + `redis` (nothing else -- pgAdmin,
MinIO, Prometheus, and Grafana aren't needed to iterate on a feature day
to day). The backend service mounts the source tree and runs with
`--reload`, so code changes are picked up immediately without rebuilding
the image.

## Production-shaped compose (`docker-compose.prod.yml`)

For the optional self-hosted/VPS path (see `docs/DEPLOYMENT.md`) --
brings up the full stack:

```bash
cp .env.example .env   # fill in real secrets first
docker compose -f docker-compose.prod.yml up -d
```

| Service | Purpose | Exposed on host |
|---|---|---|
| `backend` | The API | `:8000` |
| `postgres` | Primary database | not exposed (internal network only) |
| `redis` | Legacy Celery-broker health-check target | not exposed |
| `minio` | S3-compatible object storage | `:9000` (API), `:9001` (console) |
| `pgadmin` | Postgres admin UI | `:5050` |
| `prometheus` | Metrics collection | `:9090` |
| `grafana` | Dashboards (pre-provisioned, see `docs/MONITORING.md`) | `:3001` |

`postgres` and `redis` are deliberately **not** published to the host at
all in this file (only reachable from other containers on the
`leadboost-internal` network) -- if you need `psql` access from the host
directly, use `docker compose -f docker-compose.prod.yml exec postgres
psql -U leadboost`, or use pgAdmin.

**MinIO is included as ready-to-use infrastructure, not because any
current code path uses it** -- there's no file-upload feature in this
backend yet. It's here so adding one later doesn't also require adding
infrastructure at the same time.

## `.dockerignore`

Keeps `tests/`, docs, `.env*`, local sqlite databases, and Docker's own
config files out of the build context entirely -- smaller build context,
faster builds, and no chance of an `.env` file accidentally ending up
baked into an image layer.
