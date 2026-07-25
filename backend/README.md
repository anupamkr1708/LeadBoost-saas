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
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Pipeline execution lifecycle, observability, explainability |
| [`docs/DISCOVERY.md`](docs/DISCOVERY.md) | The discovery pipeline in depth: query parsing, search, website resolution/validation, ranking |
| [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) | Deploying to Render (backend) + Vercel (frontend), plus the optional self-hosted/VPS path |
| [`docs/DOCKER.md`](docs/DOCKER.md) | Dockerfile, docker-compose (dev and prod), image layout |
| [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md) | Every environment variable: required vs. optional, defaults, what degrades if unset |
| [`docs/MONITORING.md`](docs/MONITORING.md) | Prometheus metrics, the bundled Grafana dashboard, what each panel means |

## Testing

```bash
pip install pytest pytest-asyncio
pytest tests/ --ignore=tests/scraper
```

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
`docs/ARCHITECTURE.md` and `docs/DISCOVERY.md` for how each piece works.
