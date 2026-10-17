# LeadBoost Documentation

Start with the [root README](../../README.md) for the product overview. This index groups the engineering
documents; every link is relative to this directory.

## Architecture and design

| Document | What it covers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Canonical, short architecture overview: components, boundaries, ownership, active workers |
| [`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) | End-to-end design, failure model and trade-offs |
| [`ARCHITECTURE_FULL.md`](ARCHITECTURE_FULL.md) | Detailed, code-grounded backend reference (class level) |
| [`diagrams/`](diagrams/) | The five SVG diagrams (system context, lifecycle, deployment topology, state machines, end-to-end pipeline) and the generator that produces them (`diagrams/src/`) |

## Discovery and AI evaluation

| Document | What it covers |
|---|---|
| [`DISCOVERY.md`](DISCOVERY.md) | Discovery layer: stages, providers, website resolution, failure recovery |
| [`../discovery_eval/README.md`](../discovery_eval/README.md) | Benchmark harness, metrics and outputs |
| [`OBSERVABILITY_AND_METRICS.md`](OBSERVABILITY_AND_METRICS.md) | Pipeline instrumentation, metrics, explainability |

## Mailer integration

| Document | What it covers |
|---|---|
| [`MAILER_INTEGRATION.md`](MAILER_INTEGRATION.md) | The LeadBoost ⇄ Mailer contract from the product's perspective |
| [`../core/infrastructure/mailing_agent/CONTRACT.md`](../core/infrastructure/mailing_agent/CONTRACT.md) | Canonical LeadBoost-side wire contract |
| [Mailer L1 mailbox contract](https://github.com/anupamkr1708/LeadBoost-mail-agent/blob/main/docs/LEADBOOST_L1_MAILBOX_CONTRACT.md) | Canonical Mailer-side mailbox contract |
| [Mailer C13 deployment runbook](https://github.com/anupamkr1708/LeadBoost-mail-agent/blob/main/docs/C13_DEPLOYMENT_RUNBOOK.md) | Mailer deployment and operations |

## Setup and environment

| Document | What it covers |
|---|---|
| [`ENVIRONMENT.md`](ENVIRONMENT.md) | Every LeadBoost environment variable (single source of truth) |
| [`DOCKER.md`](DOCKER.md) | Docker and Compose usage |
| [`../README.md`](../README.md) | Backend quickstart |

## Deployment and migrations

| Document | What it covers |
|---|---|
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | Intended topology, per-service configuration, Alembic migrations, release and rollback |

## Monitoring and troubleshooting

| Document | What it covers |
|---|---|
| [`MONITORING.md`](MONITORING.md) | Health endpoints, metrics, troubleshooting |

## Testing

Tests and CI are described in the [root README](../../README.md#evaluation-and-testing) and
[`SYSTEM_DESIGN.md`](SYSTEM_DESIGN.md) §15. The workflow is [`../../.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
