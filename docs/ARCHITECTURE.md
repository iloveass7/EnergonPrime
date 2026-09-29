# Architecture — BUP Fuel Supply Operations Platform

Living overview. Update whenever structure changes; record significant decisions as ADRs in `docs/adr/`.

## Style
**Modular monolith** (one FastAPI process) + optional separate ML/decision worker only if profiling demands it.
Not microservices, not Kubernetes, not Kafka — see ADR-0001.

## Components & data flow
```
BUP Simulator (REST source-of-truth + advisory SSE)
   │
integration/   resilient client: timeouts, retry+backoff+jitter, circuit breaker, idempotency,
   │           SSE reconnect+resync, cache + last-known-good
data/          PostgreSQL (durable history + decision audit) · Redis (hot state, cache, degraded snapshots)
   │
services/      forecasting · detection · state engine · decision orchestration
   │           prediction → constraints → optimization (OR-Tools) → explainable recommendation
api/           FastAPI routers (thin) + Pydantic models
   │
frontend/      React + TS operator dashboard, SSE-driven, degraded-aware
observability  structured logs · Prometheus metrics · OTel traces · /health · decision audit (everywhere)
```

## Boundaries (dependencies point inward)
`api → services → domain`; `services → data`, `integration`. `domain/` has no IO/framework imports.
Simulator access only in `integration/`; DB access only in `data/`.

## Key constraints
- REST is truth; SSE advisory → refetch after material events.
- Never assume an allocation succeeded; handle 409/503/stale explicitly.
- Recommendations must be explainable and audited.
- Deterministic simulator → deterministic tests (replay, fixed seeds).

See `.claude/skills/` for per-area rules.
