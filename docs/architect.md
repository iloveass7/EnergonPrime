# Architecture — Fuel Supply Intelligence & Resilience Platform (Backend)

| | |
|---|---|
| **Companion docs** | `prd.md` (requirements, targets, acceptance); `pipeline.md` (final build pipeline; wins where they differ) |
| **Style** | Modular monolith: one Python package, two process roles (`api`, `worker`), Redis as cache/coordination, PostgreSQL as system of record |
| **Simulator** | `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`, base URL `SIM_BASE_URL` (default `http://simulator-api:8000`) |
| **Status** | Draft v1.0, 2026-09-29 |

---

## 1. Design principles

1. **The simulator is the world; we are the brain.** Our backend owns observation, prediction, decision and explanation. The simulator owns truth. REST is the source of truth, SSE is a hint.
2. **One reader of the simulator.** Only the `worker` reads the simulator. API handlers read from cache. User load never becomes simulator load, and simulator faults never block user reads.
3. **Precompute, then serve.** Scores, forecasts, recommendations and even rendered JSON are computed per tick by the worker and stored in Redis. The hot path is "fetch bytes, return bytes".
4. **Tick-driven, not clock-driven.** Freshness, expiry and forecasting horizons are measured in simulator ticks, so the system behaves the same at `SIMULATION_SPEED=1` or `=8`, running or paused.
5. **Every dependency has a fallback.** Degradation is explicit (`meta.degraded[]`), observable (metrics, alerts) and reversible (automatic recovery).
6. **Deterministic core.** Scoring and allocation are pure functions of `(snapshot, model_version, policy_version)`. The simulator is deterministic, so any run can be replayed exactly.
7. **Simple infrastructure.** Docker Compose, no Kubernetes, no Kafka, no microservices. Module boundaries are enforced in code (import rules), not by network hops.

## 2. High-level system design

```mermaid
flowchart LR
  subgraph SIM[BUP Fuel Supply Simulator]
    V1[/REST /v1/*/]
    SSE[/SSE /v1/stream/]
    ADM[/admin/*/]
  end

  subgraph WORKER[worker process x1]
    ING[ingest: SSE consumer + refresher]
    ST[state: snapshot builder + diff]
    FC[forecast]
    RK[risk scoring]
    DET[anomaly detection]
    AL[allocation engine]
    OB[outbox dispatcher]
  end

  subgraph API[api process xN - FastAPI]
    R[read routers]
    D[decision routers]
    PS[SSE push /api/v1/stream]
    HS[health + status]
  end

  RD[(Redis 7)]
  PG[(Neon PostgreSQL)]
  PR[Prometheus] --> GF[Grafana]
  UI[Operator web app] --> NG[nginx]

  SSE --> ING
  V1 --> ING
  ING --> ST --> FC --> RK --> AL
  ST --> DET --> RK
  ST & RK & AL --> RD
  ST & RK & AL --> PG
  OB -->|POST /v1/allocations| V1
  D -->|POST /v1/allocations, cancel| V1
  NG --> API
  R --> RD
  R -. fallback .-> PG
  D --> PG
  D --> RD
  PS <-- pub/sub --- RD
  API & WORKER -->|/metrics| PR
```

### 2.1 Process roles

| Role | Replicas | Responsibilities | Talks to |
|---|---|---|---|
| `api` | 2 (Compose), scale by CPU | HTTP API, auth, validation, reads from cache, approval/submission, what-if, SSE push to clients, `/metrics` | Redis, PostgreSQL, simulator (writes only: allocations, cancel) |
| `worker` | 1 active (leader lock), optional standby | SSE ingest, snapshot refresh, forecasting, scoring, detection, allocation planning, outbox retries, cache population, model retraining job | Simulator (reads + outbox writes), Redis, PostgreSQL |
| `migrate` | one-shot | Alembic migrations before `api`/`worker` start | PostgreSQL |

Both roles run the same image: `fuelops api` → `uvicorn fuelops.api.main:app --workers ${API_WORKERS}`; `fuelops worker` → `python -m fuelops.worker`.

### 2.2 Code layout (modular monolith)

```
fuelops/
  config.py              # pydantic-settings, all env vars
  simclient/             # typed async client, error envelopes, retry, circuit breaker, SSE reader
  domain/                # pure models: Depot, Station, Route, Allocation, Snapshot, enums (DIESEL|PETROL|OCTANE ...)
  state/                 # snapshot builder, diff -> dirty keys, reset detection
  intelligence/
    forecast/            # analytic baseline, ML model wrapper, registry
    scoring/             # risk scoring: batch + incremental (NumPy)
    detection/           # residual z-score, inventory delta, supply delay detectors
    allocation/          # constraints, LP optimizer, greedy heuristic, what-if
    validate/            # independent replay validator (shares no code with allocation/)
    directives/          # directive schema, guardrails, compiler -> planner constraints
    explain/             # template explanations, optional LLM adapter
  llm/                   # provider gateway: key pools, cooldown, repair, backup, deadlines
  decisions/             # recommendation lifecycle, policy, outbox, idempotency
  cache/                 # Redis client, L1 cache, key builders, fallback chain
  persistence/           # SQLAlchemy 2 async models, repositories, spool
  api/                   # FastAPI app, routers, deps, middleware (auth, limits, shedding)
  worker/                # main loop, leader lock, schedulers
  observability/         # Prometheus metrics, structlog config, health registry
```

Import rule (checked by `import-linter` in CI): `domain` and `intelligence` import nothing from `api`, `worker`, `cache`, `persistence` or `simclient`. They are pure and unit-testable. `intelligence.validate` must not import `intelligence.allocation`, so the replay check is a genuinely independent implementation, not the planner checking itself.

## 3. Data flow and request handling paths

### 3.1 Ingest path (worker)

```
SSE event / poll timer
      │  (sets dirty hints; coalesces bursts)
      ▼
refresh loop (single-flight; at most one refresh in flight)
      │  asyncio.gather of GETs with per-call timeout + retry:
      │   /v1/instance /v1/depots /v1/stations /v1/routes /v1/supply-arrivals
      │   /v1/events /v1/allocations /v1/metrics /v1/demand-history?limit=N
      ▼
validate (Pydantic)  ──invalid──► keep previous part, alert `sim_invalid_response`
      ▼
merge into Snapshot(tick, parts{...}, stale_flags, fetched_at)
      ▼
diff vs previous → dirty keys {(station_id, fuel_type)}, dirty routes/depots, reset?
      ▼
forecast update (dirty keys; residual check)
      ▼
risk scoring (incremental for dirty keys, batch on reset/reconnect/stale-recovery)
      ▼
anomaly detection → alerts
      ▼
allocation planning (if any key ≥ MEDIUM or plan invalidated)
      ▼
write: Redis (pipeline, one round trip) + PostgreSQL (async, batched) + PUBLISH ch:updates
```

**Coalescing.** At `SIMULATION_SPEED=8` a tick arrives every 125 ms. `simulation.tick` events only set `dirty=True` and record the latest tick. The refresh loop runs back-to-back while dirty, so under load it skips intermediate ticks instead of queueing them. Missed ticks cost nothing: all state is re-read from REST and demand history is de-duplicated by `id`.

**Partial failures.** With an `error_rate` fault of 0.25 and nine parallel GETs, about 92% of refreshes would see at least one 503 without retries. Each GET therefore retries independently (3 attempts), and if a part still fails the snapshot keeps the previous value for that part and marks it `stale_parts=["routes"]`. Scores computed from a snapshot with stale parts inherit reduced confidence.

**Demand history.** No tick filter exists, so the worker requests `limit = min(2000, 12 × (tick − last_ingested_tick) + 24)` and upserts by `id`. After reset or reconnect it requests `limit=2000` once.

**Reset detection.** `simulator.notice` with `"Simulation reset"`, or `instance.tick < last_tick`, or a changed `seed`/`scenario_id` → flush `sim:*`, `fc:*`, `risk:*`, `rec:*`, `view:*`; open a new `runs` row; batch rescore.

**SSE lifecycle.** Connect `GET /v1/stream`; ignore `: connected` and `: keepalive` comments; treat 45 s without any bytes as dead (three keepalive intervals). On 503 `FAULT_INJECTED` (stream_disconnect), EOF or error: exponential backoff (0.5 s → 8 s cap, jitter), switch refresh to polling every `POLL_INTERVAL_MS` (default 1000) until the stream is back, then do a full resync. Queue overflow (>200 events behind) is silent on the simulator side; the refresh loop's tick check (`instance.tick` jumped by more than expected with no events) forces a reconnect.

### 3.2 Hot read path (api)

```
request → auth → concurrency limiter → router
   → L1 (in-process dict, entry age ≤ L1_TTL_MS=500, same tick)  hit → return bytes
   → Redis GET view:* / sim:snapshot                              hit → fill L1 → return bytes
   → PostgreSQL latest snapshot row (JSONB) → render              hit → return (meta.source="db", stale per age)
   → 503 {"error":{"code":"STATE_UNAVAILABLE"}} with Retry-After: 2
```

- Values in Redis are pre-rendered `orjson` bytes plus an ETag (`"{run_id}:{tick}:{hash8}"`). The handler returns a `Response` directly, with no model serialization on the hot path. `If-None-Match` → `304`.
- `meta` is embedded in the cached bytes at render time by the worker; if the API served from a fallback it wraps the bytes with an updated `meta.source`/`degraded`.

### 3.3 Decision path (api → simulator)

