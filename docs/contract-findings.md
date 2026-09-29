# Simulator Contract Findings (Phase 0)

Measured against `asifmahmoud414/bup-fuel-supply-simulator:1.0.0@sha256:7067050693f49d377d91ca91f2faa6e63f673f69a69429d4693e78e9c0598e92`
(seed 12345, scenario `baseline` 1.0, `TICK_MINUTES=15`, started paused) by `scripts/contract_smoke.py` at commit `340c018`.
All values are **simulated**. Fixtures: `tests/fixtures/contract/sim-1.0.0/` (fixture name in brackets).
Evidence: `evidence/phase-0/contract-smoke-20260929T054056Z.json` (clean run, 101/101) and
`…-20260929T054113Z.json` (second clean run, determinism compare: identical).

Input for Phase 1 (ACL, typed errors, timeouts). pipeline.md is not edited here; proposals are marked **Proposal**.
Per CLAUDE.md, where `docs/simulator-guide.md` and the image disagree, the image wins.

## Where the image differs from the guide

| Guide says | Image 1.0.0 does | Consequence | Fixture |
|---|---|---|---|
| §7.8: an empty filter list applies the event to **all** entities | `{"route_ids": []}` and `{}` both affect **no** route | Crisis injection (scenarios B–F, J) must always name explicit ids; the test plane must reject empty filters | `route_disruption_empty_filter_during`, `route_disruption_no_filter_key_during` |
| §4.2/§7.5: `sim_time` like `"2026-01-01T00:00:00+00:00"` | `"2026-01-01T00:00:00"` (no offset) on `/v1/instance`, `/admin/*` and SSE ticks | The ACL must parse `sim_time` as UTC explicitly | `instance_initial`, `admin_step_01` |
| §9: idempotent replay → 200 | 201 with the same id (matches §5.4) | Accept both (pipeline §3.2); expect 201 | `alloc_replay` |

## Writes and idempotency

| Case | Observed | Fixture |
|---|---|---|
| New allocation | `201`, `status: "PENDING"`, `departure_tick: null` | `alloc_create` |
| Idempotent replay (same key, same body) | **`201`** with the same `id` | `alloc_replay` |
| Same key, different body | `409 IDEMPOTENCY_KEY_MISMATCH` | `alloc_key_mismatch_409` |
| Unknown depot | `404 NOT_FOUND` | `alloc_unknown_depot_404` |
| Route/station mismatch | `409 ROUTE_MISMATCH` | `alloc_route_mismatch_409` |
| `quantity = max_shipment + 1` | `409 ROUTE_CAPACITY_EXCEEDED` | `alloc_route_capacity_409` |
| Station headroom + 1 L (5,991 L to Mirpur DIESEL) | `409 DESTINATION_CAPACITY_EXCEEDED` | `alloc_destination_capacity_409` |
| Second Gazipur leg in one tick (5,990 + 6,500 > 12,000) | first leg `201`, second `409 DISPATCH_CAPACITY_EXCEEDED`; first leg cancelled afterwards | `alloc_dispatch_leg_1`, `alloc_dispatch_capacity_409` |
| Route DISRUPTED by a targeted event | `409 ROUTE_DISRUPTED` | `alloc_route_disrupted_409` |
| Station OUTAGE by a targeted event | `409 STATION_CLOSED` | `alloc_station_closed_409` |
| `quantity = 0` | `422 {"detail":[{"type":"greater_than","loc":["body","quantity"],…}]}` | `alloc_invalid_422` |
| Cancel PENDING | `200`, `status: "CANCELLED"` | `alloc_cancel` |
| Cancel again | `409 CANNOT_CANCEL` | `alloc_cancel_again_409` |
| Cancel unknown id | `404 ALLOCATION_NOT_FOUND` | `alloc_cancel_unknown_404` |
| Lifecycle | created and departed in the same tick; `ARRIVED` after `transit_ticks` (2) | `allocations_after_lifecycle` |

