---
name: backend-architecture
description: Backend structure and API-design rules for the fuel platform (FastAPI modular monolith, Pydantic, Postgres/Redis, async). Use when creating modules, endpoints, service boundaries, or data models.
---

# Backend Architecture

**Modular monolith** with clean dependency boundaries. One deployable process; an optional separate
ML/decision worker only if profiling shows it's needed.

## Layers (dependencies point inward)
- `integration/` — resilient simulator client (see `simulator-integration`, `resilience-engineering`).
- `domain/` — entities, value objects, invariants. No framework/IO imports here.
- `services/` — forecasting, detection, state engine, decision/optimization orchestration.
- `data/` — repositories over Postgres (durable + audit) and Redis (cache/hot state). Only layer touching DBs.
- `api/` — FastAPI routers, request/response Pydantic models, dependency injection. Thin; no business logic.

## Rules
- **Pydantic v2** for all boundaries (API in/out, simulator models, config via `BaseSettings`).
- **async** end to end; use `httpx.AsyncClient`, async DB drivers. Never block the event loop.
- Postgres: migrations (Alembic), sensible indexes, explicit transactions for multi-write ops.
- Redis: cache with TTL, degraded-mode last-known-good snapshots, hot state for the decision loop.
- Follow **SOLID**; inject dependencies (client, repos) so they can be faked in tests.
- Record an **ADR** (`docs/adr/NNNN-*.md`) for any decision that constrains future work.
- Pull exact API signatures from **Context7** rather than guessing.

## Anti-patterns to reject
Business logic in routers · DB calls outside `data/` · simulator calls outside `integration/` ·
God modules · premature microservices/message-bus.