```
POST /api/v1/recommendations/{id}/approve
  → load rec (Redis rec:{id}, fallback DB) ; check status=PROPOSED, current_tick ≤ valid_until_tick
  → replay-validate legs against the *current* snapshot and active directives (§6.5.1); if invalid → 409 REC_INVALIDATED + fresh plan id
  → DB tx: rec → APPROVED; insert allocation_intents rows (status=SUBMITTING, idempotency_key, body_hash)  [outbox]
  → for each leg: POST /v1/allocations (timeout 3 s, retry on 503/timeout with same key)
       201/200 → allocation_intents.status=ACCEPTED, store sim allocation id, idem:{key} in Redis
       409 code → map per §6.6 (may defer, re-plan, or fail the leg)
       timeout after retries → leave QUEUED; worker outbox retries; respond 202
  → audit row ; PUBLISH ch:updates
  → 200 {rec, legs[]} or 202 {rec, legs[], pending=true}
```

Before any of this, the **execution freshness gate** applies: the snapshot must be from the current tick, at most 2 s old, with no stale header and a closed circuit, and PostgreSQL must be writable; otherwise `409 STATE_STALE` / `503` with the blocking reason. Intents follow the state machine in `pipeline.md` §3.4 (`SUBMITTING → RECONCILING → ACCEPTED | NEEDS_REVIEW`). Automatic submissions (policy `ASSISTED`, opt-in; default is `MANUAL`) follow the same path from the worker.

### 3.4 Push path

Worker `PUBLISH ch:updates {"tick", "changed":["risk","recs","stations"...]}`. Each API process holds one Redis subscription and fans out to its connected `/api/v1/stream` clients through per-client bounded `asyncio.Queue(maxsize=100)`; a slow client is dropped with an `event: resync` hint rather than blocking others. If Redis pub/sub is down, the stream emits `event: degraded` and clients switch to ETag polling of `/api/v1/network/snapshot` every 2 s.

## 4. Caching architecture

### 4.1 Layers

| Layer | Scope | Purpose | Size bound |
|---|---|---|---|
| L1 | per API process, in-memory | absorb burst reads of the same tick; survive Redis outage briefly | ~50 entries, 500 ms TTL, last-good copy kept indefinitely for fallback |
| L2 | Redis | shared precomputed state across API replicas; coordination | `maxmemory 256mb`, `volatile-lru`; every key has a TTL |
| L3 | PostgreSQL | durable last snapshot, history, audit | retention jobs (§5.3) |

Redis runs without persistence for cache keys (`appendonly no`); everything in Redis is rebuildable by the worker within one refresh, except idempotency keys and the spool, which are also written to PostgreSQL (or to the local file spool if PostgreSQL is down).

### 4.2 Key catalogue

Freshness is tick-based (`value.tick`); the Redis TTL is only a safety net against orphaned keys.

| Key | Type | Content | TTL | Fresh while | On miss |
|---|---|---|---|---|---|
| `sim:snapshot` | string | full normalized snapshot bytes + `meta` | 60 s | `tick ≥ current_tick − 1` | L1 last-good → DB `snapshots` latest |
| `sim:current_tick` | string | `{run_id, tick, sim_time, status}` | 60 s | always overwritten | derive from snapshot |
| `sim:static:regions`, `sim:static:topology` | string | regions, route topology, capacities | 24 h | until reset | worker refetches; API uses DB copy |
| `view:stations`, `view:depots`, `view:routes`, `view:supply`, `view:events`, `view:allocations`, `view:station:{id}` | string | rendered responses | 60 s | same tick as snapshot | render from `sim:snapshot` (cheap) → DB |
| `fc:{station}:{fuel}` | string | `μ[1..H], σ[1..H], model_version, confidence, tick` | 300 s | `tick ≤ fc.tick + 4` and no residual breach | worker recomputes; API serves last with `stale` |
| `risk:{station}:{fuel}` | hash | score fields (§6.3) | 60 s | current tick | batch rescore on worker; API → DB `risk_scores` latest |
| `risk:rank` | zset | member `{station}:{fuel}`, score = priority | 60 s | current tick | as above |
| `rec:active` | zset | rec ids by priority | 600 s | members expire by `valid_until_tick` | DB `recommendations where status='PROPOSED'` |
| `rec:{id}` | string | recommendation + explanation | `min(600 s, ticks_left × tick_seconds + 30 s)` | `tick ≤ valid_until_tick` | DB |
| `idem:{idempotency_key}` | string | sim allocation id | 7 d | permanent | DB `allocation_intents` |
| `kpi:current` | string | `/v1/metrics` + derived KPIs | 60 s | current tick | DB `kpi_samples` latest |
| `hb:{component}:{instance}` | string | heartbeat JSON | 15 s | present | component considered down |
| `lock:worker` | string | leader instance id | 10 s, renewed every 3 s | held | PG advisory lock (§4.4) |
| `rl:{api_key}:{window}` | string | counter | 60 s | window | allow (fail open) |
| `spool:audit` | stream | audit rows buffered while DB down | none (trimmed on replay, `MAXLEN ~ 100000`) | n/a | local file spool |
| `ch:updates` | pub/sub | change notifications | n/a | n/a | clients poll |

### 4.3 Write discipline and invalidation

- Only the worker writes `sim:*`, `view:*`, `fc:*`, `risk:*`, `kpi:*`. All writes for one tick go in one `MULTI/EXEC` pipeline, then one `PUBLISH`. Readers never see a half-updated tick.
- API processes write only `rec:*` status transitions, `idem:*`, `rl:*`, `hb:*`.
- No cache stampede: API misses never recompute or call the simulator; they fall back to L1/DB.
- Invalidation triggers: reset (flush families), `X-Simulator-Stale: true` (keep data, set `stale=true`, freeze auto-submit), event start/resolve affecting a route/depot/station (dirty keys → rescore → overwrite), recommendation approval (remove from `rec:active`).

### 4.4 Fallback logic per critical operation

Each row is implemented as an explicit chain in code (`cache/fallback.py`, `simclient/resilience.py`) with a Prometheus counter `fallback_activations_total{operation, level}`.

**Read network state (API)**

```
try Redis (timeout 50 ms)
except/miss → L1 last-good (if same run) → mark source="l1"
          → DB latest snapshot → source="db", stale = (current_tick − snap.tick) > 2
          → 503 STATE_UNAVAILABLE
```

**Refresh snapshot (worker)**

```
per GET: timeout(connect 0.5 s, read 2.0 s) + retry 3× (100 ms·2^n + jitter) on 503/timeout/conn error
circuit breaker per simulator: open after 5 consecutive failures or ≥50% of last 20; half-open after 5 s (1 probe)
circuit open → no /v1 calls; poll /v1/health every 2 s (it bypasses faults, so it only proves liveness);
               close circuit when a /v1/instance probe succeeds
part failed → keep previous part, stale_parts += [part]
all parts failed for > 2 ticks worth of wall time → snapshot.stale = true, alert SimulatorDegraded
```

**Stream updates (worker)**

```
SSE connected → event-driven refresh
503 / EOF / 45 s silence → backoff reconnect + polling every 1 s → on reconnect full resync (batch rescore)
```

**Forecast**

```
structural profile champion (c = 1; guarded correction only when its gate is open)
profile inputs invalid (schema/multiplier missing) → seasonal naive (same tick previous day, if ≥ 96 ticks)
→ EWMA level (confidence LOW) → last cached fc:* if age ≤ 8 ticks → else key UNKNOWN (needs_review)
uncertainty: out-of-sample error quantiles → if too few errors: low/medium/high demand scenarios, no percentages
optional tree challenger (P2): shadow only; any error is ignored
```

**Risk scoring**

```
incremental(dirty keys)
exception / snapshot schema changed / reset → batch(all keys)
batch exception → keep previous risk:* with stale=true, alert ScoringFailed
```

**Allocation planning**

```
rolling-horizon LP, lexicographic objective (HiGHS via scipy.optimize.linprog, 250 ms per solve)
infeasible / timeout / solver error → marginal-benefit greedy (§6.5)
greedy produces nothing but risk ≥ HIGH → "HOLD" recommendation with needs_review + alert
every candidate plan passes the replay validator (§6.5.1) before it is proposed; a rejected plan falls to the next level
```

**LLM calls (explanations, directive interpretation)**

```
primary provider, next key in round-robin pool (skip keys in cooldown; 401/429 → cooldown LLM_KEY_COOLDOWN_SECONDS)
  structured output fails schema/guardrails → one repair call to the same provider with the validation errors
  provider/transport error or timeout       → one call to the backup provider (only then)
max 2 model calls; hard deadline LLM_HARD_DEADLINE_SECONDS
failure → explanations: template text ; directives: 422 DIRECTIVE_UNINTERPRETABLE + structured-form hint
never on the decision hot path: planning and scoring never wait on an LLM
```

**Submission**

```
POST /v1/allocations with deterministic idempotency_key
503 / timeout → RECONCILING: GET /v1/allocations, match idempotency_key → ACCEPTED if found
              not found → retry with same key + same body (up to 3×) ; still ambiguous → NEEDS_REVIEW + alert
still failing → leave in outbox (QUEUED); worker retries every tick until valid_until_tick, then EXPIRED + alert
circuit open → do not attempt; queue; tell operator "queued, simulator unavailable"
```

**Leader election (worker)**

```
Redis SET lock:worker NX PX 10000, renew every 3 s
Redis down → pg_try_advisory_lock(hashtext('fuelops-worker'))
both down → run only if WORKER_STANDALONE=true (default in Compose, since there is one worker)
```

**Audit / persistence (both)**

