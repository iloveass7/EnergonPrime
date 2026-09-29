# Simulator Contract Findings (Phase 0)

Measured against `asifmahmoud414/bup-fuel-supply-simulator:1.0.0@sha256:7067050693f49d377d91ca91f2faa6e63f673f69a69429d4693e78e9c0598e92`
(seed 12345, scenario `baseline` 1.0, `TICK_MINUTES=15`, started paused) by `scripts/contract_smoke.py`.
All values are **simulated**. Fixtures: `tests/fixtures/contract/sim-1.0.0/` (fixture name in brackets).
Evidence: `evidence/phase-0/contract-smoke-20260929T052805Z.json` (clean run) and
`…-20260929T052823Z.json` (second clean run, determinism compare: identical).

Input for Phase 1 (ACL, typed errors, timeouts). pipeline.md is not edited here; proposals are marked **Proposal**.

## Writes and idempotency

| Case | Observed | Fixture |
|---|---|---|
| New allocation | `201`, `status: "PENDING"`, `departure_tick: null` | `alloc_create` |
| Idempotent replay (same key, same body) | **`201`** (not 200) with the same `id` | `alloc_replay` |
| Same key, different body | `409 {"detail":{"code":"IDEMPOTENCY_KEY_MISMATCH",…}}` | `alloc_key_mismatch_409` |
| Unknown depot | `404 NOT_FOUND` | `alloc_unknown_depot_404` |
| Route/station mismatch | `409 ROUTE_MISMATCH` | `alloc_route_mismatch_409` |
| `quantity = max_shipment + 1` | `409 ROUTE_CAPACITY_EXCEEDED` | `alloc_route_capacity_409` |
| `quantity = 0` | `422 {"detail":[{"type":"greater_than","loc":["body","quantity"],…}]}` | `alloc_invalid_422` |
| Cancel PENDING | `200`, `status: "CANCELLED"` | `alloc_cancel` |
| Cancel again | `409 CANNOT_CANCEL` | `alloc_cancel_again_409` |
| Cancel unknown id | `404 ALLOCATION_NOT_FOUND` | `alloc_cancel_unknown_404` |
| Lifecycle | created and departed in the same tick; `ARRIVED` after `transit_ticks` (2) | `allocations_after_lifecycle` |

The client must still accept 200 as well as 201 on replay (pipeline §3.2), but 1.0.0 returns 201.

## Reads

- Every `/v1` GET in pipeline §3.1 returns 200 JSON on baseline; 28 recorded non-volatile 200 GETs: p50 **2.3 ms**, max **7.6 ms** (local Docker). **Proposal:** the §3.2 timeouts (connect 0.5 s, read 2 s) leave ample margin; keep them.
- Unknown depot/station id → `404 {"detail":{"code":"NOT_FOUND"}}` [`depot_unknown_404`, `station_unknown_404`].
- `demand-history?limit=2001` → **200** (clamped, not 422); 48 rows at tick 4 = 4 ticks × 12 series [`demand_history_limit_over`].
- `sim_time` is serialised **without a UTC offset** (`"2026-01-01T00:00:00"`) on `/v1/instance`, `/admin/*` and SSE ticks. The ACL must parse it as UTC explicitly.
- `/admin/reset` and `/admin/pause` return the full instance object; `/admin/step` returns `{"tick","sim_time"}`; `/admin/faults/clear` returns `{"status":"cleared"}`.

## Admin request bodies (from the image's `/openapi.json`)

- `EventCreate`: `type` ∈ {demand_spike, shipment_delay, route_disruption, station_outage, depot_constraint, supply_shortfall}, `start_tick` ≥ 0, `duration_ticks` > 0, `parameters` free-form object. Response `201` with `end_tick = start_tick + duration_ticks` and `status: "SCHEDULED"` [`admin_event_route_disruption`].
- `FaultCreate`: `type` ∈ {latency, unavailable, error_rate, stale_data, stream_disconnect}, 0 < `duration_seconds` ≤ 3600, `parameters` free-form object. Response `201` with `start_wall_time`, `end_wall_time`, `active` [`admin_fault_stale_data`].
- **Open question:** `parameters` keys are not in `docs/` or the schema. An event with `parameters: {}` goes ACTIVE, then RESOLVED at `end_tick`, but **changes no route or station** [`route_disruption_during`, `station_outage_during`]. Targeted crisis injection (pipeline §13.2 scenarios B–F, J) needs a `docs/` source for the filter keys.

## Faults (`parameters: {}`, i.e. defaults)

| Fault | Observed | Fixture |
|---|---|---|
| `stale_data` | `/v1/depots` 200 with `x-simulator-stale: true`; header absent after clear | `depots_stale`, `depots_fresh` |
| `unavailable` | `503 {"error":{"code":"FAULT_INJECTED","message":"Simulator API temporarily unavailable."}}`; `/v1/health` and `/admin/audit` still 200 | `instance_unavailable_503`, `health_during_unavailable`, `admin_audit_during_unavailable` |
| `stream_disconnect` | new `GET /v1/stream` → `503 {"detail":{"code":"FAULT_INJECTED"}}` (under `detail`, no message); **an already-open stream stays connected** | `stream_disconnect_503`, manifest `sse_segments_after_faults` |
| `latency` | default adds **498–513 ms** to `/v1/instance` across runs; `/v1/health` unaffected (3–6 ms) | manifest `timings` |
| `error_rate` | random 503 with the `error` envelope; first 503 after 1–3 attempts across runs | `instance_error_rate_503` |

**Proposal:** with the default latency fault, a 2 s read timeout still succeeds, so `latency` shows up as degraded latency, not as errors.

## SSE (`/v1/stream`)

- First line on connect: `: connected`. Keepalive comment `: keepalive` after **14.8 s** of silence (the architect §3.1 45 s dead timer = 3 intervals holds).
- Content type `text/event-stream; charset=utf-8`. All four names were seen: `simulation.tick` (one per `/admin/step`), `allocation.status_changed`, `inventory.updated`, `simulator.notice` (on `/admin/reset`).
- The recorder keeps raw lines because `httpx-sse` drops comment lines.

## Determinism

Two clean runs (`docker compose down -v && up -d`, then the script) produce identical fixtures after dropping wall-clock data. The fields and exchanges excluded:
- body keys `wall_time`, `start_wall_time`, `end_wall_time`, and `elapsed_ms`
- headers `date`, `server`, `content-length`
- `volatile` exchanges: `admin_run`, `admin_pause_final`, the latency pair
- SSE is compared by its event sequence only; comments, keepalive timing and reconnect segments are ignored.

Fault and event ids restart after `/admin/reset`.
