# Environment Variables

See `.env.example` for a ready-to-copy template with every variable
below. This document explains *why* each one matters and what happens if
it's left unset.

## Required (app refuses to start without these in production)

Enforced at startup by `core/config.py`'s `validate_startup_environment`
-- see `docs/DEPLOYMENT.md` for the exact errors this produces.

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Signs JWTs. **Must not** be the `.env.example` placeholder, and must be at least 32 characters, when `ENVIRONMENT=production`. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `DATABASE_URL` | Must be a `postgresql://` URL in production (sqlite is for local development only — both `core/infrastructure/database` and `core/config.py` enforce this). |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins. Must not contain `*` in production. |

In development (`ENVIRONMENT` unset or `development`), none of the above
are hard-enforced -- sensible insecure defaults are used so local setup
has minimal friction.

## Recommended (features degrade gracefully, don't break, if unset)

| Variable | Powers | If unset |
|---|---|---|
| `GROQ_API_KEY` | LLM calls for enrichment, qualification, decisioning, outreach drafting | Those stages fall back to their existing deterministic/heuristic paths (see `docs/ARCHITECTURE.md`) — the pipeline still runs, just without AI. |
| `SERPER_API_KEY` | Website-resolution fallback (when a business has no website in the primary search provider's data) and the general "primary search found nothing" discovery fallback | Businesses without a directly-provided website are reported unvalidated (`no_website`) instead of resolved; the discovery fallback simply doesn't fire. |
| `BRAVE_API_KEY` | Alternate website-resolution fallback provider (constructor-injectable; Serper is the default) | N/A unless you specifically wire `BraveWebsiteResolver` in as `resolver_fallback`. |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | Future billing integration | Not used at all yet — see `core/infrastructure/billing/stripe_service.py` and PART 8/SECTION 10 of the brief. The `/upgrade` endpoint doesn't need these; it never calls Stripe. |

## Database / cache

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | *(required)* | See above. |
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | Only used today as a `/health` check target — see `docs/DEPLOYMENT.md`'s note that the active pipeline is LangGraph-based, not Celery-based. |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/0` | Same as above. |

## Security / auth

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | *(required in prod)* | See above. |
| `ALGORITHM` | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | |
| `ALLOWED_ORIGINS` | *(required in prod)* | CORS allowlist, comma-separated. |

## Application

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | Set to `production` to enable the startup validation described above. |
| `LOG_LEVEL` | `INFO` | |
| `FRONTEND_URL` | `http://localhost:5173` | |
| `SENDER_ORG` | `Our Company` | **Fallback only.** Outreach now personalizes to each organization's own name (its profile in the database) first; this is used only for organizations that haven't set one. |
| `LLM_MODEL` | `llama-3.3-70b-versatile` | Groq model used by the LangGraph pipeline's LLM-backed stages. |
| `REVIEW_AUTO_APPROVE_THRESHOLD` | `0.75` | |
| `REVIEW_HUMAN_REVIEW_THRESHOLD` | `0.45` | |

## Scraper

| Variable | Default | Notes |
|---|---|---|
| `SCRAPER_MAX_CONCURRENT_PAGES` | `4` | |
| `RESPECT_ROBOTS_TXT` | `false` | |

## Discovery

| Variable | Default | Notes |
|---|---|---|
| `OVERPASS_API_URL` | `https://overpass-api.de/api/interpreter` | Primary business-search provider (OpenStreetMap). Point at a self-hosted mirror for higher volume. |
| `SERPER_API_KEY` | *(unset)* | See "Recommended" above. |
| `BRAVE_API_KEY` | *(unset)* | See "Recommended" above. |
| `DISCOVERY_MAX_CONCURRENT_PIPELINES` | `3` | How many businesses' `LeadPipeline.execute()` runs happen concurrently per search request. |
| `DISCOVERY_MAX_CONCURRENT_RESOLUTIONS` | `5` | How many candidates' website resolution/validation happen concurrently per search request. Kept modest by design — see SECTION 3 of the production-polish brief on predictable memory usage over raw speed. |

## Billing / plan limits

Real payment processing isn't wired up (see "Recommended" above), but
usage-limit enforcement is fully active and independent of that.

| Variable | Default | Notes |
|---|---|---|
| `DEFAULT_PLAN` | `free` | Assigned to every newly-registered organization. |
| `FREE_MAX_LEADS_PER_DAY` | `50` | |
| `PRO_MAX_LEADS_PER_DAY` | `500` | |
| `ENTERPRISE_MAX_LEADS_PER_DAY` | `10000` | |
| `CAN_USE_AI_FREE` / `CAN_USE_AI_PRO` / `CAN_USE_AI_ENTERPRISE` | `false` / `true` / `true` | Gates the LLM-backed pipeline stages per plan. |
| `CAN_EXPORT_FREE` / `CAN_EXPORT_PRO` / `CAN_EXPORT_ENTERPRISE` | all `false` | Gates data export (if/when that feature is added). |

A canceled subscription is treated as effectively "free" for all of the
above (see `SubscriptionService._effective_plan_name`) — `plan_name`
itself is left alone on cancellation for billing-history purposes, but
limits and feature flags immediately drop to the free tier's.
