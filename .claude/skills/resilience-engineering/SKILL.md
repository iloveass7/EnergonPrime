---
name: resilience-engineering
description: Fault-tolerance rules for surviving the simulator's injected faults (latency, unavailable API, error rate, stale data, stream disconnect). Use when building the integration client, fallback/degraded behavior, or chaos tests.
---

# Resilience Engineering

The simulator injects faults on purpose (`/admin/faults`). Surviving them cleanly is a first-class,
graded requirement — not an afterthought.

## Patterns (implement in the integration client)
- **Timeouts** on every call (connect + read); never unbounded.
- **Retry with exponential backoff + jitter** for transient 5xx/timeouts. Cap attempts. **Do not retry 409** or other definitive rejects.
- **Circuit breaker** per dependency: open on sustained failure, half-open probe, close on recovery. Fail fast while open.
- **Bulkheads**: isolate simulator I/O from the decision loop (separate pools/tasks) so a stall can't freeze everything.
- **Graceful degradation / fallback**: serve last-known-good from Redis, switch to baseline forecast/policy,
  and clearly mark state as degraded.
- **Stale-cache handling**: track data age; use stale data only when marked stale, with the operator informed.
- **SSE disconnect**: reconnect (backoff+jitter) then full REST resync.
- **Idempotency** so retries can't double-write.

## Recovery
On dependency recovery: close the breaker, resync REST state, clear degraded flags, log the incident window.

## Test it (see also `testing`)
Every pattern has a test that drives `/admin/faults` to inject the matching fault and asserts:
correct degraded behavior, no crash, no data corruption, and clean recovery after `/admin/faults/clear`.
