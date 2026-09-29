# PRD — Fuel Supply Intelligence & Resilience Platform (Backend)

| | |
|---|---|
| **Product** | Fuel Supply Operations Platform for the BUP CSE Fest 2026 Hackathon Finals |
| **Scope of this document** | Backend: API, intelligence (forecast, risk scoring, allocation), caching, resilience, DevOps |
| **Operating environment** | BUP Fuel Supply Simulator `asifmahmoud414/bup-fuel-supply-simulator:1.0.0` (REST `/v1/*`, SSE `/v1/stream`, `/admin/*`) |
| **Stack** | Python 3.12, FastAPI, Redis 7, PostgreSQL (Neon, managed), Docker Compose, Prometheus + Grafana |
| **Architecture style** | Modular monolith (one codebase, two process roles: `api` and `worker`). No Kubernetes, Kafka or microservices. |
| **Companion docs** | `architect.md` (design, data flow, schema, API contract, test plan); `pipeline.md` (final build pipeline, decisions merged from PIPELINE_1, runbook, defense). Where they differ, `pipeline.md` wins. |
| **Status** | Draft v1.0, 2026-09-29 |

---

## 1. Problem statement

An operations team must keep four fuel stations across two regions (Dhaka, Chattogram) supplied with Diesel, Petrol and Octane from two depots over six routes. The network is simulated, deterministic and fast (default 15 simulated minutes per tick, 8 ticks per wall-clock second, so a simulated day passes in about 12 seconds). Demand follows hour-of-day profiles with noise, supply arrives in scheduled batches, and organizers inject crises (demand spikes, route disruptions, station outages, depot constraints, shipment delays, supply shortfalls) and software faults (latency, unavailability, random 503s, stale data, stream disconnects) at any time, including during judging.

The simulator is the world, not the brain. It never predicts, recommends or decides. The only way to change outcomes is `POST /v1/allocations`. The team's backend must therefore:

1. observe the network continuously and correctly despite faults;
2. predict where and when stockouts will happen;
3. recommend (and, under policy, execute) allocations that respect every simulator constraint;
4. explain each recommendation so a human can inspect and approve it;
5. keep serving operators when the simulator, Redis, the database, or the model fails;
6. prove all of the above with metrics, logs and load-test evidence.

The engineering loop the product must close is:

```
Observe → Detect → Predict → Decide → Simulate → Act → Monitor → Recover
```

## 2. Goals and non-goals

### Goals

- **G1. Decision quality.** Maximise the simulator's ground-truth `service_level` (served / (served + unmet)) and minimise `allocation_failures`, under baseline and crisis scenarios.
- **G2. Responsiveness.** Operator-facing reads are served from cache on the hot path with low, predictable latency under normal and stress load.
- **G3. Graceful degradation.** Every critical operation has a defined fallback. Failure of any single dependency degrades the product; it never takes it down.
- **G4. Inspectability.** Every recommendation carries its signals, constraints, expected impact, confidence and alternatives. Consequential actions keep a human in the loop by default.
- **G5. Operability.** `docker compose up` brings the whole stack up reproducibly; CI builds, tests and smoke-tests it; health, metrics and logs make the system's state obvious to judges.

### Non-goals

- Controlling real fuel infrastructure, real purchases or real dispatches (guardrail from the brief).
- Modifying the simulator. Judges run against the published image.
- Enterprise security (SSO, multi-tenant RBAC). Basic auth hygiene only.
- Kubernetes, Kafka, service mesh, microservices. Listed as optional in the brief; they add risk without improving the scored outcome here.
- Reinforcement learning in v1. It stays an optional experiment that must beat the heuristic/optimizer baseline on the benchmark in §9 before it can be enabled.

## 3. Users and primary workflows

| Persona | Needs from the backend |
|---|---|
| **Operator** (primary) | Live network state, risk-ranked alerts, recommendations with explanations, approve/reject/simulate, manual allocation, cancel pending, decision history. |
| **Shift supervisor** | Set the automation policy (manual / assisted / auto), thresholds, see KPIs and incidents. |
| **Judge / observer** | System status page, p95 latency, error rate, fallback activations, evidence that the system reacted to injected events and faults. |
| **Developer / on-call** | Health endpoints, Prometheus metrics, structured logs, deterministic replay for debugging. |

