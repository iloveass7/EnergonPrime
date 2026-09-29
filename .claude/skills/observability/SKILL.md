---
name: observability
description: Logging, metrics, tracing, health, and decision-audit rules for the fuel platform. Use when adding instrumentation, health endpoints, dashboards/alerts, or the decision audit log.
---

# Observability

You can't operate what you can't see. Instrument as you build, not at the end.

## Structured logging
- JSON logs, one event per line, with `correlation_id` propagated from API → services → integration client.
- Log level via config. No secrets/PII in logs. Log fault activation and recovery explicitly.

## Metrics (Prometheus-style `/metrics`; visualize in Grafana)
Track at minimum: API latency, request rate, error rate, simulator availability, model latency,
model confidence, forecast error, fallback/degraded activation, decision frequency, allocation failures (409/503),
circuit-breaker state, SSE connection status, overall system health.

## Tracing
OpenTelemetry spans across the request/decision path (API → forecast → optimize → allocate → simulator).

## Health
- `/health/live` (process up) and `/health/ready` (deps reachable, or explicitly degraded).
- Ready endpoint reports per-dependency status and current degraded flags.

## Decision audit (required, durable in Postgres)
Every recommendation/allocation records: timestamp, inputs snapshot, forecast + intervals, constraints,
chosen action, objective value, explanation, alternative, outcome, and whether degraded mode was active.
This enables replay, debugging, and "why did it do that?" review.
