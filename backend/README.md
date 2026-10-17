# LeadBoost — AI Lead Discovery & Outreach SaaS (Backend)

LeadBoost discovers real businesses for a natural-language query
("dentists in Bangalore", "AI automation startups in Noida"), resolves
and validates each one's genuine official website, runs them through a
lead-qualification and outreach-drafting pipeline, and tracks it all per
organization with usage-based plan limits.

This repository is the backend: FastAPI + SQLAlchemy + a deterministic
discovery pipeline + a LangGraph-based lead pipeline.

## Quickstart (local development)

```bash
cp .env.example .env
# edit .env: at minimum set SECRET_KEY to something random for local use.
# GROQ_API_KEY / SERPER_API_KEY are optional -- their absence degrades AI
# features and website-resolution fallback gracefully rather than breaking.

docker compose up
```

The API is then live at `http://localhost:8000`, with interactive docs at
`http://localhost:8000/docs`.

Without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium   # only needed for the scraper's browser-based tier

export DATABASE_URL="sqlite:///./dev.db"   # or a real postgresql:// URL
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"

uvicorn main:app --reload
```

## Documentation

| Guide | Covers |
|---|---|
| [`docs/README.md`](docs/README.md) | Index of every document below |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Canonical architecture overview: components, boundaries, ownership |
| [`docs/SYSTEM_DESIGN.md`](docs/SYSTEM_DESIGN.md) | End-to-end design, failure model, trade-offs |
| [`docs/MAILER_INTEGRATION.md`](docs/MAILER_INTEGRATION.md) | The LeadBoost ⇄ Mailer Agent contract |
| [`docs/DISCOVERY.md`](docs/DISCOVERY.md) | The discovery pipeline in depth |
| [`docs/OBSERVABILITY_AND_METRICS.md`](docs/OBSERVABILITY_AND_METRICS.md) | Pipeline execution lifecycle, observability, explainability |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Intended topology, configuration, migrations, release and rollback |
| [`docs/DOCKER.md`](docs/DOCKER.md) | Dockerfile, docker-compose (dev and prod), image layout |
| [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) | Every environment variable: required vs. optional, defaults, what degrades if unset |
| [`docs/MONITORING.md`](docs/MONITORING.md) | Prometheus metrics, the bundled Grafana dashboard, what each panel means |

## Testing

```bash
pip install pytest pytest-asyncio
export DATABASE_URL="sqlite:///./ci_test.db" ENVIRONMENT=development
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
pytest tests/ discovery_eval/tests/ --ignore=tests/scraper
```

This is the test selection and environment of the CI job "Backend unit tests" (`.github/workflows/ci.yml`),
which additionally installs Chromium with `playwright install --with-deps chromium`. Cross-service tests in
`tests/e2e/` skip themselves unless `MAILER_REPO_PATH` points at a Mailer checkout; CI runs them in a separate job
with the Mailer, Dovecot and a disposable PostgreSQL database.

(`tests/scraper/` contains standalone verification scripts meant to be
run directly with `python`, not collected by pytest -- see that
directory's own comments.)

## Project status

Authentication, organizations, billing (usage-limit enforcement; Stripe
checkout itself is intentionally not wired up yet -- see
`core/infrastructure/billing/stripe_service.py` and
`docs/ENVIRONMENT.md`), discovery, website resolution/validation,
duplicate detection, lead qualification, outreach generation, and
analytics are all implemented and covered by the test suite. See
`docs/ARCHITECTURE.md`, `docs/SYSTEM_DESIGN.md` and `docs/DISCOVERY.md` for how each piece works.