```
PostgreSQL insert (timeout 500 ms)
down → XADD spool:audit ; Redis down too → append JSONL to /var/spool/fuelops/audit.jsonl
replayer drains spool on DB recovery (idempotent by audit uuid)
while DB down: all new submissions blocked (intent durability), reads continue, health = Unhealthy for writes
```

**Client push (API)**

```
Redis pub/sub → SSE fan-out
pub/sub unavailable → event: degraded → client ETag polling
```

**Overload (API)**

```
global in-flight limit per process (API_MAX_INFLIGHT=256)
over limit → shed non-critical GETs with 503 + Retry-After: 1
never shed: /healthz, /readyz, /api/v1/system/status, approve/reject/cancel
```

## 5. Data model

### 5.1 PostgreSQL schema (core tables)

```sql
-- One row per simulator run (created on first contact and on every reset)
CREATE TABLE runs (
  id              BIGSERIAL PRIMARY KEY,
  scenario_id     TEXT NOT NULL,
  scenario_version TEXT NOT NULL,
  seed            BIGINT NOT NULL,
  started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at        TIMESTAMPTZ
);

-- Latest snapshot per tick (compact JSONB); used for DB fallback and replay
CREATE TABLE snapshots (
  run_id      BIGINT REFERENCES runs(id),
  tick        INT NOT NULL,
  sim_time    TIMESTAMPTZ NOT NULL,
  stale       BOOLEAN NOT NULL DEFAULT false,
  stale_parts TEXT[] NOT NULL DEFAULT '{}',
  body        JSONB NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, tick)
);

-- Mirror of /v1/demand-history (training + backtests)
CREATE TABLE demand_observations (
  run_id        BIGINT REFERENCES runs(id),
  sim_id        BIGINT NOT NULL,            -- simulator row id (dedupe)
  station_id    TEXT NOT NULL,
  fuel_type     TEXT NOT NULL CHECK (fuel_type IN ('DIESEL','PETROL','OCTANE')),
  tick          INT NOT NULL,
  sim_time      TIMESTAMPTZ NOT NULL,
  demand_liters NUMERIC(12,3) NOT NULL,
  served_liters NUMERIC(12,3) NOT NULL,
  unmet_liters  NUMERIC(12,3) NOT NULL,
  PRIMARY KEY (run_id, sim_id)
);
CREATE INDEX ON demand_observations (run_id, station_id, fuel_type, tick DESC);

CREATE TABLE forecasts (
  run_id BIGINT, tick INT, station_id TEXT, fuel_type TEXT,
  model_version TEXT NOT NULL, horizon INT NOT NULL,
  mu REAL[] NOT NULL, sigma REAL[] NOT NULL, confidence REAL NOT NULL,
  PRIMARY KEY (run_id, tick, station_id, fuel_type)
);

CREATE TABLE risk_scores (
  run_id BIGINT, tick INT, station_id TEXT, fuel_type TEXT,
  inventory REAL, inbound_h REAL, demand_h REAL,
  first_unmet_tick_p50 INT, first_unmet_tick_p10 INT, p_shortage REAL, expected_unmet REAL,
  priority REAL, level TEXT CHECK (level IN ('LOW','MEDIUM','HIGH','CRITICAL','OUTAGE','UNREACHABLE')),
  confidence TEXT CHECK (confidence IN ('HIGH','MEDIUM','LOW')), scenarios INT, gate_open BOOLEAN,
  mode TEXT CHECK (mode IN ('batch','incremental')),
  PRIMARY KEY (run_id, tick, station_id, fuel_type)
);
CREATE INDEX ON risk_scores (run_id, tick DESC, priority DESC);

CREATE TABLE recommendations (
  id              UUID PRIMARY KEY,
  run_id          BIGINT REFERENCES runs(id),
  created_tick    INT NOT NULL,
  valid_until_tick INT NOT NULL,
  status          TEXT NOT NULL,  -- PROPOSED|APPROVED|SUBMITTED|PARTIAL|DONE|REJECTED|EXPIRED|SUPERSEDED|SUBMIT_FAILED
                                  -- NOTE: pipeline.md §3.4 extends the lifecycle with SUBMITTING, RECONCILING,
                                  -- ACCEPTED, NEEDS_REVIEW and TERMINAL states. Use pipeline.md as the
                                  -- authoritative state machine when implementing.
  policy_used     TEXT NOT NULL,  -- lp|greedy|hold|manual
  model_version   TEXT NOT NULL,
  policy_version  TEXT NOT NULL,
  snapshot_hash   TEXT NOT NULL,
  needs_review    BOOLEAN NOT NULL,
  confidence      REAL NOT NULL,
  impact          JSONB NOT NULL, -- before/after risk per key, expected unmet delta
  explanation     JSONB NOT NULL, -- signals, constraints, alternatives, text
  decided_by      TEXT,
  decided_at      TIMESTAMPTZ
);
CREATE INDEX ON recommendations (run_id, status, created_tick DESC);

-- Outbox + ledger of our submissions to POST /v1/allocations
-- NOTE: pipeline.md §9 renames this table to `allocation_intents` and extends the
-- status enum to SUBMITTING|ACCEPTED|REJECTED|RECONCILING|NEEDS_REVIEW|EXPIRED.
-- It also adds a `body_hash` column for canonical body hash (A4).
-- Use pipeline.md as authoritative when implementing the migration.
CREATE TABLE allocation_intents (
  id                 BIGSERIAL PRIMARY KEY,
  recommendation_id  UUID REFERENCES recommendations(id),
  leg                INT NOT NULL,
  idempotency_key    TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 150),
  body_hash          TEXT NOT NULL,  -- canonical body hash (pipeline.md A4)
  source_depot_id    TEXT NOT NULL,
  destination_station_id TEXT NOT NULL,
  route_id           TEXT NOT NULL,
  fuel_type          TEXT NOT NULL,
  quantity           NUMERIC(12,3) NOT NULL CHECK (quantity > 0),
  status             TEXT NOT NULL, -- SUBMITTING|ACCEPTED|REJECTED|RECONCILING|NEEDS_REVIEW|EXPIRED
  attempts           INT NOT NULL DEFAULT 0,
  last_error_code    TEXT,
  sim_allocation_id  BIGINT,
  sim_status         TEXT,          -- PENDING|IN_TRANSIT|ARRIVED|FAILED|CANCELLED
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON allocation_intents (status) WHERE status = 'SUBMITTING';

CREATE TABLE alerts (
  id BIGSERIAL PRIMARY KEY, run_id BIGINT, tick INT, kind TEXT, severity TEXT,
  subject JSONB, message TEXT, opened_at TIMESTAMPTZ DEFAULT now(), closed_at TIMESTAMPTZ
);
CREATE INDEX ON alerts (run_id, closed_at NULLS FIRST, severity);

CREATE TABLE audit_log (
  id UUID PRIMARY KEY, at TIMESTAMPTZ NOT NULL DEFAULT now(), run_id BIGINT, tick INT,
  actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT, entity_id TEXT, payload JSONB
);

CREATE TABLE model_versions (
  version TEXT PRIMARY KEY, kind TEXT, artifact_uri TEXT, metrics JSONB,
  created_at TIMESTAMPTZ DEFAULT now(), active BOOLEAN DEFAULT false
);

CREATE TABLE policy_versions (
  version TEXT PRIMARY KEY, mode TEXT CHECK (mode IN ('MANUAL','ASSISTED','AUTO')),
  params JSONB NOT NULL, created_at TIMESTAMPTZ DEFAULT now(), active BOOLEAN DEFAULT false
);

CREATE TABLE directives (
  id UUID PRIMARY KEY, run_id BIGINT REFERENCES runs(id),
  type TEXT NOT NULL CHECK (type IN ('depot_reserve','station_min_level','avoid_route','cap_dispatch','prioritize','no_op')),
  params JSONB NOT NULL, source_note TEXT, interpreted_by TEXT,  -- 'structured' | '<provider>:<model>'
  from_tick INT, to_tick INT, status TEXT NOT NULL,              -- ACTIVE|EXPIRED|REVOKED
  created_by TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON directives (run_id, status);

CREATE TABLE kpi_samples (
  run_id BIGINT, tick INT, served REAL, unmet REAL, service_level REAL,
  allocation_liters REAL, allocation_failures INT, PRIMARY KEY (run_id, tick)
);
```

### 5.2 Query patterns

| Use | Query | Index |
|---|---|---|
| DB fallback for snapshot | `SELECT body, tick FROM snapshots WHERE run_id=$1 ORDER BY tick DESC LIMIT 1` | PK |
| Training window | `demand_observations WHERE run_id=$1 AND station_id=$2 AND fuel_type=$3 AND tick > $4` | `(run_id, station_id, fuel_type, tick DESC)` |
| Risk fallback | `risk_scores WHERE run_id=$1 AND tick=(SELECT max(tick) ...) ORDER BY priority DESC` | `(run_id, tick DESC, priority DESC)` |
| Outbox scan | `allocation_intents WHERE status='SUBMITTING' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 20` | partial index |
| Decision history | `recommendations WHERE run_id=$1 ORDER BY created_tick DESC LIMIT $2` keyset by `(created_tick, id)` | `(run_id, status, created_tick DESC)` |

All writes from the worker are batched per tick (`executemany` / `COPY` for demand rows). Connection pool: `asyncpg` via SQLAlchemy 2, `pool_size=10` per process, `statement_timeout=2s`.

### 5.3 Retention

`snapshots` and `risk_scores`: keep every tick of the current run and every 4th tick of previous runs; purge runs older than 7 days (nightly job in the worker). `demand_observations` kept (small: 12 rows/tick ≈ 1,152 rows per simulated day).

## 6. Intelligence and scoring

