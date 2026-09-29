---
name: testing
description: Testing strategy for the fuel platform — unit, integration, contract, property-based, deterministic simulation replay, and E2E. Use when writing or reviewing tests, or defining test coverage for a change.
---

# Testing

Deterministic simulator = testable system. Exploit it.

## Layers
- **Unit** (pytest): domain logic, forecasting transforms, optimizer formulation, client parsing. Fast, no network.
- **Contract**: assert simulator responses match Pydantic models; guard against schema drift. Fail loudly on unexpected shapes.
- **Integration**: real integration client against a running simulator (or recorded fixtures). Cover 200/409/503/stale paths.
- **Fault-injection**: drive `/admin/faults` for each fault type; assert degraded behavior + recovery (see `resilience-engineering`).
- **Deterministic replay**: record a simulator run (fixed seed) and replay it; decisions must reproduce exactly.
  Snapshot key decisions and diff on regressions.
- **Property-based** (Hypothesis): allocations never violate capacity/conservation; forecasts stay in valid ranges.
- **E2E** (Playwright): operator dashboard renders live data, handles loading/error/degraded states, submits an allocation.

## Rules
- A bug fix starts with a failing test that reproduces the bug (Superpowers `systematic-debugging`).
- New behavior ships with tests in the same change. CI runs the full suite (see `deployment`).
- Keep tests deterministic: fixed seeds, frozen time where needed, no reliance on wall-clock or network flakiness.
- Target meaningful coverage of decision + resilience paths over raw line-coverage numbers.