Not reproduced: `INSUFFICIENT_INVENTORY` (baseline depots hold ≥ 24,000 L per fuel against a 7,000 L max shipment, so it needs a multi-tick drain) and `DEPOT_CLOSED` (probably unreachable: `depot_constraint` sets `CONSTRAINED`, which is still shippable). Both use the same `{"detail":{"code","message"}}` envelope as every recorded 409.

## Reads

- Every `/v1` GET in guide §10 returns 200 JSON on baseline. Across 35 recorded non-volatile 200 GETs: p50 **2.2 ms**, max **6.9 ms** (local Docker). **Proposal:** the pipeline §3.2 timeouts (connect 0.5 s, read 2 s) leave ample margin; keep them.
- Unknown depot/station id → `404 {"detail":{"code":"NOT_FOUND"}}` [`depot_unknown_404`, `station_unknown_404`].
- `demand-history?limit=2001` → **200**, clamped (guide §4.11); 48 rows at tick 4 = 4 ticks × 12 series [`demand_history_limit_over`].
- `/admin/reset` and `/admin/pause` return the full instance object; `/admin/step` returns `{"tick","sim_time"}`; `/admin/faults/clear` returns `{"status":"cleared"}`.

## Admin request bodies

Match guide §7.7–7.10 and the image's `/openapi.json` (`EventCreate`, `FaultCreate`; `parameters` is a free-form object in the schema).
- Events return `201` with `end_tick = start_tick + duration_ticks` and `status: "SCHEDULED"`; they go ACTIVE on the next step and RESOLVED at `end_tick` [`admin_event_route_disruption`, `events_during_route_disruption`].
- Targeted filters work: `{"route_ids": ["route-gazipur-mirpur"]}` disrupts only that route; `{"station_ids": ["station-mirpur"]}` takes only Mirpur to OUTAGE.
- Faults return `201` with `start_wall_time`, `end_wall_time`, `active` [`admin_fault_stale_data`].

## Faults

| Fault | Observed | Fixture |
|---|---|---|
| `stale_data` | `/v1/depots` 200 with `x-simulator-stale: true`; header absent after clear | `depots_stale`, `depots_fresh` |
| `unavailable` | `503 {"error":{"code":"FAULT_INJECTED","message":"Simulator API temporarily unavailable."}}`; `/v1/health` and `/admin/audit` still 200 | `instance_unavailable_503`, `health_during_unavailable`, `admin_audit_during_unavailable` |
| `stream_disconnect` | a new `GET /v1/stream` → `503 {"detail":{"code":"FAULT_INJECTED"}}` (under `detail`, no message); **an already-open stream stays connected** | `stream_disconnect_503`, manifest `sse_segments_after_faults` |
| `latency` `{"delay_ms": 300}` | adds ≈ **310 ms** to `/v1/instance`; `/v1/health` unaffected (≈ 6 ms). With `{}` the default added ≈ 500 ms | manifest `timings` |
| `error_rate` `{"rate": 1.0}` | first request → 503 with the `error` envelope (deterministic) | `instance_error_rate_503` |

**Proposal:** a 2 s read timeout survives the default 500 ms latency fault, so `latency` shows up as degraded latency, not as errors.

## SSE (`/v1/stream`)

- First line on connect: `: connected`. Keepalive `: keepalive` after **14.8 s** of silence (guide §6.1 says 15 s; the architect §3.1 45 s dead timer = 3 intervals holds).
- Content type `text/event-stream; charset=utf-8`. All four names were seen: `simulation.tick` (one per `/admin/step`), `allocation.status_changed`, `inventory.updated`, `simulator.notice` (on `/admin/reset`).
- The recorder keeps raw lines because `httpx-sse` drops comment lines.

## Determinism

Two clean runs (`docker compose down -v && up -d`, then the script) produce identical fixtures after dropping wall-clock data:
- body keys `wall_time`, `start_wall_time`, `end_wall_time`, and `elapsed_ms`
- headers `date`, `server`, `content-length`
- `volatile` exchanges: `admin_run`, `admin_pause_final`, the latency pair
- SSE is compared by its event sequence only; comments, keepalive timing and reconnect segments are ignored.

Fault and event ids restart after `/admin/reset`.
