"""
Application Layer
==================

This package sits above the existing, stable `core` layer (domain +
infrastructure) and orchestrates it into an intelligent, explainable,
AI-assisted lead-processing workflow.

It does not replace, rewrite, or duplicate anything in `core`. Every agent
and workflow node in this package delegates real work (scraping, enrichment,
scoring, messaging, persistence, subscription checks) to the existing,
production-tested infrastructure via thin adapters in
`application.services.infra_adapters`.

Layout
------
agents/          Single-responsibility AI agents (reasoning)
workflows/        LangGraph-based orchestration (control flow)
prompts/          Versioned, reusable prompt templates + registry
context/          Builds the combined context an agent reasons over
memory/           Lightweight business memory (past analyses/decisions)
evaluation/       Confidence/completeness/grounding/consistency scoring
explainability/   Standard "reasoning + evidence + confidence" contract
dto/              Pydantic data-transfer objects exchanged between stages
interfaces/       Ports (Protocols) agents depend on, not concrete infra
services/         LLM provider + infra adapters (the only place that talks
                   to `core`)
state/            LangGraph state schema + typed context bundles
exceptions/       Application-layer error types
utils/            Cross-cutting helpers (stage logging/timing)
"""
