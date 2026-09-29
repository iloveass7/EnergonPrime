---
name: simulator-integration
description: Rules for integrating with the BUP Fuel Supply Simulator (REST + SSE). Use whenever writing or reviewing code that calls the simulator, handles its events, submits allocations, or deals with injected faults.
---

# Simulator Integration

The simulator (`/v1/*` REST, `/v1/stream` SSE, `/admin/*` controls) is deterministic and injects faults
(latency, unavailable API, error rate, stale data, stream disconnect).

## Rules
- **REST is the source of truth. SSE is advisory.** After any material SSE event, refetch the affected REST resource before acting.
- All access goes through one **integration client** (see `resilience-engineering`). Business logic never calls httpx directly.
- **Never assume `POST /v1/allocations` succeeded** — read the response body/status. Handle **409** (allocation conflict/rejected) explicitly; do not retry a 409 blindly.
- Treat **503** as an injected fault → backoff + degraded mode, not a crash.
- Respect **stale-data** headers/flags; mark data as stale in state and surface it to the operator, don't silently serve it as fresh.
- Handle **stream disconnects**: reconnect with backoff + jitter, and on reconnect do a full REST resync of depots/stations/routes/allocations.
- Use **idempotency keys** on writes so retries can't double-allocate.
- **Validate relationships** before submitting: route ↔ depot ↔ station must be consistent with `/v1/routes`, `/v1/depots`, `/v1/stations`.
- Model every endpoint response with **Pydantic** models; reject/log unexpected shapes rather than propagating `dict`s.

## Endpoint map
Read: instance, depots, stations, routes, supply-arrivals, events, allocations, demand-history, metrics.
Write: `POST /v1/allocations`. Admin (test harness only): run/pause/toggle/step/reset/events/faults/faults-clear.

## Definition of done
Client has typed models, retry/backoff, circuit breaker, cache, idempotency, SSE reconnect+resync, and
explicit 409/503/stale handling — each covered by a fault-injection test.
