# LeadBoost Architecture

This is the canonical architecture overview for **LeadBoost + the Mailer Agent**. It is deliberately short.
For the full end-to-end design and its trade-offs read [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md); for the
cross-service contract read [`MAILER_INTEGRATION.md`](MAILER_INTEGRATION.md).

## 1. System context

LeadBoost is a multi-tenant lead-generation platform. A user describes the businesses they want in plain
language; LeadBoost finds them, verifies who they are, enriches and qualifies them, and lets the user
authorize outreach. A **separate service, the Mailer Agent** (its own repository, deployment and database),
writes, sends and tracks the resulting email conversation.

![System context: browser, Next.js frontend, LeadBoost API with in-process job loop and database, and the separately deployed Mailer API, worker and database, with SMTP, IMAP and LLM dependencies](diagrams/leadboost-system-context.svg)

*Diagram: [`leadboost-system-context.svg`](diagrams/leadboost-system-context.svg). The text below carries the
same information if the image does not render.*

| Component | Responsibility | Runs as |
|---|---|---|
| **Frontend** (`frontend-react/`) | Next.js 14 UI (15 page routes). Calls LeadBoost's `/api/v2` only. Never holds a Mailer URL or key. | Static/Node service; backend URL baked in at build time |
| **LeadBoost API** (`backend/`) | FastAPI app. Authentication, organization scoping, discovery, lead pipeline, outreach authorization, email-account management, mailbox sync, Mailer client. | One process (`uvicorn --workers 1`) |
| **Discovery + identity resolution** | Parse a natural-language query, find candidate businesses, resolve and validate the official website, deduplicate, rank. See [`DISCOVERY.md`](DISCOVERY.md). | In-request, as an inline `DISCOVERY` job |
| **Qualification pipeline** | LangGraph workflow: scrape, enrich, score, decide, draft. Every AI step has a deterministic fallback. | Inline for discovery, or as a durable `LEAD_PIPELINE` job |
| **In-process job loop** | `asyncio` task started from the API's lifespan. Polls the `jobs` table, claims a row atomically with a lease, runs it, reclaims expired leases. `JOB_WORKER_ENABLED` (default `true`) controls it. | Inside the API process |
| **LeadBoost database** | PostgreSQL in production (SQLite for local development). Leads, jobs, email accounts (encrypted credential), outreach actions, organization policy. | Managed database |
| **Mailer API** (other repo) | Tenant-authenticated HTTP surface: mailbox provisioning, outreach-request intake, read-only conversation view. | gunicorn + 2 uvicorn workers, no scheduler |
| **Mailer worker** (other repo) | APScheduler process: message generation, SMTP sending, lease recovery, per-mailbox IMAP polling, reply classification. | Exactly one instance |
| **Mailer database** (other repo) | The Mailer's own PostgreSQL: mailboxes (encrypted credentials), dispatches, contacts, messages. | Managed database |

External dependencies: OpenStreetMap Overpass and Serper (discovery), Groq-hosted LLMs (separate keys for
LeadBoost and the Mailer), the business websites themselves (validation and scraping), and the customer's SMTP
and IMAP server.

### Qualification workflow (LangGraph)

`scrape` → `enrich` → `analyze_company` → `qualification` → `decide` → `confidence_evaluation` → `review_decision`, then
`message_generation` (skipped when the review decision is `human_review`) → `persistence` → `analytics`. The graph is
built in `application/workflows/lead_pipeline.py`. The message it drafts is stored on the lead and used as
LeadBoost's authorization snapshot; the Mailer generates the message that is actually sent.

## 2. Layer boundaries inside LeadBoost

```
api/            HTTP surface: routers, request/response schemas, auth dependencies
application/    use cases: services (outreach, mailbox sync), discovery, workflows, execution (job loop)
core/domain/    models and pure business rules (outreach state vocabulary, scoring)
core/infrastructure/  adapters: database/CRUD, scraping, enrichment, mailing_agent clients, security
```