**Chosen approach (Shila, 2026-09-29):** a profile-based demand forecast with guarded adaptation, uncertainty from empirical out-of-sample errors, tick-by-tick inventory accounting, an allocation planner that asks "which feasible plan prevents the most unmet liters?", and a counterfactual decision-comparison panel. Pitch: *we predict which stations will run out, compare feasible responses under uncertainty, explain the trade-off, and measure whether the decision worked.*

Notation: key `k = (station_id, fuel_type)`, 12 keys. `t0` = current tick. Operational horizon `H = 16` ticks (4 simulated hours; test whether longer changes decisions). Depot supply outlook `H_s` = ticks until the next supply arrival at each depot (recurring spacing is 64 ticks). `M` = number of demand scenarios (100–300, tuned for runtime and coverage).

### 6.1 Forecast

**Evidence behind the choice.** A read-only check of the live simulator (baseline, seed 12345, 458 ticks, 5,496 demand records) compared four forecasts on cumulative future demand, after a 96-tick warmup and with chronological origins (aggregate WAPE; lower is better):

| Forecast | 15 min | 1 h cumulative | 4 h cumulative |
|---|---:|---:|---:|
| Published demand profile | **5.06%** | **2.41%** | **1.26%** |
| Profile + always-on correction (α = 0.2) | 5.23% | 2.83% | 2.00% |
| EWMA demand level | 14.06% | 15.73% | 25.51% |
| Same tick previous day | 6.71% | 3.43% | 1.87% |

Observed/structural ratio averaged 0.9994 (range ≈ 0.88–1.12). This is one seed, ~4.8 simulated days, no events, and overlapping windows. It supports the choice; it is not evidence about crises.

**Champion: the structural profile (c = 1).**

```
b[s,f,t] = B[p(s), f] / 96 × r[s] × g[p(s)](t) × m[s,t]
```

`B` is the documented daily demand, `r` the region `demand_factor`, `g` the hour-of-day factor, and `m` the station's live `demand_multiplier`, carried forward unchanged over the horizon unless a publicly listed scheduled event says otherwise (the assumption is recorded on the forecast). If `m` already reflects a `demand_spike`, the event is not applied a second time. Normalization, tick-boundary convention and the noise distribution are verified by contract tests against observed demand (Phase 0), not assumed from the guide's tables.

**Challenger: guarded adaptive correction.** One correction per key:

```
c_t      = (1 − α)·c_{t−1} + α · d_t / max(b_t, ε)          c_0 = 1
d̂_{t+h} = max(0, b_{t+h} · c_used)
c_used   = 1                   while the adaptation gate is closed
         = c_t (or a blend)    only after persistent bias opens the gate
```

- The gate opens only on persistent bias: the mean of the last `W` standardized one-step errors has the same sign and exceeds a threshold. Always-on adaptation was worse on the baseline because it follows noise.
- α ∈ {0.1, 0.2, 0.4}, the gate threshold and `W` are tuned by rolling-origin validation on separate normal and crisis replays. These are proposed settings, not established optima.
- On an unexplained shift detected by §6.7, the gate opens with a shorter window and uncertainty widens. On a *known* event (the multiplier changed), the gate stays closed because the structure already explains it.
- Near-zero baselines (`b_t < ε`) skip the ratio update and use the profile.

**Targets and data hygiene.** Train and evaluate on `demand_liters`, never `served_liters` (served demand is censored by stockouts). Inventory is not a demand feature; it belongs to the decision model. Only information available at decision time is used.

**History.** `/v1/demand-history` returns at most 2,000 rows, about 41.7 simulated hours when unfiltered. The worker polls per `station_id` incrementally, verifies ordering, de-duplicates by `id`, and keeps its own full history in `demand_observations`.

**Fallback ladder** (confidence label drops at each step): profile champion → seasonal naive (same tick previous day, once ≥ 96 ticks exist) → EWMA level → "no forecast, needs review".

**Optional ML challenger (P2).** One pooled gradient-boosted tree model across all 12 series (station/fuel identity, profile, time of day, demand lags, rolling demand, observed multipliers; quantile regression optional). It is kept only if it improves *decision outcomes* on unseen windows and crisis replays. Given the published structure and little data, it may not win. Not pursued: ARIMA/Prophet (fitting work with no clear advantage), LSTM/Transformer (12 short series), RL (validation burden before beating a strong heuristic).

Forecast arrays for all 12 keys × max(H, H_s) are precomputed each tick and cached (`fc:*`). Recompute on a new tick, a multiplier change, or a gate change.

### 6.2 Uncertainty

- **Errors used:** only out-of-sample errors (forecasts made before the outcome was observed), stored per key and per horizon `h`. Training residuals are never presented as future uncertainty.
- **Bands:** horizon-specific empirical error quantiles (p10/p50/p90) around `d̂`, shown on charts.
- **Scenarios:** `M` demand trajectories built by a residual-block bootstrap. Short blocks of recent forecast errors are sampled *aligned across stations* (preserving shared regional shocks and time dependence), added to `d̂`, and clipped at 0. Block length and `M` are tuned from runtime and coverage checks.
- **Insufficient history:** fewer than `N_min` out-of-sample errors → no percentages; show *low / medium / high* demand scenarios (profile × 0.9 / 1.0 / 1.12, matching the observed ratio range) and label confidence LOW.
- **Labelling:** every probability shown is "model-estimated risk". The simulator is deterministic, but our forecast is not.

### 6.3 Stockout prediction: tick-by-tick inventory accounting

For each key, each scenario `j` and each future tick `h = 1..H`:

```
A_h = arrivals at h: existing PENDING/IN_TRANSIT shipments (counted once) + proposed legs
V_h = I_{h−1} + A_h                         (capped at station capacity)
S_h = min(D_h, V_h)       (S_h = 0 while the station is OUTAGE)
U_h = D_h − S_h
I_h = V_h − S_h
```

Arrival-versus-demand ordering within a tick and capacity behaviour are matched to the engine by contract tests. Outputs per key (mean across scenarios and per-scenario):

| Output | Definition |
|---|---|
| `first_unmet_tick` | first `h` with `U_h > 0` (per scenario; report median and p10) |
| `expected_unmet` | mean over scenarios of `Σ_h U_h` |
| `p_shortage` | `(1/M) Σ_j 1[Σ_h U_h^(j) > 0]`: model-estimated risk |
| `inventory_at_delivery` | `I` at each proposed leg's arrival tick |
| `level` | CRITICAL if `p_shortage ≥ 0.8` or `first_unmet_tick ≤ lead_min`; HIGH ≥ 0.5; MEDIUM ≥ 0.2; else LOW. Plus OUTAGE and UNREACHABLE |
| `priority` | `expected_unmet` in liters (the direct objective; no hand-tuned weights) |

A shipment arriving *after* the first shortage can still prevent later unmet demand. Legs are rejected only for zero benefit (`ARRIVES_TOO_LATE_OR_NO_BENEFIT` means no reduction in `expected_unmet`), not for arriving after the first stockout.

Depot stock reported by the simulator already reflects PENDING/IN_TRANSIT dispatches, so reservations are not subtracted a second time.

### 6.4 Batch vs incremental evaluation

Both modes call the same vectorised kernel `project(keys, snapshot, forecasts, scenarios, plan) → Trajectories`, over NumPy arrays shaped `[M, n_keys, H]` (with M = 200, 12 keys and H = 16 that is 38,400 cells, microseconds per pass):

