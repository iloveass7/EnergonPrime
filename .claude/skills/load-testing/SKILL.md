---
name: load-testing
description: Load, stress, and soak testing rules for the fuel platform under realistic and fault conditions. Use when measuring throughput/latency, capacity planning, or validating behavior under load.
---

# Load Testing

Prove the system holds latency/error budgets under load, and stays sane when load coincides with faults.

## Tools
- **k6** (scriptable JS) or **Locust** (Python) for HTTP + SSE load. Pick one and standardize.

## Scenarios
- **Baseline**: steady RPS on read endpoints; record p50/p95/p99 latency and error rate.
- **Decision loop**: sustained allocation submissions at operational tick rate.
- **Spike/stress**: ramp to find the knee; confirm graceful degradation, not collapse.
- **Soak**: run long enough to expose leaks/connection exhaustion.
- **Chaos-under-load**: inject `/admin/faults` mid-test; assert circuit breakers open, fallbacks engage, and
  recovery is clean after `faults/clear`.

## Rules
- Define explicit SLOs first (e.g. read p95 < X ms, allocation success rate > Y% under nominal load).
- Load-test against a dedicated simulator instance; never a shared/graded one.
- Publish results to `docs/` with the config used, so runs are reproducible and comparable.
- Feed findings back into pool sizes, timeouts, cache TTLs, and breaker thresholds.
