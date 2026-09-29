---
name: operator-dashboard
description: Operator-facing UI rules — an operations center, not a CRUD app. Use for React/TypeScript frontend, real-time SSE UI, charts/tables, decision-explanation views, and loading/error/degraded states.
---

# Operator Dashboard

Feels like a **network operations center**: live situational awareness, clear alerts, explainable decisions.
React + TypeScript + Vite.

## Core views
- **Situation map/overview**: depots, stations, routes with live status; shortages/risk highlighted.
- **Forecast panel**: demand + prediction intervals per station; forecast error over time.
- **Alerts/anomalies**: prioritized, acknowledgeable; link to the underlying event.
- **Decision/recommendation view**: proposed allocation, the "why", binding constraints, the alternative,
  and an operator action (approve/adjust) — with confirmation before any write.
- **System health**: simulator availability, latency, breaker state, degraded-mode banner, fallback activation.

## Real-time & resilience (UI must mirror backend resilience)
- Consume `/v1/stream` (SSE) with auto-reconnect; on reconnect, refetch REST state (SSE is advisory).
- Every data surface has explicit **loading / error / empty / degraded** states. When backend is degraded,
  show a persistent banner and mark stale data — never present stale/uncertain data as live truth.
- Show uncertainty visually (intervals/confidence), not just point numbers.

## Quality
- Charts: Recharts/visx; accessible (labels, keyboard nav, sufficient contrast, no color-only encoding).
- Typed API client generated/aligned with backend Pydantic models. Pull React/lib APIs from **Context7**.
- Test with Playwright (see `testing`): live render, degraded state, allocation submit flow.