- **Batch** passes all 12 keys. Used on startup, reset, SSE reconnect, stale recovery, forecast/gate change, planner candidate evaluation, and the replay/benchmark harness (stacked over ticks for backtests).
- **Incremental** passes only dirty keys from the snapshot diff: inventory changed; an allocation was created or changed status (its destination key); the multiplier or station status changed (the station's 3 keys); route status changed (keys of the destination station); forecast or gate changed. Depot changes affect planning only. Scenario error blocks are reused between ticks unless new errors arrived, so the common per-tick update touches one key's trajectory.
- Every 16th tick a batch pass runs and asserts equality with the incremental state (`scoring_drift_total`; batch wins on mismatch). The scenario RNG is seeded from `(epoch, tick)` so both modes, and replays, are reproducible.

### 6.5 Allocation engine

Planning is rolling-horizon (model predictive control): optimise shipments over the horizon, submit only the actions that depart now, then refresh and re-plan on the next tick or disruption.

**Constraints.** These mirror the simulator's validation order (§5.2 of the guide) and are reserved *jointly* across all legs of a plan, because individually valid legs can form an invalid batch:

| Simulator rule | Planner constraint |
|---|---|
| route must match `(source_depot_id, destination_station_id)` | legs are generated only from `/v1/routes` rows |
| `DEPOT_CLOSED` | depot status ∈ {OPEN, CONSTRAINED}; CONSTRAINED applies `CONSTRAINED_DISPATCH_FACTOR` (default 0.5, assumption, see PRD §11) |
| `STATION_CLOSED` | station status = OPEN |
| `ROUTE_DISRUPTED` | route status = AVAILABLE |
| `ROUTE_CAPACITY_EXCEEDED` | each leg ≤ `route.max_shipment`; larger flows split |
| `INSUFFICIENT_INVENTORY` | Σ legs from depot for fuel ≤ `depot.inventory[fuel] − reserve[depot, fuel]` |
| `DISPATCH_CAPACITY_EXCEEDED` | Σ legs from depot (all fuels) + already pending/in-flight on this tick ≤ effective dispatch capacity |
| `DESTINATION_CAPACITY_EXCEEDED` | the API validates against *current* station inventory: `station.inventory[fuel] + Σ pending/in-transit to key + x ≤ capacity[fuel]`, even if projected consumption would make room later |

- **Lead time** = departure wait + `transit_ticks` + any observed shipment delay; these are kept separate in the explanation.
- **Topology:** Mirpur and Karnaphuli have alternative depot routes; Tongi and Cox's Bazar have one route each. Rerouting is never promised where no alternative exists.
- **Reserves:** `reserve[depot, fuel]` comes from the depot supply outlook. The planner projects each depot's stock until its next `SCHEDULED`/`DELAYED` arrival (`H_s`), so a 4-hour plan does not create scarcity just beyond its horizon.

**Replenishment quantity (used by greedy candidates and the reactive baseline):**

```
q = [ Q_τ( D_{1:L+R} ) − I_0 − A_{1:L+R} ]₊
```

`L` = lead time, `R` = time to the next decision review, and `Q_τ` = the τ-quantile of *cumulative* demand over `L+R`, taken from the scenario set (not a sum of per-tick quantiles). Service targets τ ∈ {0.8, 0.9, 0.95} are compared in the benchmark, not asserted. The result is clamped by every constraint.

**Primary: rolling-horizon LP (HiGHS).** Continuous variables are shipment liters per (route, fuel, departure tick ≤ H), with station and depot stock balances per tick (the §6.3 recursion as linear constraints) on the scenario-mean demand. The objective is lexicographic, solved as a short sequence of LPs:

1. minimise total projected unmet liters across the network;
2. within a tolerance of the optimum in step 1, maximise the worst station's projected service level;
3. break remaining ties by fewer transferred liters, then shorter transit.

Fairness (step 2) is a team policy, and transit and volume are operational proxies; there is no price model in the documented metrics. No integer variables unless a meaningful discrete dispatch decision is added, and no vehicle routing (the simulator supplies the routes). Time limit 250 ms per solve; the problem is ~6 routes × 3 fuels × 16 ticks ≈ 300 variables, solved in milliseconds. Only departure-tick-0 legs are submitted.

**Fallback: marginal-benefit greedy.**

```
plan = {}                                  # "send nothing" is always a candidate
loop:
    for each feasible (route, fuel) under remaining joint constraints:
        Δ = expected_unmet(plan) − expected_unmet(plan + increment)   # via §6.3 kernel, all scenarios
        score = Δ / scarce_resource_consumed(increment)                # depot stock or dispatch, whichever binds
    pick the best; stop if best Δ ≤ 0
    add a bounded increment (≤ min(max_shipment, INCREMENT_LITERS)); update reservations
merge increments per route/fuel into legs
```

Network benefit is recomputed after every choice, which handles diminishing returns better than scoring legs once. It is a heuristic and is never called optimal. It is also the "current baseline policy" row of the comparison panel when the LP is used, and vice versa.

**Stretch: tail-risk (CVaR) objective.** Once the ordinary LP is validated, the same fixed candidate plans are evaluated across all scenarios with `mean(L_j) + λ·CVaR_β(L)` (β = 0.9 initially; λ benchmarked, not chosen for presentation). If future actions are optimised per scenario, current actions are constrained identical across scenarios (non-anticipativity).

**Resource sensitivity (stretch).** Re-run the planner with a small extra dispatch allowance per depot and report the projected unmet liters avoided. This answers "what is the actual bottleneck?"

#### 6.5.1 Independent replay validator

This pattern comes from the preliminary round, where every schedule was independently replayed before HTTP 200 was returned. Here, `intelligence.validate.replay(plan, snapshot, directives)` is a separate implementation that takes the serialized plan (the exact legs that would be POSTed) and checks:

- each leg against the simulator's validation order (§5.2 of the guide), in order, reporting the code the simulator would return;
- joint conservation across legs: per depot and fuel, Σ quantity ≤ inventory − reserve; per depot, Σ quantity plus existing pending and in-flight ≤ effective dispatch capacity; per station and fuel, current inventory plus inbound plus Σ quantity ≤ capacity;
- every active operator directive (§6.9);
- leg quantity is > 0, is at most `max_shipment`, is at least `MIN_LEG_LITERS`, and has a unique idempotency key of at most 150 characters;
- recomputed impact: it re-runs the §6.3 projection with the same seeded scenarios and requires the stored `impact` to match within 1e-6 and `expected_unmet(plan) ≤ expected_unmet(no new shipment)`.

It runs when a plan is produced (before `PROPOSED`), at approval or auto-submit time against the then-current snapshot, and again after every accepted submission for the remaining legs (a multi-leg plan is not atomic). A failure is never "fixed up" silently: the plan is discarded, `replay_rejections_total{reason}` increments, and the next fallback (greedy, then hold) is validated instead.

**Confidence and review.** `rec.confidence` = the lowest label of the affected keys (HIGH/MEDIUM/LOW from out-of-sample error coverage, freshness and fallback use). `needs_review = LOW or snapshot.stale or policy_used = 'hold'`.

**Validity.** `valid_until_tick = created_tick + 2`. A newer plan for the same keys marks older `PROPOSED` ones `SUPERSEDED`.

### 6.6 Handling simulator responses on submission

| Response | Action |
|---|---|
| 201 Created / 200 (idempotent replay) | `SUBMITTED`; store `sim_allocation_id`; track via SSE `allocation.status_changed` + `/v1/allocations` |
| 404 `NOT_FOUND` | Our topology cache is wrong → force batch resync; leg `REJECTED`; alert |
| 409 `IDEMPOTENCY_KEY_MISMATCH` | Bug signal (key reuse with different body) → leg `REJECTED`, alert, never retry with the same key |
| 409 `ROUTE_MISMATCH` | Topology bug → resync, re-plan |
| 409 `DEPOT_CLOSED` / `STATION_CLOSED` / `ROUTE_DISRUPTED` | State changed since planning → dirty keys, re-plan (alternative route if one exists) |
| 409 `ROUTE_CAPACITY_EXCEEDED` | Split leg and resubmit with new keys `…-{leg}a`, `…-{leg}b` |
| 409 `INSUFFICIENT_INVENTORY` | Refresh depot, re-plan with lower quantity |
| 409 `DISPATCH_CAPACITY_EXCEEDED` | Keep leg `QUEUED` for next tick (new key suffix `-t{tick}`) if still within validity |
| 409 `DESTINATION_CAPACITY_EXCEEDED` | Reduce quantity to headroom or drop the leg |
| 422 | Client bug → reject, alert, include body in log |
| 503 `FAULT_INJECTED` / timeout | Retry with same key (3×), then outbox |

Idempotency key format: `fo-{epoch}-{rec_id_short}-v{version}-{leg}[-suffix]`, always ≤ 150 chars, with the canonical body hash stored beside it. Editing a quantity creates a new recommendation version and therefore a new key. Since cancellation does not free a key, a re-submission after cancel always gets a new suffix.

### 6.7 Shock detection

- **Demand shock:** robust standardized one-step error `r_t = (d_t − d̂_{t|t−1}) / max(σ_robust, ε)`, where `σ_robust` = 1.4826 × MAD of recent out-of-sample errors. Alert on two consecutive unusually positive values (threshold tuned on normal vs shock replays, starting at 3). If `/v1/events` shows an `ACTIVE` event, or the multiplier changed, that explains it: label the alert **"known demand event"** and do not claim independent discovery. If it is unexplained: `DEMAND_ANOMALY` alert, open the adaptation gate with a shorter window, widen uncertainty, recompute recommendations. Detection delay is measured in the benchmark.
- **Inventory anomaly:** station inventory change not explained by `served_liters` and arrivals (tolerance 1%) → `INVENTORY_ANOMALY`.
- **Supply delay / shortfall:** supply arrival `status=DELAYED`, `planned_tick` increased or `quantity` decreased → `SUPPLY_RISK`, which feeds the depot outlook and re-planning.
- **Bottleneck:** depot dispatch utilisation > 90% for 4 ticks, or route `DISRUPTED` with no alternative → `BOTTLENECK`.
- **Stale input:** freeze execution and show the reason; a statistical fallback cannot make stale inventory safe to act on.

### 6.8 Explanations: the decision comparison panel

Every recommendation carries a comparison computed with the §6.3 kernel on the same seeded scenarios:

| Plan | Projected unmet liters | Model-estimated shortage risk | Binding constraint | Why selected / rejected |
|---|---:|---:|---|---|
| No new shipment | computed | computed (or low/med/high label) | none | reference case |
| Current baseline policy (reactive threshold, or greedy when LP is primary) | computed | computed | computed | comparison |
| **Recommended plan** | computed | computed | computed | best feasible measured objective |
| Runner-up | computed | computed | computed | the exact trade-off |

The UI shows inventory trajectories before and after the action with an arrival marker and uncertainty bands. It also has **hypothetical** what-if controls (e.g. "demand +30%", "alternate route unavailable"), clearly labelled, that re-run the planner live through `POST /recommendations/{id}/simulate` without touching the simulator.

Explanations are generated from structured facts (reason codes, binding constraints, numbers). Statements such as *"We kept some fuel at this depot because another station has no alternative route"* appear only when a numerical comparison (plan with vs without that reserve) supports them. The brief's §9 alert format is rendered from the same facts. The optional LLM adapter (P2) only rewords the template through the LLM gateway (§4.4); it never invents quantities, constraints or confidence, and its output is checked to contain the template's numbers.

Local counterfactuals are model estimates. Evidence of policy quality comes only from replaying policies against identical reset conditions and event schedules in the real simulator (§10.4); there is no state-cloning endpoint, so live branching is not assumed.

### 6.9 Operator directives (LLM-interpreted, deterministically enforced)

This adapts the preliminary round's architecture ("notes → typed directives → guardrails → compiler → LP") to allocation.

```
POST /api/v1/directives {notes: [...]}
  → bounded body + queue + deadline
  → LLM gateway: one structured request covering all notes; each note maps to exactly one type
  → schema + guardrails ──invalid──► one repair attempt ──invalid──► 422 (nothing stored)
  → compiler → per-tick constraint arrays
  → stored as ACTIVE directives (versioned, audited) → planner and replay validator both enforce them
```

**Closed set of directive types**

| type | parameters | compiled into |
|---|---|---|
| `depot_reserve` | `depot_id`, `fuel_type`, `liters` | `reserve[depot, fuel] = max(reserve, liters)` |
| `station_min_level` | `station_id`, `fuel_type`, `liters` | raises the target level in greedy, adds a penalty on falling short in the LP |
| `avoid_route` | `route_id`, `from_tick`, `to_tick` | route treated as unavailable in that window |
| `cap_dispatch` | `depot_id`, `max_liters_per_tick`, window | tighter dispatch capacity |
| `prioritize` | `station_id` or `region_id`, `fuel_type?`, `weight` (1–3) | multiplier on priority |
| `no_op` | none | note acknowledged, nothing applied |

**Guardrails** check that every note is covered in order with exactly one directive, that entity ids exist in the current topology, that fuel types are in the enum, that liters are positive and within the capacity of the referenced entity, that time windows are whole ticks with `from < to` (sim times from the note converted using `tick_minutes`), and that no two directives contradict each other. For example, `avoid_route` on the only available route into a station while that station has a `station_min_level` gets rejected with an explanation. The LLM output only selects types and fills in parameters. All arithmetic, including time-to-tick conversion, is done deterministically in the compiler.

Directives expire at `to_tick` or on reset, can be deleted by an operator, and are shown in every recommendation explanation that they affected. The whole feature is optional: with no LLM configured, the same directives can be created through the structured `POST /api/v1/directives/structured` form, which runs the same guardrails.

## 7. API contract

Base path `/api/v1`. JSON via `orjson`. Auth: `AUTH_MODE=off` locally; when `AUTH_MODE=apikey` (forced for `APP_ENV=public`), the `X-API-Key` header maps to a role (`viewer`, `operator`, `admin`).

### 7.1 Envelopes

```json
// success
{
  "data": { ... },
  "meta": {
    "run_id": 3, "tick": 128, "sim_time": "2026-01-02T08:00:00+00:00",
    "as_of": "2026-09-29T04:10:11.201Z",
    "source": "cache",            // cache | l1 | db | live
    "stale": false,
    "degraded": []                // e.g. ["simulator:circuit_open", "redis:down", "forecast:fallback"]
  }
}
// error
// error: RFC 9457 application/problem+json
{ "type": "https://fuelops/errors/rec-invalidated", "title": "Recommendation invalidated", "status": 409,
  "code": "REC_INVALIDATED", "detail": "Route route-gazipur-mirpur is DISRUPTED",
  "correlation_id": "...", "retryable": false, "field_errors": [], "upstream_code": "ROUTE_DISRUPTED" }
```

Upstream simulator codes are passed through in `upstream_code` (e.g. `ROUTE_DISRUPTED`).

### 7.2 Endpoints

| Method | Path | Role | Purpose | Served from |
|---|---|---|---|---|
| GET | `/healthz` | none | liveness | process |
| GET | `/readyz` | none | readiness (§9.3) | process + deps |
| GET | `/metrics` | internal | Prometheus | process |
| GET | `/api/v1/system/status` | viewer | component health, p95, error rate | `hb:*` + metrics |
| GET | `/api/v1/network/snapshot` | viewer | full current state | `sim:snapshot` |
| GET | `/api/v1/stations`, `/stations/{station_id}` | viewer | inventory, capacity, status, risk per fuel | `view:*` |
| GET | `/api/v1/depots`, `/depots/{depot_id}` | viewer | inventory, dispatch utilisation, reserve | `view:*` |
| GET | `/api/v1/routes` | viewer | status, transit, max_shipment | `view:routes` |
| GET | `/api/v1/supply-arrivals` | viewer | inbound supply incl. delays | `view:supply` |
| GET | `/api/v1/events` | viewer | crisis events with detected impact | `view:events` |
| GET | `/api/v1/risk?level=HIGH&fuel_type=DIESEL` | viewer | ranked risk scores | `risk:rank` + `risk:*` |
| GET | `/api/v1/forecasts/{station_id}?fuel_type=&horizon=` | viewer | μ/σ series, model version | `fc:*` |
| GET | `/api/v1/alerts?open=true` | viewer | alerts | DB (small, cached 1 tick) |
| GET | `/api/v1/recommendations?status=PROPOSED` | viewer | active recommendations | `rec:active` |
| GET | `/api/v1/recommendations/{id}` | viewer | detail + explanation | `rec:{id}` |
| POST | `/api/v1/recommendations/{id}/simulate` | viewer | what-if, optional quantity overrides | computed in API (pure kernel) |
| POST | `/api/v1/recommendations/{id}/approve` | operator | approve + submit | DB + simulator |
| POST | `/api/v1/recommendations/{id}/reject` | operator | reject with reason | DB |
| POST | `/api/v1/allocations` | operator | manual allocation (validated by planner constraints first) | DB + simulator |
| POST | `/api/v1/allocations/{allocation_id}/cancel` | operator | cancel `PENDING` | simulator |
| GET | `/api/v1/allocations` | viewer | ledger: our submissions joined with simulator status | `view:allocations` |
| GET | `/api/v1/decisions?limit=&cursor=` | viewer | decision audit history | DB |
| GET | `/api/v1/kpi` | viewer | service level, unmet, failures, trend | `kpi:current` |
| GET/PUT | `/api/v1/policy` | viewer / admin | automation mode, thresholds, model version | DB |
| POST | `/api/v1/directives` | operator | interpret 1–5 natural-language notes into directives (LLM + guardrails) | LLM gateway + DB |
| POST | `/api/v1/directives/structured` | operator | create directives without the LLM (same guardrails) | DB |
| GET / DELETE | `/api/v1/directives[/{id}]` | viewer / operator | list active, revoke | DB (cached 1 tick) |
| GET | `/api/v1/stream` | viewer | SSE: `tick`, `risk.updated`, `recommendation.created/updated`, `alert.opened/closed`, `degraded`, `resync` | Redis pub/sub |
| POST | `/api/v1/test/simulator/{run|pause|step|reset|events|faults|faults-clear}`, `/api/v1/test/scenarios/{name}/run` | admin (or local) | test plane: allowlisted proxy to simulator `/admin/*`; router registered only when `APP_ENV ∈ {local,test,demo}` and `ENABLE_SIMULATOR_TEST_CONTROLS=true` | simulator |
| GET | `/api/v1/capabilities` | viewer | which optional features (test plane, directives, ASSISTED) are enabled | config |

### 7.3 Key shapes

**Risk item**

```json
{
  "station_id": "station-mirpur", "fuel_type": "DIESEL", "level": "HIGH",
  "inventory": 8400, "capacity": 15000, "inbound_h": 0,
  "expected_demand_h": 11900, "demand_band_h": [11100, 12700],
  "first_unmet_tick": { "p50": 25, "p10": 22 }, "hours_to_first_unmet": 6.25,
  "p_shortage": 0.72, "p_shortage_label": "model-estimated", "expected_unmet": 3500.4, "priority": 3500.4,
  "confidence": "MEDIUM", "forecast": { "model": "profile-v1", "gate_open": false, "multiplier_assumed": 1.8 },
  "signals": ["known demand event: demand_spike #1 (multiplier 1.8)"]
}
```

**Recommendation**

```json
{
  "id": "7f1c…", "status": "PROPOSED", "created_tick": 128, "valid_until_tick": 130,
  "policy_used": "lp", "model_version": "profile-v1", "confidence": 0.81, "needs_review": false,
  "legs": [
    { "leg": 0, "source_depot_id": "depot-patiya", "destination_station_id": "station-mirpur",
      "route_id": "route-patiya-mirpur", "fuel_type": "DIESEL", "quantity": 5000,
      "expected_arrival_tick": 133 }
  ],
  "impact": { "station-mirpur:DIESEL": { "p_shortage": [0.72, 0.19], "expected_unmet": [3500.4, 410.2] } },
  "comparison": [ { "plan": "no_new_shipment", "unmet": 3500.4 }, { "plan": "baseline_greedy", "unmet": 820.0 },
                  { "plan": "recommended_lp", "unmet": 410.2, "binding": "route_max_shipment" }, { "plan": "runner_up", "unmet": 505.9 } ],
  "explanation": {
    "summary": "Mirpur Diesel projected to stock out in 6.2 h; route-gazipur-mirpur is DISRUPTED so Patiya supplies via route-patiya-mirpur.",
    "constraints": ["route-gazipur-mirpur DISRUPTED", "depot-gazipur dispatch 11,000/12,000 used"],
    "alternatives": [ { "policy": "greedy", "legs": [...], "impact": {...} } ]
  }
}
```

**Approve request/response**

```json
// POST /api/v1/recommendations/{id}/approve
{ "quantity_overrides": { "0": 4500 }, "note": "keep buffer at Patiya" }

// 200
{ "data": { "id": "7f1c…", "status": "SUBMITTED",
            "legs": [ { "leg": 0, "idempotency_key": "fo-3-7f1c9a-0", "status": "SUBMITTED",
                        "sim_allocation_id": 42, "sim_status": "PENDING" } ] },
  "meta": { ... } }
// 202 when any leg is queued in the outbox; 409 REC_EXPIRED | REC_INVALIDATED | REC_NOT_PROPOSED
```

**System status**

```json
{
  "components": {
    "backend_api": "healthy", "database": "healthy", "redis": "healthy",
    "fuel_simulator": "degraded", "simulator_stream": "polling",
    "prediction_service": "fallback", "decision_engine": "healthy", "worker": "healthy"
  },
  "p95_latency_ms": 164, "error_rate": 0.004,
  "tick_lag": 1, "snapshot_age_ticks": 1, "active_faults_detected": ["error_rate"],
  "policy_mode": "MANUAL", "overall": "degraded",
  "blocked_actions": [ { "action": "approve", "reason": "STATE_STALE: X-Simulator-Stale seen at tick 131" } ]
}
```

## 8. Concurrency and performance design

- **Runtime:** Uvicorn with `uvloop` + `httptools`, `API_WORKERS = CPU count` per `api` container (2 containers × 2 workers on 4 vCPU). Worker process is a single asyncio loop; CPU-bound model inference and LP run in a `ProcessPoolExecutor(max_workers=1)` with timeouts so they never block ingest.
- **HTTP client:** one shared `httpx.AsyncClient` per process with HTTP keep-alive, `max_connections=20`, explicit timeouts. SSE uses a separate client without read timeout (liveness via 45 s silence watchdog).
- **Redis:** `redis-py` asyncio with a connection pool (`max_connections=50` per process), 50 ms socket timeout on the read path, pipelining for per-tick writes.
- **Serialization:** worker pre-renders JSON; API returns bytes. No per-request Pydantic model construction on hot reads.
- **Backpressure:** per-process in-flight limit and shedding (§4.4), per-API-key rate limit (default 50 req/s viewer, 10 req/s writes), bounded SSE client queues.
- **Heavy-route bounds (from the preliminary round):** a body-size middleware rejects anything over `MAX_REQUEST_BODY_BYTES` before parsing. `simulate`, `approve` and `directives` go through a per-process `asyncio.Semaphore(MAX_CONCURRENT_HEAVY)` with a bounded wait queue (`MAX_QUEUED_HEAVY`); a full queue returns 503 immediately. Each request carries a deadline from the moment it is queued (`TOTAL_REQUEST_DEADLINE_MS`), and every downstream timeout (simulator, LLM, solver) is capped at the remaining budget. Health and read routes never enter these queues.
- **Response headers:** `Cache-Control: no-cache` plus ETag on reads (revalidate, never serve stale from an intermediary), `no-store` on writes, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`. HTTP clients are closed on shutdown via the FastAPI lifespan.
- **Budget per tick (worker, healthy simulator):** refresh ≤ 150 ms p95, score ≤ 5 ms incremental / 50 ms batch, plan ≤ 100 ms, persist + publish ≤ 20 ms. Coalescing absorbs anything slower.

## 9. Deployment model

### 9.1 Infrastructure assumptions

Single host (laptop or VM, 4 vCPU / 8 GB), Docker Engine 24+ with Compose v2. Simulator runs in the same Compose project. The only cloud dependency is Neon (managed PostgreSQL, reached over TLS); if it is unreachable the system stays readable and blocks writes (pipeline.md §7). Optional: the same Compose file runs on a small cloud VM for the demo.

### 9.2 Compose topology (abridged)

```yaml
services:
  simulator-api:
    image: asifmahmoud414/bup-fuel-supply-simulator:1.0.0
    environment:
      SIMULATION_SPEED: ${SIMULATION_SPEED:-8}
      TICK_MINUTES: ${TICK_MINUTES:-15}
      SIMULATOR_START_MODE: ${SIMULATOR_START_MODE:-paused}
    ports: ["8000:8000"]
    healthcheck: { test: ["CMD", "curl", "-fs", "http://localhost:8000/v1/health"], interval: 5s, retries: 12 }

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "volatile-lru", "--appendonly", "no"]
    healthcheck: { test: ["CMD", "redis-cli", "ping"], interval: 5s }

  # Database: Neon (managed). No postgres service; DATABASE_URL (pooled) and
  # DATABASE_URL_DIRECT (migrations) come from .env / CI secrets.

  migrate:
    image: ${FUELOPS_IMAGE}:${FUELOPS_TAG}
    command: ["alembic", "upgrade", "head"]
    env_file: .env   # uses DATABASE_URL_DIRECT

  worker:
    image: ${FUELOPS_IMAGE}:${FUELOPS_TAG}
    command: ["fuelops", "worker"]
    env_file: .env
    volumes: ["spool:/var/spool/fuelops"]
    depends_on:
      simulator-api: { condition: service_healthy }
      redis: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }
    restart: unless-stopped

  api:
    image: ${FUELOPS_IMAGE}:${FUELOPS_TAG}
    command: ["fuelops", "api"]
    env_file: .env
    deploy: { replicas: 2 }
    volumes: ["spool:/var/spool/fuelops"]
    depends_on:
      redis: { condition: service_healthy }
      migrate: { condition: service_completed_successfully }
    healthcheck: { test: ["CMD", "curl", "-fs", "http://localhost:8080/readyz"], interval: 5s }
    restart: unless-stopped

  nginx:          # serves the operator UI, reverse-proxies /api (SSE: proxy_buffering off)
  prometheus:     # scrapes api:8080/metrics, worker:9100/metrics, redis_exporter, postgres_exporter
  grafana:        # dashboards + alert rules provisioned from ./ops/grafana
