# Demo run-sheet (brief §22, ~8 min)

All data is SIMULATED. Before judges arrive: `docker compose up -d`, open http://localhost:3000,
Test Controls → type `SIMULATE` → **Reset world**, then **Run**. Grafana (optional): `docker compose --profile observability up -d` → :3001.

| # | Brief step | Do this | Say this |
|---|---|---|---|
| 1 | Normal operations | Command Center, sim RUNNING | Status bar: tick, sim time, data age, stream, platform health. Service level ~100%. |
| 2 | Operator dashboard | Click Network, then back | Depots, stations, routes live from the simulator; worker polls 1 s + SSE, API reads Redis. |
| 3 | Demand starts increasing | Test Controls → **demand spike dhaka** | Scenario targets explicit stations; nothing hidden. |
| 4 | System detects risk | Alerts (count badge rises) | Demand anomaly (robust z > 3), labelled "known demand event". |
| 5 | Predicts shortage | Command Center → Top risks | Profile forecast (5.1% WAPE) → tick-by-tick projection → stockout hours + probability. |
| 6 | Recommendation generated | Recommendations | Marginal-benefit greedy under route/depot/capacity constraints, checked by an independent validator. |
| 7 | Operator inspects | **Review** on top card | No-shipment vs recommended vs runner-up, binding constraint, before/after inventory chart, reasons. |
| 8 | Allocation simulated | Approve & submit → Confirm | Idempotency key + atomic claim: 3 concurrent approvals = exactly 1 allocation. Allocations tab shows it. |
| 9 | Crisis event | Test Controls → **route disruption mirpur** or **combined chattogram** | |
| 10 | System adapts | Recommendations | Planner re-plans on alternative routes; stale recs are superseded (409 if approved). |
| 11 | Failure injected | Fault injection → `stale_data` 30 s → Inject | (or terminal: `docker compose stop redis`) |
| 12 | Monitoring detects | Amber "Execution blocked" banner; System Health | Health model per component; data age climbs. |
| 13 | Fallback activates | Try Approve → refused `409 STATE_STALE` | Never act on stale data. Redis down → API serves last good copy (`evidence/resilience/`). |
| 14 | Operations continue | Clear all faults / `docker compose start redis` | Recovers by itself, no restarts. Decisions tab = full audit trail. |

Close with evidence: CI green (GitHub Actions), k6 500 VUs 1,040 req/s p95 5 ms 0 errors
(`evidence/phase-8/`), e2e 12/12, contract 101/101, audits clean (`evidence/security/`).

Backup if the simulator misbehaves: Test Controls → Reset world → Run, wait ~20 s.