Core workflow (mirrors the brief's demo story, §22):

1. Normal operations: dashboard shows inventory, demand, inbound supply, service level.
2. Demand rises: forecast residuals exceed threshold, anomaly detector flags the station/region.
3. Risk score crosses `HIGH`: alert raised with projected stockout time.
4. Allocation engine produces a recommendation with expected impact (e.g. risk 72% → 19%).
5. Operator inspects, optionally runs a what-if simulation, then approves.
6. Backend submits `POST /v1/allocations` idempotently and tracks the shipment to `ARRIVED`/`FAILED`.
7. Crisis event occurs (e.g. `route_disruption`): scores and recommendations recompute within one refresh cycle; alternative routes are chosen.
8. A fault is injected (e.g. `unavailable`): circuit opens, cached state is served with a `degraded` flag, alerts fire, the system recovers automatically when the fault clears.

## 4. Success metrics

### 4.1 Outcome metrics (decision quality, measured from simulator ground truth)

| Metric | Source | Target |
|---|---|---|
| `service_level` over 2 simulated days, baseline scenario | `GET /v1/metrics` | ≥ 0.99 |
| `service_level` over 2 simulated days with the standard crisis pack (§9.3) | `GET /v1/metrics` | ≥ 0.95 |
| Improvement vs. "no action" and vs. reactive threshold replenishment under crisis pack | replay benchmark | reported from replays; target ≥ +15 pp vs. "no action" (Internal Target, not claimed until measured) |
| Worst-station service level under crisis pack | replay benchmark | reported; used as the fairness tie-breaker |
| Forecast WAPE (1 h / 4 h cumulative), interval coverage | rolling-origin validation | profile champion baseline: 2.4% / 1.3% on the live baseline check; p10–p90 coverage within ±5 pp of 80% |
| `allocation_failures` caused by our own submissions | `GET /v1/metrics` | 0 in baseline; ≤ 2 per crisis run |
| Rejected allocation attempts (409 other than idempotent replay) | our metrics | < 2% of submissions |
| Stockout detection lead time | benchmark harness | ≥ 8 ticks (2 simulated hours) before first unmet liter, p50 |
| Shock-detection delay (unexplained demand shift) | shock replays | ≤ 2 ticks after onset, p50 |

### 4.2 Platform metrics (see §6 for full targets)

| Metric | Target |
|---|---|
| Read API p95 / p99 latency at normal load | ≤ 50 ms / ≤ 120 ms |
| Read API availability while simulator is faulted | ≥ 99.5% non-5xx |
| Tick-to-fresh-recommendation latency (p95) | ≤ 1.0 s wall-clock |
| Mean time to detect a simulator fault | ≤ 5 s |
| Mean time to recover after fault clears | ≤ 10 s |
| Cache hit ratio on hot read paths | ≥ 95% |

## 5. Feature scope (what the backend must deliver)

Priorities: **P0** required to pass judging, **P1** strongly expected, **P2** stretch.

### 5.1 Simulator integration (P0)

- **F-INT-1** Typed async client for every endpoint in the integration guide's defensive checklist (§10): `/v1/health`, `/v1/instance`, `/v1/regions`, `/v1/depots[/{id}]`, `/v1/stations[/{id}]`, `/v1/routes`, `/v1/supply-arrivals`, `/v1/events`, `/v1/allocations`, `/v1/demand-history`, `/v1/metrics`, `POST /v1/allocations`, `POST /v1/allocations/{id}/cancel`, `/v1/stream`.
- **F-INT-2** Response validation with Pydantic models. Invalid payloads are rejected, counted, and raise an alert; the last valid snapshot is kept (brief §11: "Invalid simulator response → Reject input + raise alert").
- **F-INT-3** Parse both error envelopes: domain errors `{"detail":{"code","message"}}`, faults `{"error":{"code":"FAULT_INJECTED"}}`, `stream_disconnect` as `{"detail":{"code":"FAULT_INJECTED"}}`, and Pydantic `{"detail":[...]}`.
- **F-INT-4** SSE consumer for `simulation.tick`, `allocation.status_changed`, `inventory.updated`, `simulator.notice`. SSE is advisory: every event triggers a REST re-read. Reconnect with backoff; no `Last-Event-ID` replay, so full REST resync on every reconnect. Fall back to polling while the stream is unavailable. Ignore `: keepalive` (15 s silence is normal).
- **F-INT-5** Stale-data handling: any `/v1/*` GET carrying `X-Simulator-Stale: true` marks the snapshot stale, blocks automatic execution, and is surfaced in every API response's `meta.stale`.
- **F-INT-6** Reset handling: `simulator.notice {"message":"Simulation reset"}` or a tick that goes backwards triggers a full cache flush and resync, and starts a new "run" in our database.
- **F-INT-7** Demand history ingestion always uses `limit` (clamped [1, 2000]); incremental fetch of `12 × ticks_since_last + margin` rows, de-duplicated by `id`.

### 5.2 Network state and read APIs (P0)

- **F-STATE-1** Normalized network snapshot per tick: instance, regions, depots, stations, routes, supply arrivals, events, allocations, metrics.
- **F-STATE-2** Operator read APIs (served from cache): network snapshot, stations, depots, routes, inbound supply, events, allocations ledger, KPIs.
- **F-STATE-3** Every response carries `meta`: `tick`, `sim_time`, `as_of`, `source` (`cache` | `l1` | `db` | `live`), `stale`, `degraded[]`.
- **F-STATE-4** Push updates to clients through our own SSE endpoint, with ETag-based polling as fallback.

### 5.3 Intelligence (P0: forecast, risk scoring, allocation; P1: anomaly detection; P2: LLM explanations)

- **F-INT-FC** Demand forecast per `(station_id, fuel_type)`. **Champion (P0): the structural profile** `b = B[profile,fuel]/96 × region factor × hour factor × live demand_multiplier` with correction `c = 1`. A read-only check on the live simulator measured it best of four methods (WAPE 5.1% at 15 min, 2.4% at 1 h, 1.3% at 4 h, versus 2.0% at 4 h for always-on correction, 25.5% for EWMA and 1.9% for seasonal naive). **Guarded adaptive correction** `c_t = (1−α)c_{t−1} + α·d_t/b_t` applies only when persistent bias opens a gate; α ∈ {0.1, 0.2, 0.4} and the gate are tuned on separate crisis replays. Train on `demand_liters`, never `served_liters` (censored). The multiplier is carried forward and never double-counted with its event. History is polled per station incrementally, because one response is capped at 2,000 rows. Fallbacks: seasonal naive → EWMA → "needs review". Optional pooled gradient-boosted challenger, kept only if it improves decision outcomes.
- **F-INT-UNC** Uncertainty from out-of-sample errors only: horizon-specific error quantile bands, plus 100–300 demand scenarios from a residual-block bootstrap aligned across stations. Risk is labelled "model-estimated". With too little error history, the product shows low/medium/high demand scenarios instead of percentages.
- **F-INT-RISK** Stockout prediction by tick-by-tick inventory accounting (`V = I + arrivals`, `served = min(demand, V)`, `unmet = demand − served`, outage serves 0) per scenario. Outputs: first unmet tick, total expected unmet liters, model-estimated shortage probability, and inventory at delivery. A shipment arriving after the first shortage still counts if it prevents later unmet demand.
- **F-INT-DET** Shock detection: robust standardized forecast error, alerting on two consecutive unusually positive values. Changes already explained by a public event are shown as "known demand event". An unexplained shift opens the adaptation gate, widens uncertainty and triggers re-planning. Also: abnormal inventory deltas, route/depot state changes, supply delays and shortfalls.
- **F-INT-ALLOC** Allocation answers one question: *which feasible plan prevents the most unmet liters?* Primary: a rolling-horizon LP (16-tick horizon plus a depot supply outlook to the next replenishment; submit only current actions; re-plan every tick) with a lexicographic objective: total unmet liters → worst-station service level → fewer liters or shorter transit. Fallback: marginal-benefit greedy (bounded increments, largest unmet reduction per scarce resource, "send nothing" always considered). Last resort: "hold" plus human review. Constraints are reserved jointly across legs and match the simulator's validation rules (§7.4); the plan is revalidated after each accepted submission. Replenishment sizing uses a quantile of *cumulative* demand over lead time plus review period, with service targets 0.8/0.9/0.95 compared by replay. Stretch: CVaR tail-risk objective and resource-sensitivity analysis.
- **F-INT-EXPL** Decision comparison panel for each recommendation: *no new shipment*, *current baseline policy*, *recommended plan* and *runner-up*, each with projected unmet liters, model-estimated shortage risk, binding constraint and why it was selected or rejected. Also inventory trajectories with arrival markers and uncertainty bands, and clearly labelled hypothetical what-if controls (e.g. demand +30%, alternate route unavailable). Explanations come from structured facts; claims like "kept fuel at this depot because another station has no alternative route" appear only when a numerical comparison supports them. An optional LLM only rewords.
- **F-INT-DIR** (P1) Operator directives in natural language, reusing the preliminary round's proven pattern: an LLM turns notes such as "keep 10,000 L diesel in reserve at Gazipur" or "avoid route-gazipur-karnaphuli until 18:00" into one of a closed set of typed directives. The directives are checked by deterministic guardrails and then compiled into planner constraints. The LLM never does quantity arithmetic, and invalid output never becomes a directive (see `architect.md` §6.9).
- **F-INT-LLM** One LLM gateway shared by F-INT-EXPL and F-INT-DIR. It uses a primary provider with a pool of round-robin keys and a per-key cooldown, allows one repair attempt on bad structured output, and switches to a backup provider only on provider or transport failure. Each request makes at most 2 model calls and has a hard deadline. By default, Gemini is the primary provider and Groq the backup, as qualified in the preliminary round.

### 5.4 Decisions and execution (P0)

- **F-DEC-1** Recommendation lifecycle: `PROPOSED → APPROVED → SUBMITTED → (IN_TRANSIT → ARRIVED | FAILED) | REJECTED | EXPIRED | SUPERSEDED`.
- **F-DEC-2** Automation policy: `MANUAL` (every submission needs approval), `ASSISTED` (auto-submit only when confidence ≥ threshold and data not stale; the rest wait for approval), `AUTO` (auto-submit unless low confidence or stale). Default `MANUAL` (human review is the brief's guardrail); `ASSISTED` is opt-in and switches itself off on stale data, degraded health, forecast fallback or repeated rejections. At simulator speed 8 a simulated hour lasts 0.5 s, so recommendations carry `valid_until_tick` and expire rather than being executed late.
- **F-DEC-3** What-if simulation of a recommendation or a manual plan without writing to the simulator.
- **F-DEC-4** Idempotent submission: deterministic `idempotency_key` derived from recommendation id and leg. Idempotent replay is accepted whether the simulator answers 200 or 201 (the guide documents both).
- **F-DEC-5** Every 409 code has a defined handling (§6.6 of `architect.md`). `ROUTE_CAPACITY_EXCEEDED` is prevented by splitting; `DISPATCH_CAPACITY_EXCEEDED` defers to the next tick.
- **F-DEC-6** Cancel pending allocations (`POST /v1/allocations/{id}/cancel`, `PENDING` only).
- **F-DEC-7** Immutable decision audit: who/what decided, inputs snapshot hash, model and policy versions, outcome.
- **F-DEC-8** Independent replay validation, adapted from the preliminary round. Before any plan is shown as `PROPOSED`, and again before it is submitted, a separate validator re-checks it against the current snapshot. The validator shares no code with the planner. It checks every simulator validation rule, every active operator directive, depot inventory and dispatch conservation across legs, station headroom, and the recomputed expected impact. A plan that fails is never submitted. It is logged, counted (`replay_rejections_total`), and replaced by the next fallback plan.

### 5.5 Resilience (P0)

Brief §11 mapping, extended:

| Failure | Required behavior |
|---|---|
| ML model unavailable or errors | Fallback allocation policy (greedy heuristic) and analytic forecast; `fallback_activations_total` increments; UI banner. |
| Invalid simulator response | Reject input, keep last valid snapshot, raise alert. |
| Prediction confidence too low | Recommendation marked `needs_review`; never auto-submitted. |
| Simulator unavailable / 503 / latency | Timeout, retry with jittered backoff, circuit breaker, serve cached state in degraded mode. |
| Redis unavailable | In-process L1 cache and PostgreSQL snapshot; writes that need Redis (locks, pub/sub) fall back to PostgreSQL advisory locks and client polling. |
| PostgreSQL unavailable | Reads from Redis/L1; audit writes spooled and replayed; **new submissions blocked** because the allocation intent cannot be recorded durably. |
| Worker process dies | API keeps serving last snapshot marked stale; container restart policy restores it; readiness reflects it. |
| API overloaded | Concurrency limit and load shedding (`503` + `Retry-After`) on non-critical routes; health and approvals keep priority. |
| LLM provider down, rate-limited or slow | Rotate keys (cooldown on 401/429), then backup provider, all within the deadline; explanations fall back to template; directive interpretation returns a controlled error and asks the operator to use the structured directive form. Decisions never wait on an LLM. |
| Plan fails replay validation | Not submitted; next fallback plan (greedy, then hold) is validated instead; alert raised. |

### 5.6 Observability (P0)

- Prometheus metrics: request rate, latency histograms, error rate per route; simulator call latency/errors per endpoint and fault type; cache hit/miss per key family; circuit state; SSE connected; tick lag; scoring duration (batch vs incremental); forecast error (MAE, MAPE); model confidence; alerts raised; recommendations produced/approved/rejected/expired; fallback activations; allocations submitted/failed by code.
- Structured JSON logs with `request_id`, `tick`, `run_id`, `recommendation_id`.
- Grafana dashboards provisioned from files: API, Simulator Integration, Intelligence, Business KPIs.
- Alert rules: simulator down, stale data, circuit open, p95 over target, fallback active, service level dropping.
- `/api/v1/system/status` returns per-component health in the brief's §15 format.

### 5.7 Out of scope for v1

RL policy in production, multi-simulator tenancy, horizontal worker scaling, autoscaling, Kubernetes/Helm, distributed tracing (OpenTelemetry hooks may exist but are not required).

## 6. Performance targets

Reference hardware: one host, 4 vCPU, 8 GB RAM (a typical laptop or small VM), all services in Docker Compose, simulator on the same host. Targets are validated by the load tests in `architect.md` §10 and reported as evidence.

### 6.1 Latency (server-side, measured at the API)

| Path | p50 | p95 | p99 |
|---|---|---|---|
| Hot reads, cache hit (`/network/snapshot`, `/stations`, `/risk`, `/recommendations`) | ≤ 10 ms | ≤ 50 ms | ≤ 120 ms |
| Reads on cache miss (fallback to DB) | ≤ 60 ms | ≤ 200 ms | ≤ 400 ms |
| `GET /system/status` | ≤ 10 ms | ≤ 30 ms | ≤ 80 ms |
| `POST /recommendations/{id}/simulate` (what-if) | ≤ 40 ms | ≤ 150 ms | ≤ 300 ms |
| `POST /recommendations/{id}/approve` (includes simulator write, healthy simulator) | ≤ 80 ms | ≤ 250 ms | ≤ 600 ms |
| Approve under `latency` fault (500 ms) | n/a | ≤ 1.5 s | hard timeout 3 s → `202 Accepted`, completion tracked asynchronously |

### 6.2 Throughput and concurrency

| Scenario | Load | Pass criteria |
|---|---|---|
| **Normal** | 50 virtual users, ~150 req/s mixed (90% reads, 8% SSE clients, 2% writes), 15 min | Latency targets in 6.1; 0% 5xx; CPU < 60% |
| **Stress** | ramp to 500 VUs, ~1,500 req/s mixed, 10 min | p95 ≤ 250 ms on hot reads; 5xx (excluding intentional 503 shedding) ≤ 0.5%; no crash or restart |
| **Spike** | 20 → 800 VUs in 10 s, hold 2 min | Shedding engages instead of timeouts; recovers to normal p95 within 30 s after spike |
| **Soak** | 100 VUs, 60 min, simulator running at speed 8 | No memory growth > 10%; no Redis key growth beyond bounded sets; p95 stable ±20% |
| **Fault under load** | Normal load + each simulator fault type for 60 s | Read availability ≥ 99.5% non-5xx; responses flagged `degraded`/`stale`; recovery ≤ 10 s after clear |
| **Dependency kill under load** | Normal load + `docker stop redis` (60 s), then `postgres` (60 s) | Hot reads continue from L1/DB or Redis respectively; no 5xx storm; automatic recovery |
| **SSE fan-out** | 300 concurrent `/api/v1/stream` clients | Each receives tick updates with ≤ 1 s lag; server memory bounded |

### 6.3 Intelligence pipeline timing

The simulator can advance a tick every 125 ms. The worker does not process every tick; it coalesces to the latest one.

| Operation | Target |
|---|---|
| Incremental re-projection after a tick (dirty keys, 200 scenarios × 16 ticks) | p95 ≤ 5 ms |
| Full batch re-projection (12 keys × 200 scenarios × 16 ticks, forecast refresh) | p95 ≤ 50 ms |
| Allocation optimisation (rolling-horizon LP, ≈ 300 variables, 3 lexicographic solves) + comparison panel | p95 ≤ 100 ms, hard timeout 250 ms per solve → greedy fallback |
| Snapshot refresh (parallel REST fetch) | p95 ≤ 150 ms healthy; bounded by per-call timeout under faults |
| Tick → updated risk and recommendations visible via API | p95 ≤ 1.0 s |
| Offline benchmark (replay 2 simulated days, 192 ticks, batch mode) | ≤ 5 s per scenario |

### 6.4 Request budgets and bounds (carried over from the preliminary round)

| Bound | Default | Purpose |
|---|---|---|
| `MAX_REQUEST_BODY_BYTES` | 65,536 | Reject oversized bodies with 413 before JSON parsing or LLM use |
| `MAX_CONCURRENT_HEAVY` / `MAX_QUEUED_HEAVY` | 16 / 64 per process | Bound concurrent and queued work on heavy routes (simulate, approve, directives); over the queue limit → 503 + `Retry-After` |
| `TOTAL_REQUEST_DEADLINE_MS` | 3,000 (approve), 1,000 (simulate), 9,000 (directives) | End-to-end deadline from queue entry to response; on expiry the request returns a controlled error or 202, never hangs |
| `LLM_TIMEOUT_SECONDS` / `LLM_HARD_DEADLINE_SECONDS` | 4 / 8 | Per-attempt and total model-stage budget |
| Directive notes | 1–5 notes per request, ≤ 2,000 chars each | Bounded input shape |

Health endpoints never share these queues, so they stay responsive under saturation.

## 7. Scoring logic requirements

"Scoring" means the risk and priority scores that drive alerts and allocation. The same pure functions must serve two modes:

| Mode | When | Requirement |
|---|---|---|
| **Batch** | startup, simulator reset, SSE reconnect, stale-data recovery, backtests, hard/stress test replays | Recompute all 12 `(station, fuel)` keys and all candidate legs in one vectorised pass (NumPy arrays of shape `[M scenarios, 12, H]`). Deterministic: same snapshot → identical scores. |
| **Incremental** | every coalesced tick or SSE event during normal use | Recompute only dirty keys (inventory changed, allocation status changed, event touched station/route/depot, forecast residual moved). Reuse cached per-key features. |

Requirements:

- **S-1** Batch and incremental produce identical outputs for the same snapshot (property-tested).
- **S-2** Scores are a function of the snapshot plus the model version only (no wall-clock dependency) so replays are reproducible against the deterministic simulator.
- **S-3** Objective aligned with judging ground truth, lexicographic: minimise projected unmet liters across the network (drives `service_level`), then maximise the worst station's service level, then fewer transferred liters or shorter transit. No hand-weighted composite scores, and no uncertainty penalty that deprioritises the station most at risk.
- **S-4** Risk levels from the scenario-based, model-estimated shortage probability: `LOW` < 0.2, `MEDIUM` 0.2–0.5, `HIGH` 0.5–0.8, `CRITICAL` ≥ 0.8 or first unmet tick within the fastest available lead time. With too little error history, levels come from low/medium/high demand scenarios and no percentages are shown.
- **S-5** Confidence is reported per score as HIGH/MEDIUM/LOW (from out-of-sample error coverage, freshness and fallback use); LOW → `needs_review`.
- **S-7** Scenario sampling is seeded from `(epoch, tick)` so batch, incremental and replay runs produce identical numbers.
- **S-6** Hot-path reads never compute scores; they read precomputed results from cache.

Detailed formulas are in `architect.md` §6.

## 8. Caching strategy and fallback behavior

Redis reduces latency on the hot paths and decouples API read load from the simulator. The simulator is read only by the worker, never by API request handlers, so user load never amplifies into simulator load.

### 8.1 What is cached

| Data | Key family | TTL (Redis safety net) | Logical freshness | Writer |
|---|---|---|---|---|
| Normalized network snapshot (pre-rendered JSON bytes + ETag) | `sim:snapshot` | 60 s | valid for 2 ticks | worker |
| Static world (regions, route topology, capacities) | `sim:static:*` | 24 h | until reset notice | worker |
| Per-view rendered responses (stations, depots, routes, supply, events) | `view:*` | 60 s | same tick as snapshot | worker |
| Forecasts per `(station, fuel)` | `fc:*` | 300 s | until `tick + 4` or residual breach | worker |
| Risk scores (hash per key + sorted set by priority) | `risk:*` | 60 s | current tick | worker |
| Active recommendations | `rec:*` | until `valid_until_tick` (max 600 s) | tick-bounded | worker/API |
| Idempotency map `idempotency_key → allocation id` | `idem:*` | 7 d | permanent in DB | API |
| KPIs (`/v1/metrics` + derived) | `kpi:*` | 60 s | current tick | worker |
| Component heartbeats | `hb:*` | 15 s | presence = alive | all |
| Worker leader lock | `lock:worker` | 10 s, renewed every 3 s | n/a | worker |
| Rate-limit counters | `rl:*` | 60 s | window | API |

TTLs are safety nets. Freshness is decided by `tick` stored in each value, because wall-clock time per tick changes with `SIMULATION_SPEED` and pause/run.

### 8.2 Fallback per critical operation

| Operation | Primary | Fallback 1 | Fallback 2 | User-visible effect |
|---|---|---|---|---|
| Hot reads | Redis | In-process L1 (last good, ≤ 1 s) | PostgreSQL latest snapshot | `meta.source`, `meta.stale` |
| Snapshot refresh | SSE-triggered REST fetch | Polling every 1 s | Keep last valid snapshot, mark stale after 2 ticks without refresh | Stale banner |
| Forecast | ML model | Analytic profile + EWMA | Last cached forecast (if ≤ 8 ticks old) | Confidence lowered |
| Risk scoring | Incremental | Batch recompute | Last scores + stale flag | None / stale flag |
| Allocation plan | LP optimizer | Greedy heuristic | Hold + human-review alert | `policy_used` in explanation |
| Submission | Direct POST with retry | Durable outbox retry (same idempotency key) | Mark `SUBMIT_FAILED`, alert operator | Status on recommendation |
| Push updates | Redis pub/sub → SSE | Client ETag polling | n/a | Slightly higher lag |
| Leader election | Redis lock | PostgreSQL advisory lock | Single worker by config | None |
| Audit write | PostgreSQL | Redis stream spool | Local file spool | None (replayed later); submissions blocked until PostgreSQL returns |
| Execution gate | Fresh execution snapshot (current tick, ≤ 2 s, no stale header) | none | `409 STATE_STALE` | Approve disabled with the reason |

On a cache miss the API never calls the simulator; it falls back down the chain above. Cache stampede is prevented because only the worker populates cache keys; API processes read only.

## 9. Testing and acceptance

### 9.1 Test layers

Unit (scoring, forecasting, constraint validation), contract (against the pinned simulator image), deterministic scenario tests using `/admin/reset` + `/admin/step`, fault tests for all five fault types, dependency chaos tests, load tests (normal, stress, spike, soak, fault-under-load), and a decision-quality benchmark. Details in `architect.md` §10.

### 9.2 Acceptance criteria for "done"

- All P0 features implemented and covered by tests; CI green.
- `docker compose up` on a clean machine yields a healthy stack in ≤ 90 s.
- Load-test report with avg, p50, p95, p99, throughput, error rate, concurrency and resource usage for every scenario in §6.2.
- Resilience demo: each row of §5.5 demonstrated at least once with metrics and logs.
- Benchmark report meets §4.1 targets.

### 9.3 Standard crisis pack (used for benchmark and demo rehearsals)

Injected via `POST /admin/events` after `/admin/reset`:

1. `demand_spike` region-dhaka, multiplier 1.8, start 8, duration 12
2. `route_disruption` route-gazipur-mirpur, start 24, duration 16
3. `shipment_delay` depot-patiya DIESEL, delay_ticks 6, start 30, duration 1
4. `station_outage` station-tongi, start 40, duration 8
5. `depot_constraint` depot-gazipur, start 50, duration 20
6. `supply_shortfall` depot-gazipur PETROL, factor 0.5, start 60, duration 1
7. Combined: `demand_spike` region-chattogram 1.6 + `route_disruption` route-patiya-karnaphuli, start 80, duration 12

## 10. Deployment and DevOps

- **Packaging:** one Docker image for the backend (`api` and `worker` roles selected by command), multi-stage build, non-root user, pinned dependencies (`uv.lock`), image tagged by git SHA and semver.
- **Runtime:** `docker compose up` starts `simulator`, `api` (2 replicas), `worker` (1), `redis`, `postgres`, `migrate` (one-shot Alembic), `prometheus`, `grafana`, and the frontend behind `nginx`. Health checks and `depends_on: condition: service_healthy` sequence startup.
- **Configuration:** 12-factor, `pydantic-settings`, all via environment; `.env.example` documented; no secrets in the repo.
- **CI (GitHub Actions):** lint (ruff), type check (mypy), unit tests, contract + scenario tests against the simulator image as a service container, image build, compose smoke test hitting `/readyz`, short k6 smoke load test, publish image on main.
- **CD / rollback:** deploy by image tag; rollback by re-deploying the previous tag. Model and policy versions are rows in the database and can be rolled back at runtime via the policy API without a redeploy.
- **Health:** `/healthz` (liveness, process only), `/readyz` (Redis or fallback available, DB reachable or spooling, snapshot age within bounds), `/api/v1/system/status` (per-component).
- **Supply-chain hygiene (carried over from the preliminary round):** hash-verified dependency install (`uv sync --frozen` from a hashed lock), `pip-audit --strict` in CI, CI actions and the Docker base image pinned by digest, `.dockerignore` excludes `.env`, tests and dev files, image runs as non-root UID/GID 10001 with a `HEALTHCHECK`. Tagged releases publish to GHCR with SBOM and provenance, and the submission references the image by immutable digest.
- **Verification scripts:** `scripts/run_scenarios.py [--base-url]` runs the crisis pack (§9.3) against a local or deployed stack and prints pass/fail per scenario with service level; `scripts/qualify_llm.py --provider {primary,backup} --passes 3` runs the directive-interpretation cases (official examples plus adversarial paraphrases) per provider and reports accuracy and median/max latency.
- **Controlled errors:** malformed requests return 400/422 with a stable error code; internal, model, solver or validation failures return a controlled 500 with a `request_id` and never leak stack traces, prompts, provider response bodies or secrets. FastAPI `/docs` is off unless `ENABLE_DOCS=true`.
- **Security hygiene:** `AUTH_MODE=off` for the local single-operator demo; `AUTH_MODE=apikey` (viewer/operator/admin keys from environment) is forced when `APP_ENV=public`. The simulator test plane (`/admin/*` proxy) is registered only when `APP_ENV ∈ {local,test,demo}` and `ENABLE_SIMULATOR_TEST_CONTROLS=true` (otherwise 404), uses allowlisted payloads and typed confirmation, and is audited separately. Input validation on all endpoints; CORS restricted to the frontend origin; gitleaks and Semgrep in CI.
- **Database:** PostgreSQL via `DATABASE_URL`. Neon (managed) is the database, with branches for dev, test and demo and no local container; see `pipeline.md` §0.4 for the internet-dependency mitigations.

## 11. Assumptions and open questions

- The simulator runs on the same host/network as the backend (local Compose). Latency figures assume this.
- The guide lists idempotent replay as 201 in §5.4 and as 200 in §9; the client accepts both.
- The guide's `/v1/demand-history` supports only `station_id` and `limit` filters, not a tick range; incremental ingestion relies on `id` de-duplication.
- `depot_constraint` sets `CONSTRAINED` without changing numeric capacity; the risk model applies a configurable effective-dispatch haircut (default 50%) while the status holds, and this is documented as an assumption.
- Organizer surprise events may add new event types; unknown types are stored, surfaced as generic alerts, and never crash ingestion.
- Open: whether judges run the simulator at a speed other than 8. The design is tick-driven, not time-driven, so it tolerates any speed; confirm during rehearsal.

## 12. Milestones

| # | Milestone | Exit criterion |
|---|---|---|
| M1 | Skeleton + integration | Compose stack up; worker ingests snapshot; read APIs served from Redis; CI green |
| M2 | Intelligence v1 | Analytic forecast, risk scoring (batch + incremental), greedy allocator, recommendations with explanations |
| M3 | Decisions + execution | Approve/reject/simulate, idempotent submission, outbox, audit |
| M4 | Resilience + observability | All fallbacks in §8.2, dashboards, alerts, status page |
| M5 | ML + optimizer | Learned forecast and LP allocator behind fallbacks; benchmark shows gain |
| M6 | Evidence | Load-test report, resilience demo script, benchmark report, architecture diagram |

## 13. Carried over from the preliminary round (Gridlock-d)

| Preliminary pattern | Adapted here as |
|---|---|
| LLM interprets notes into a closed set of directive types; guardrails validate; compiler feeds the LP | F-INT-DIR operator directives compiled into allocation constraints |
| Primary provider with key pool, one repair attempt, backup provider only on provider failure, max 2 calls, hard deadline | F-INT-LLM gateway |
| SciPy / HiGHS linear program | Primary allocation optimizer |
| Independent deterministic replay of the plan before responding | F-DEC-8 replay validator before proposal and before submission |
| Bounded body, queue, concurrency and total deadline; health independent | §6.4 request budgets |
| Hash-pinned deps, pip-audit, pinned CI and base image, non-root image, SBOM, GHCR digest | §10 supply-chain hygiene |
| Public-sample runner and provider qualification scripts | §10 verification scripts |
| Controlled 500 without internals; docs routes disabled | §10 controlled errors |

Not carried over: the two-endpoint public surface and no-auth design (this product needs an operator API with protected write actions), a controlled 500 as the final outcome (here every critical path ends in a degraded but working state instead), and a single stateless process (the finals need a stateful worker, cache and database).