volumes: { spool: {} }
```

Redis and PostgreSQL are intentionally not hard dependencies of `api` readiness beyond startup: once running, the API stays up and degrades if either fails.

### 9.3 Health model

| Endpoint | Checks | Fails when |
|---|---|---|
| `/healthz` | event loop responsive | process wedged (container restarted) |
| `/readyz` | at least one read source available (Redis or L1 last-good or DB); worker heartbeat ≤ 15 s old OR snapshot in DB ≤ 2 min old | no state can be served at all |
| `/api/v1/system/status` | per-component status from heartbeats, circuit state, fallback flags, p95/error rate from in-process histograms | never fails; reports |

### 9.4 Configuration (selected env vars)

`SIM_BASE_URL`, `REDIS_URL`, `DATABASE_URL`, `API_WORKERS`, `API_MAX_INFLIGHT`, `L1_TTL_MS`, `POLL_INTERVAL_MS`, `FORECAST_HORIZON_TICKS`, `REVIEW_THRESHOLD`, `POLICY_MODE_DEFAULT`, `CONSTRAINED_DISPATCH_FACTOR`, `LP_TIME_LIMIT_MS`, `APP_ENV`, `AUTH_MODE`, `ENABLE_SIMULATOR_TEST_CONTROLS`, `EXECUTION_MAX_AGE_MS`, `RECOVERY_STABLE_TICKS`, `ENABLE_DOCS`, `API_KEYS_VIEWER`, `API_KEYS_OPERATOR`, `API_KEYS_ADMIN`, `MAX_REQUEST_BODY_BYTES`, `MAX_CONCURRENT_HEAVY`, `MAX_QUEUED_HEAVY`, `TOTAL_REQUEST_DEADLINE_MS`, `LLM_ENABLED`, `LLM_PRIMARY_MODEL`, `LLM_PRIMARY_API_KEY_1..N`, `LLM_BACKUP_MODEL`, `LLM_BACKUP_API_KEY_1..N` (backup disabled unless both model and one key are set), `LLM_TIMEOUT_SECONDS`, `LLM_HARD_DEADLINE_SECONDS`, `LLM_KEY_COOLDOWN_SECONDS`, `LOG_LEVEL`. Secrets come from `.env` (git-ignored) or CI secrets; `.env.example` documents all.

### 9.5 CI/CD

```
push/PR ─► uv sync --frozen (hash-verified) ─► pip-audit --strict
        ─► ruff + mypy + import-linter
        ─► unit + property tests (pytest, hypothesis)          ~1 min
        ─► integration: services {simulator:1.0.0, redis, postgres}
             contract tests + deterministic scenario tests      ~4 min
        ─► docker build (multi-stage, cache) ─► trivy scan
        ─► compose up ─► wait /readyz ─► k6 smoke (60 s) ─► compose down
