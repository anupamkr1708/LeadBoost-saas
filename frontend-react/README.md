# LeadBoost — Frontend

A premium, customer-facing frontend for **LeadBoost**, an AI lead intelligence platform. Built directly against the
supplied OpenAPI spec (`LeadBoost SaaS API v2.0.0`) — every screen, form, and data table is wired to a real endpoint;
nothing is mocked except the illustrative copy on the public landing page.

## Stack

Next.js 14 (App Router) · TypeScript (strict) · Tailwind CSS · TanStack Query · TanStack Table · Framer Motion ·
React Hook Form + Zod · Recharts · Zustand · Axios · Radix UI primitives (styled as a custom "glass" design system,
in the spirit of shadcn/ui) · `cmdk` command palette.

## Getting started

```bash
npm install
cp .env.example .env.local   # set NEXT_PUBLIC_API_URL to your backend
npm run dev
```

Open http://localhost:3000. The public marketing site is served at `/`; `/login` and `/register` connect to
`POST /api/v2/login` and `POST /api/v2/register`. Everything under the authenticated app shell (`/dashboard`,
`/leads`, `/discovery`, …) requires a session.

`npm run build` produces a production build. `npm run typecheck` runs `tsc --noEmit`. Both were run against this
codebase before delivery (see "Verified" below) — the only external dependency is Google Fonts at build time, which
your environment will reach even though the sandbox this was built in could not.

## Architecture

```
src/
  app/                     Next.js App Router routes
    (auth)/                login, register, forgot-password — centered auth layout
    (app)/                 dashboard, discovery, leads, pipeline, outreach,
                           analytics, organization, billing, profile, settings
                           — sidebar + topbar shell, guarded by AuthGuard
    page.tsx               public landing page
  components/
    ui/                    design-system primitives (Button, Card, Dialog, Sheet, Table
                           chrome, Select, Tabs, Tooltip, Dropdown, etc.)
    layout/                Sidebar, Topbar, MobileNav, CommandPalette, AuthGuard
    landing/                marketing sections
    dashboard/, discovery/, leads/, pipeline/, outreach/, analytics/, billing/,
    organization/           feature-specific presentational components
    shared/                 cross-feature UI: ambient background, empty/error states,
                            confirm dialog, status badges, score ring, animated counter
  features/<domain>/       api.ts (typed axios calls) + hooks.ts (TanStack Query)
                           per OpenAPI tag: auth, leads, discovery, organizations,
                           billing, analytics
  lib/                     api-client (axios + refresh interceptor), utils, constants,
                           zod validation schemas
  store/                   zustand auth store (persisted to localStorage)
  types/api.ts             types generated from the OpenAPI components.schemas block
```

## Auth model

The API issues bearer tokens via `POST /api/v2/login` (OAuth2 password grant, form-encoded) and refreshes them via
`POST /api/v2/refresh?refresh_token=...`. Tokens are kept in a persisted Zustand store (localStorage), **not**
cookies — so route protection happens client-side in `components/layout/auth-guard.tsx` rather than in Next.js
middleware, which can't read localStorage. `lib/api-client.ts` attaches the bearer token to every request and
silently retries once on a 401 after refreshing.

## What maps to which endpoint

| Screen | Endpoints |
|---|---|
| Login / Register | `POST /api/v2/login`, `POST /api/v2/register`, `GET/PUT /api/v2/me` |
| Dashboard | `GET /api/v2/leads/`, `GET /api/v2/usage`, `GET /api/v2/analytics/discovery-metrics`, `GET /api/v2/analytics/pipeline-metrics` |
| Lead Discovery | `POST /api/v2/discovery/search` |
| Leads | `GET/POST /api/v2/leads/`, `POST /api/v2/leads/single`, `GET/PUT/DELETE /api/v2/leads/{id}`, `POST /api/v2/leads/{id}/process` |
| Pipeline | `GET /api/v2/analytics/pipeline-metrics` (+ reprocess via `POST /api/v2/leads/{id}/process`) |
| Outreach | Reads/writes `outreach_message` via `GET/PUT /api/v2/leads/{id}` |
| Analytics | All three `GET /api/v2/analytics/*` endpoints |
| Organization | `GET/POST /api/v2/organizations/`, `PUT /api/v2/organizations/{id}` |
| Billing | `GET /api/v2/usage`, `GET /api/v2/plans`, `POST /api/v2/upgrade`, `POST /api/v2/cancel` |
| Profile | `GET/PUT /api/v2/me` |

## Deliberately out of scope (no backend support yet)

The brief said not to invent backend functionality, so a few brief items are implemented as clearly-labeled
placeholders rather than fake API calls:

- **Forgot password** — the API has no reset-password endpoint. The page collects the request and confirms receipt
  client-side; wire it to a real endpoint once one exists.
- **Team members** (Organization page) — no membership endpoints exist yet; shown as a "coming soon" panel.
- **Outreach "regenerate"** — no endpoint regenerates a draft; the page supports editing and saving the existing
  `outreach_message` instead, plus copy-to-clipboard and a `mailto:` handoff.
- **Password change** (Profile → Security) — no self-serve endpoint; shown as a placeholder pointing to support.
- **Pipeline "queue"** — the API has no per-run history/queue endpoint, only aggregated metrics
  (`/analytics/pipeline-metrics`). The Pipeline page shows those aggregates plus a manual reprocess panel instead of
  a fabricated live queue.
- **`/api/v2/plans`** has no fixed response schema in the spec — the billing page treats each entry defensively via
  a loosely-typed `PlanOption` shape.

## Verified before delivery

- `npx tsc --noEmit` — clean.
- `npx eslint src --ext .ts,.tsx --quiet` — clean.
- `npx next build` — all 17 routes compile and prerender successfully (verified with fonts temporarily stubbed,
  since this sandbox can't reach `fonts.googleapis.com`; your environment will fetch them normally on first build).

## Design system

Dark-first, glass-morphism surfaces over an animated ambient gradient background (violet / fuchsia / indigo, plus a
faint amber glow), with a masked grid texture. Space Grotesk for display headings, Inter for body text, JetBrains
Mono for scores/timestamps/data. See `tailwind.config.ts` and `src/app/globals.css` for the full token system.
