# BUP Fuel Supply Operations Platform — Project Context

Intelligent decision-support platform operating against the official **BUP Fuel Supply Simulator**
(BUP CSE Fest 2026 Hackathon). Engineering loop:

`Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover`

---

## Source of truth (overrides everything below on conflict)

Precedence: docs/simulator-guide.md > docs/pipeline.md > docs/architect.md > docs/prd.md > defaults below.
pipeline.md §0.3 and §0.4 REPLACE the older forecast/LP/DB design in architect.md and prd.md.
If docs conflict, stop and ask. Current phase: docs/PROGRESS.md.
Never load architect.md or prd.md whole. Grep headings; read only the sections the phase references.

## Overrides

- DB: Neon via SQLAlchemy + asyncpg + Alembic. No local Postgres. Pooled URL for api/worker, direct URL for migrations.
- Processes: api (xN) + worker (x1, leader lock).
- Forecast: structural profile is champion. No Prophet/statsmodels/LightGBM by default.
- Planner: marginal-benefit greedy is P0. OR-Tools LP is Phase 9, only if it beats greedy in replay.
- Layout: fuelops/ per architect §2.2. import-linter enforces it; intelligence.validate never imports intelligence.allocation.
- /v1 writes = POST allocations + cancel only. /admin only in the dual-gated test adapter.
- Report only measured numbers. Label all quantities as simulated.

---

## Context hierarchy (read top-down; pull lower layers only when the task needs them)

1. **GLOBAL** — your Claude Code global rules + installed skills/MCPs.
2. **PROJECT** — this file. Goal, stack, guardrails, workflow.
3. **ARCHITECTURE** — `docs/ARCHITECTURE.md` + ADRs in `docs/adr/`. The source of truth for structure.
4. **TASK** — the plan for the current change (`docs/plans/`). Written before non-trivial work.
5. **CODE** — the files being changed. Navigate with Serena, not by re-reading whole trees.
6. **LIBRARY DOCS** — pull with **Context7** MCP on demand (FastAPI, Pydantic, OR-Tools, React, etc.). Do not guess API signatures.

Rule against context pollution: keep long-lived facts (decisions, APIs, constraints) in files 2–4;
keep transient detail out of them. Retrieve, don't memorize.

---

## Architecture (target — a modular monolith, not microservices)

```
BUP Simulator (REST + SSE)
        │  Integration Layer  (resilient client: retry/backoff, circuit breaker, cache)
        ▼
Data / State Layer  (PostgreSQL = durable history/audit · Redis = hot state/cache)
        │
   ┌────┴─────────────┬──────────────┐
Forecasting        Detection      State Engine
   └────┬─────────────┴──────────────┘
        ▼  Decision Intelligence → Optimization/Policy → Recommendation (explainable)
        ▼
   Backend API (FastAPI)  →  Operator UI (React + SSE)
        │
   Observability across everything (structured logs, metrics, traces, health)
```

**Deliberately NOT used** (unless a concrete need appears): Kubernetes, Kafka, Terraform, service mesh,
microservices, extra agents. A modular monolith is the ceiling of complexity for a hackathon.

## Stack (default — confirm before deviating; see Overrides above for precedence)
- Backend: **Python 3.12+, FastAPI, Pydantic v2, httpx, async**.
- Data: **Neon PostgreSQL** (durable + audit; see Overrides), **Redis 7** (cache/coordination/hot state).
- Intelligence: NumPy, SciPy (HiGHS LP — Phase 9 only); structural profile forecast; no statsmodels/Prophet/LightGBM by default.
- Frontend: **React + TypeScript + Vite**, Tailwind, TanStack Query, Recharts, SSE client.
- Infra: **Docker + docker-compose**, GitHub Actions CI.
- Test/load: **pytest, Hypothesis, Playwright, k6**.

---

## Development workflow (behave like a senior team — do NOT jump straight to code)

For any non-trivial change:
1. Understand the requirement. 2. Inspect existing code. 3. Pull relevant docs (Context7).
4. Write/update the plan + note risks. 5. Implement. 6. Test. 7. Self-review.
8. Integration test. 9. Verify behavior. 10. Update docs. 11. Commit a coherent change.

- **Architecture changes** → plan first (use Superpowers `brainstorming` / `writing-plans`).
- **Bug fixes** → reproduce and understand before editing (Superpowers `systematic-debugging`).
- **ML changes** → `data → baseline → experiment → metric → comparison → decision`.
- **Optimization changes** → `objective → constraints → baseline → optimize → evaluate`.

Project skills live in `.claude/skills/`; invoke the matching one for the task at hand.

## Hard rules
- REST is the source of truth; SSE is advisory. Never assume a POST /allocations succeeded without reading the response.
- Every simulator call goes through the resilient integration client (never call httpx directly from business logic).
- Determinism: the simulator is deterministic — tests replay recorded sequences; no hidden randomness in decisions without a fixed seed.
- Recommendations must be explainable (inputs, constraints, chosen action, why).
- Secrets via environment/`.env` (gitignored), never committed.
