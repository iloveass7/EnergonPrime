# ADR-0001: Modular monolith + Python/FastAPI stack

## Context
Hackathon-scoped decision-support platform against a single deterministic simulator. Needs strong API
integration, resilience, forecasting, optimization, and an operator UI — built fast, by a small team,
without operational sprawl.

## Decision
- **Modular monolith** (FastAPI, one process) with clean internal boundaries; optional single
  ML/decision worker only if profiling shows the decision loop starves the API.
- Stack: Python 3.12+/FastAPI/Pydantic v2/httpx async; PostgreSQL (durable + audit) + Redis (cache/hot state);
  scikit-learn/statsmodels + OR-Tools; React+TS+Vite; Docker Compose + GitHub Actions.

## Status
Accepted (initial).

## Consequences
- Simple to run, test, and demo; boundaries keep it from becoming a big ball of mud and allow later extraction.
- Single deploy unit; scale vertically for the hackathon.

## Alternatives considered
- **Microservices / Kafka / Kubernetes**: rejected — operational overhead and latency far exceed the value at this scale.
- **Notebook-only ML**: rejected — no resilience, serving, or audit story.