Dependencies point inward, `api → application → core`, with one known exception: `core/domain/services/scoring.py`
imports a DTO from `application/dto`. The three Mailer HTTP clients
(`client.py`, `mailbox_client.py`, `conversation_client.py` under
`core/infrastructure/mailing_agent/`) are infrastructure adapters; services call them, routers never do.

## 3. Ownership and trust boundaries

* **LeadBoost owns** customer identity and tenancy, leads and their business context, the decision to
  authorize an outreach action, and the user's sender account.
* **The Mailer owns** the generated message text, mailbox execution, the sending credential in use at send
  time, inbound mail, and the authoritative delivery state.
* The services share **no database, ORM model, transaction or encryption key**. They talk over HTTPS only.
* Every cross-service call is authenticated with a **per-organization API key** the Mailer maps to its own
  organization. LeadBoost never sends an organization id; nothing in a request body is used for
  authorization. The Mailer's separate system-operator credential is not used by LeadBoost.
* The browser never talks to the Mailer. It sees a LeadBoost-shaped product view only.

## 4. Active background execution

| Where | Mechanism | Notes |
|---|---|---|
| LeadBoost | One `asyncio` job loop inside the API process, backed by the `jobs` table | Durable: a crash loses no queued work; expired leases are reclaimed. Scaling the API out means running more than one loop; see [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) §17 |
| Mailer | One APScheduler worker process, separate from the API | Single-instance by design. Generation and send poll every 15 s, lease recovery every 60 s, IMAP polling every 120 s (defaults) |

A Celery app and a Redis health check remain in the repository but are **not on the runtime path**: no
application code imports the Celery worker, and no queue is used for pipelines or outreach.

## 5. Data ownership at a glance

| Data | Lives in | Notes |
|---|---|---|
| Leads, qualification output, jobs | LeadBoost DB | Organization-scoped |
| Email account + encrypted credential | LeadBoost DB | Used only for LeadBoost's own SMTP verification |
| `OutreachAction` (authorization snapshot, state, idempotency key) | LeadBoost DB | `submitted` means the Mailer durably accepted it |
| Mailbox + encrypted credential used to send | Mailer DB | The Mailer's own encrypted copy; its key is never shared |
| Generated message, dispatch state, inbound replies, contacts | Mailer DB | Authoritative for delivery state |

## 6. Deployment and verification status

LeadBoost (frontend and backend) and the Mailer (API and worker) are deployed as separate services; the intended
topology and per-service configuration are in [`DEPLOYMENT.md`](DEPLOYMENT.md). The synthetic cross-service
lifecycle test is CI-verified, the local and hosted runs are operator-reported, and hosted message delivery to a
real inbox is not asserted because no acceptance-test record is included. The levels are set out in
[`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) §15.

## 7. Where to go next

| You want | Read |
|---|---|
| Full design, failure model, trade-offs | [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) |
| The LeadBoost ⇄ Mailer contract in product terms | [`MAILER_INTEGRATION.md`](MAILER_INTEGRATION.md) |
| Exact request/response contract (canonical) | [`../core/infrastructure/mailing_agent/CONTRACT.md`](../core/infrastructure/mailing_agent/CONTRACT.md) |
| Discovery and identity resolution internals | [`DISCOVERY.md`](DISCOVERY.md) |
| Class-by-class backend reference | [`ARCHITECTURE_FULL.md`](ARCHITECTURE_FULL.md) |
| Pipeline observability, metrics, explainability | [`OBSERVABILITY_AND_METRICS.md`](OBSERVABILITY_AND_METRICS.md) |
| Environment variables | [`ENVIRONMENT.md`](ENVIRONMENT.md) |
| Deploying and migrating | [`DEPLOYMENT.md`](DEPLOYMENT.md) |
| Monitoring and troubleshooting | [`MONITORING.md`](MONITORING.md) |
| Docker | [`DOCKER.md`](DOCKER.md) |