main    ─► push image :sha and :latest ─► tag release vX.Y.Z
tag     ─► same checks ─► publish linux/amd64 image to GHCR with SBOM + provenance ─► record digest
```

GitHub Actions and the Docker base image are pinned by digest. The image runs as non-root UID/GID 10001, has a `HEALTHCHECK`, and `.dockerignore` keeps `.env`, tests and dev files out of the build context. The submission README references the image by immutable digest, not a mutable tag.

Rollback: `FUELOPS_TAG=<previous> docker compose up -d api worker`. Model/policy rollback: `PUT /api/v1/policy {"model_version": "...", "version": "..."}` (no redeploy).

### 9.6 Observability

Metrics (Prometheus names):

- API: `http_requests_total{route,method,status}`, `http_request_duration_seconds_bucket{route}`, `http_inflight`, `http_shed_total`
- Simulator: `sim_request_duration_seconds{endpoint}`, `sim_errors_total{endpoint,code}`, `sim_circuit_state`, `sim_stream_connected`, `sim_stale_responses_total`, `sim_tick`, `worker_tick_lag`
- Cache: `cache_requests_total{family,result=hit|miss|fallback}`, `redis_up`
- Intelligence: `forecast_mae{fuel}`, `forecast_mape{fuel}`, `model_confidence`, `scoring_duration_seconds{mode}`, `scoring_drift_total`, `planner_duration_seconds{policy}`, `alerts_opened_total{kind}`, `recommendations_total{status}`, `fallback_activations_total{operation,level}`
- Business: `service_level`, `unmet_liters_total`, `allocation_failures`, `submissions_total{result,code}`

Logs: `structlog` JSON to stdout with `request_id`, `run_id`, `tick`, `rec_id`, `idempotency_key`; shipped to Loki if enabled (optional). Alert rules: `SimulatorDown`, `SimulatorStale`, `CircuitOpen > 30s`, `ApiP95High`, `FallbackActive`, `ServiceLevelDrop (< 0.97 over 16 ticks)`, `WorkerHeartbeatMissing`.

## 10. Testing strategy

### 10.1 Layers

| Layer | Tool | What | Where |
|---|---|---|---|
| Unit | pytest | forecast baseline, risk kernel, constraint checks, greedy, LP wrapper, 409 mapping, idempotency key builder, envelope parsing | every commit |
| Property | hypothesis | (a) batch == incremental for random snapshots and random dirty sets; (b) every planned leg satisfies all §6.5 constraints; (c) scores invariant to key order; (d) no plan exceeds `max_shipment` after splitting | every commit |
| Contract | pytest + real simulator image | every `/v1/*` shape parses; each documented 409 code reproduced and mapped; idempotent replay (200 and 201 both accepted); cancel only on PENDING | CI integration |
| Deterministic scenario | `/admin/reset` → inject events → `/admin/step` N | same seed + same actions ⇒ identical `/v1/metrics`; regression-lock service level per scenario | CI integration |
| Fault | `/admin/faults` | each of `latency`, `unavailable`, `error_rate`, `stale_data`, `stream_disconnect`: expected degraded flags, fallbacks, alerts, recovery time | CI integration (short durations) + nightly |
| Chaos | `docker stop/start` | Redis down, PostgreSQL down, worker killed, API replica killed | nightly + demo rehearsal |
| Load | k6 | §10.3 profiles | nightly + before demo |
| Decision benchmark | replay harness | §10.4 | nightly, and on any intelligence change |
| Replay validator | pytest + hypothesis | fuzzed plans that break each rule are rejected with the right code; every planner output passes; validator and simulator agree on accept/reject for sampled legs (contract) | every commit + CI integration |
| Directive guardrails | pytest | each directive type, contradictions, unknown ids, out-of-range liters, bad windows | every commit |
| LLM qualification | `scripts/qualify_llm.py --provider … --passes 3` | official-style notes plus adversarial paraphrases per directive type; accuracy and median/max latency per provider; recorded in the evidence folder | on model/prompt change, before demo |
| Scenario runner | `scripts/run_scenarios.py [--base-url]` | crisis pack against local or deployed stack; pass/fail and service level per scenario | before demo, after each deploy |

### 10.2 Deterministic test harness

```
reset → pause → (optional) POST /admin/events … → loop N:
    worker.refresh_once() ; worker.plan_once(policy=AUTO)
    POST /admin/step
collect /v1/metrics, our metrics, recommendations → assert
```

The worker exposes `refresh_once`/`plan_once` in test mode so tests step the world synchronously; no sleeps.

### 10.3 Load profiles (k6)

Mix for normal and stress: 40% `/network/snapshot`, 20% `/risk`, 15% `/recommendations`, 10% `/stations/{id}`, 5% `/kpi`, 5% `/system/status`, 3% `/recommendations/{id}/simulate`, 2% approve/reject (against a pool of synthetic recommendations in a dedicated test run), plus a separate scenario holding N SSE connections.

| Profile | Shape | Thresholds (k6 `thresholds`) |
|---|---|---|
| smoke (CI) | 10 VUs, 60 s | `p(95)<50` hot reads, `http_req_failed<0.01` |
| normal | 50 VUs, 15 min, simulator RUNNING at speed 8 | PRD §6.1 |
| stress | ramp 0 → 500 VUs over 5 min, hold 5 min | hot `p(95)<250`, non-shed 5xx `<0.5%` |
| spike | 20 → 800 VUs in 10 s, hold 2 min, drop | shed rate reported; recovery ≤ 30 s |
| soak | 100 VUs, 60 min | RSS growth < 10%, stable p95 |
| fault-under-load | normal + fault injected at minute 2 for 60 s, one run per fault type | non-5xx ≥ 99.5%, `meta.degraded` present during fault, recovery ≤ 10 s |
| dependency-kill | normal + stop Redis (60 s), later Postgres (60 s) | no 5xx storm, auto-recovery |

Every run exports avg, p50, p95, p99, RPS, error rate, VUs, and container CPU/memory (from cAdvisor or `docker stats` sampled into the report), which together form the load-test evidence deliverable.

### 10.4 Decision-quality benchmark

Replay policies against identical reset conditions, event visibility, duration and demand data (there is no state cloning, so every comparison is a separate replay from `/admin/reset`). Scenarios: normal, demand spike, route failure, delayed supply, constrained depot, combined crisis, plus demand changes or event schedules **not used for tuning**. Run 192 ticks (2 simulated days) with these policies:

1. `none`: no allocations (reference);
2. `reactive`: reactive threshold replenishment (q formula with a fixed threshold, no forecast);
3. `greedy`: profile forecast + marginal-benefit greedy;
4. `lp`: the same forecast + rolling-horizon LP (if implemented).

The forecast is held the same across 3 and 4 so improvements are attributable to the planner. Service targets τ ∈ {0.8, 0.9, 0.95}, α and gate settings, and (stretch) CVaR λ are compared here, never asserted.

Report official `service_level`, unmet liters, `allocation_failures`, **worst-station service level**, 409 rate and decision latency. Separately, report forecast MAE/WAPE by horizon (WAPE only where demand ≠ 0), horizon-specific interval coverage, and shock-detection delay, all using rolling-origin (chronological) validation, never random splits. No improvement percentage is announced until replays support it. If simple replenishment already reaches 100% service in the normal scenario, report that tie honestly and show the constrained crises where allocation matters. A change to the intelligence layer merges only if it does not reduce `service_level` on any scenario by more than 0.5 percentage points. Batch mode makes a full benchmark run take seconds, so it runs on every intelligence PR.

## 11. Failure-mode summary

| Failure | Detection | Automatic response | Operator sees | Recovery |
|---|---|---|---|---|
| Simulator `latency` | `sim_request_duration` p95 > 400 ms | coalescing absorbs; approve returns 202 when > 3 s | "Simulator slow" | automatic |
| Simulator `unavailable` | 503 FAULT_INJECTED streak → circuit open | serve cache, freeze auto-submit, queue approvals | degraded banner, stale age | circuit half-open probe → resync |
| Simulator `error_rate` | `sim_errors_total` rate | per-call retry, partial snapshot merge | lower confidence on affected keys | automatic |
| Simulator `stale_data` | `X-Simulator-Stale: true` | mark stale, confidence × 0.5, no auto-submit | stale badge | header disappears → batch rescore |
| Simulator `stream_disconnect` | 503 on `/v1/stream` | polling mode | "stream: polling" | reconnect → full resync |
| Simulator reset | notice / tick regression | flush, new run, batch rescore | new run marker | automatic |
| Invalid simulator payload | Pydantic error | reject part, keep last valid, alert | alert | next valid refresh |
| Redis down | connection errors, `redis_up=0` | L1 + DB reads, PG advisory lock, polling push, spool to file | "cache degraded" | reconnect, worker repopulates in one refresh |
| PostgreSQL down | pool errors | Redis spool, policy forced MANUAL | "history degraded" | spool replay |
| Worker down | heartbeat missing | API serves last state as stale | "decision engine down" | restart policy; batch rescore on start |
| ML model failure | inference error / timeout | analytic forecast | "forecast: fallback" | reload or roll back model version |
| LP failure | infeasible / timeout | greedy | `policy_used=greedy` | automatic next tick |
| API overload | in-flight limit | shed non-critical reads | 503 + Retry-After on some reads | automatic |

## 12. Decisions log (ADR summary)

| # | Decision | Rationale |
|---|---|---|
| 1 | Modular monolith with `api` and `worker` roles | Matches project preference; one image, clear boundaries, fewer failure points than microservices |
| 2 | Worker is the only simulator reader | Isolates user load from simulator faults; enables coalescing and precomputation |
| 3 | Tick-based freshness, TTL as safety net | Simulator speed is variable and may pause |
| 4 | Pre-rendered JSON in Redis | Hot-path latency dominated by serialization otherwise |
| 5 | Same scoring kernel for batch and incremental, with periodic equality check | Test replays and live operation cannot drift apart |
| 6 | LP primary, greedy fallback, hold last | Optimal when it works, always an answer when it does not, human review when neither helps |
| 7 | Outbox with deterministic idempotency keys | Safe retries under `error_rate`/`unavailable` faults; uses the simulator's body-level `idempotency_key` |
| 8 | PostgreSQL over SQLite | Concurrent writers (2 API + worker), advisory locks, `SKIP LOCKED` outbox |
| 9 | No Kafka/Kubernetes | Scale is 12 keys and 6 routes; Compose meets every requirement and is simpler to demo and recover |
| 10 | Independent replay validator before proposal and submission | Worked in the preliminary round; catches planner bugs before the simulator rejects them or, worse, accepts a bad plan |
| 11 | LLM only interprets into a closed directive set; deterministic guardrails and compiler own the numbers | Preliminary-round pattern; gives a meaningful LLM role (brief §7) without letting it do arithmetic or block decisions |
| 12 | LLM gateway: key pool, one repair, backup provider only on provider failure, max 2 calls | Qualified 60/60 on both providers in the preliminary round; bounded cost and latency |
