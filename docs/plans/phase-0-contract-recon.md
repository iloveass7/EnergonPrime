# Phase 0 — Contract Recon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One script, `scripts/contract_smoke.py`, that drives the pinned simulator image from a clean start, hits every documented endpoint, asserts the contract, and records fixtures. The fixtures cover SSE, the stale header, the 200/201 idempotent replay, cancel, and the admin request and response bodies.

**Architecture:** A standalone async recon script outside `fuelops/`. It uses raw `httpx` with no retries, so it sees true wire behaviour. It is organised as ordered *sections* (preflight → reads → allocations → events → faults → finale) that share one `SmokeContext`. A background SSE recorder runs across all sections. Every HTTP exchange becomes one JSON fixture. A run report goes to `evidence/phase-0/`. Offline pytest suites cover the script's pure helpers and the recorded fixture set.

**Tech Stack:** Python 3.12, httpx 0.28.1 (`AsyncClient`, `client.stream`, `MockTransport`), pytest 9.1.1, Docker Compose, image `asifmahmoud414/bup-fuel-supply-simulator:1.0.0`.

**Spec:** `docs/pipeline.md` §3 (integration rules) and §14 Phase 0. The following supplement it:
- `docs/prd.md` F-INT-3…6 (envelopes, SSE event names, stale header, reset notice).
- `docs/architect.md` §3.1 (SSE lifecycle), §6.6 (response table), §9.2 (compose), §10.1 (contract layer).

Per your instruction, the files in `docs/` are the source of truth. The Downloads guide is not used.

## Global Constraints

- Image pinned by tag **and** digest: `asifmahmoud414/bup-fuel-supply-simulator:1.0.0@sha256:7067050693f49d377d91ca91f2faa6e63f673f69a69429d4693e78e9c0598e92`. Unmodified.
- `/v1` writes are `POST /v1/allocations` and `POST /v1/allocations/{id}/cancel` only (pipeline §3.1).
- Admin surface is limited to the pipeline §2 list: `run, pause, step, reset, events, faults, faults/clear, audit`. There is no `toggle` and no `GET /admin` HTML.
- Idempotent replay: accept **200 or 201**, and assert the **same allocation id** (pipeline §3.2). Record which status the image actually returns.
- Envelopes to observe: `{"detail":{"code"}}` (domain), `{"error":{"code":"FAULT_INJECTED"}}` (faults), `{"detail":{"code":"FAULT_INJECTED"}}` (stream disconnect), `{"detail":[...]}` (422).
- SSE event names: `simulation.tick`, `allocation.status_changed`, `inventory.updated`, `simulator.notice`. The recorder keeps `: connected` and `: keepalive` comments.
- Stale signal: the `X-Simulator-Stale: true` header on `/v1/*` GETs.
- Idempotency keys are 1–150 chars. Use fixed keys (`p0-*`); reset wipes allocations, so they are safe to reuse across runs.
- Report only measured numbers. Label every fixture and report as simulated (`"provenance": "SIMULATED — recorded from simulator image 1.0.0"`).
- No secrets. The script reads no `.env`. The base URL comes from `--base-url` (default `http://localhost:8000`).
- No commits (the repo is not under git). Record `git_sha: null` in evidence.

## Decisions that need your approval (CLAUDE.md exceptions, scoped)

1. **Raw httpx, not the resilient client.** CLAUDE.md requires every simulator call to go through the integration client. That client does not exist until Phase 1, and recon must see unretried 503s. The exception is scoped to `scripts/contract_smoke.py`. Nothing in `fuelops/` may import `scripts/`; Phase 1 adds that as an import-linter contract.
2. **Direct `/admin/*` from the script.** The "dual-gated test adapter" rule governs the product. This is a dev tool outside the product, run by hand or in CI only.
3. **Admin request bodies come from the image's own `/openapi.json`**, recorded in Task 1. The field names for events and faults are not in `docs/`. If `/openapi.json` is absent, **stop and ask**.

## Review Focus

1. **Aborted run leaves a fault active.** If a section raises mid-fault, the next run hits a stuck `unavailable` fault. Expected: `finally` always calls `faults/clear` and `pause`, and preflight clears again. Pinned by `test_cleanup_runs_when_section_raises` (Task 1).
2. **Fixture name collision overwrites silently.** Expected: the writer raises on a duplicate name. Pinned by `test_writer_rejects_duplicate_name` (Task 1).
3. **Non-JSON or empty response body** (HTML error page, empty 503). Expected: stored as `body_text` with `body: null`, and the run does not crash. Pinned by `test_writer_stores_non_json_body_as_text` (Task 1).
4. **SSE stream stalls or ends during a fault.** Expected: the recorder never blocks shutdown. It reconnects as a new segment and stops within a hard deadline. Pinned by `test_parse_sse_lines_*` plus the deadline assertion in Task 2.
5. **Stale header casing or value drift** (`x-simulator-stale: True`). Expected: detection is case-insensitive on both name and value. Pinned by `test_is_stale_case_insensitive` (Task 1).

---

## File map

| Path | Responsibility |
|---|---|
| `docker-compose.yml` (create) | Only the `simulator-api` service, pinned tag@digest, `SIMULATOR_START_MODE=paused`, `ports: ["8000:8000"]`, healthcheck per architect §9.2. Later phases extend this file. |
| `scripts/__init__.py` (create, empty) | Makes the helpers importable by tests. |
| `scripts/contract_smoke.py` (create) | CLI, `SmokeContext`, `FixtureWriter`, pure helpers, SSE recorder, section functions, report and compare. |
| `tests/unit/test_contract_smoke_helpers.py` (create) | Offline tests of pure helpers and the writer. |
| `tests/unit/test_contract_smoke_cleanup.py` (create) | Offline `MockTransport` test of the always-cleanup path. |
| `tests/contract/test_phase0_fixtures.py` (create) | Offline gate: the recorded fixture set is complete, passed, and pinned. |
| `tests/fixtures/contract/sim-1.0.0/` (generated) | Recorded fixtures plus `manifest.json`. |
| `evidence/phase-0/contract-smoke-<UTC>.json` (generated) | Run report. |
| `pyproject.toml` (modify) | Add `[tool.pytest.ini_options]`: `pythonpath = ["."]`, `testpaths = ["tests"]`, `addopts = "--strict-markers"`. |
| `docs/PROGRESS.md` (create) | Phase tracker. |
| `docs/contract-findings.md` (create) | Measured contract facts for Phase 1 (actual replay status, keepalive interval, admin shapes, clamp behaviour, volatile fields). |

## Fixture format

One file per exchange: `tests/fixtures/contract/sim-1.0.0/{seq:03d}_{name}.json`. `seq` is the global call order.

```json
{
  "fixture_version": 1,
  "provenance": "SIMULATED — recorded from simulator image 1.0.0",
  "name": "alloc_replay",
  "section": "allocations",
  "volatile": false,
  "request":  {"method": "POST", "path": "/v1/allocations", "query": {}, "body": {"idempotency_key": "p0-create-1", "...": "..."}},
  "response": {"status": 201, "headers": {"content-type": "application/json", "x-simulator-stale": null}, "body": {"id": 1, "...": "..."}, "body_text": null},
  "sim_tick": 4
}
```

- Response headers are lowercased and stored in full, minus `date` and `server`. `x-simulator-stale` is always present as a key (null when absent), so its absence is recorded explicitly.
- `volatile: true` marks exchanges whose bodies depend on wall-clock time (`admin_run`, the latency pair). They are excluded from the determinism diff.
- The SSE fixture is `{seq}_sse_stream.json`:
  - `{"segments": [{"status", "content_type", "lines": [raw...], "events": [{"event", "data"}]}], "comments_seen": [...]}`.
  - Raw lines come from `response.aiter_lines()`. `httpx-sse` is **not** used, because its decoder drops `:` comment lines (verified in the installed `httpx_sse/_decoders.py:114`).
- `manifest.json` holds:
  - image ref and digest, base URL, `seed` and `scenario_id` from `/v1/instance`
  - the fixture list with sha256
  - check results `[{name, ok, detail}]`, `all_passed`
  - measured timings (stream keepalive gap, latency-fault delta)

## Proof of "done"

All four must hold:

1. **Clean run passes.** `docker compose down -v && docker compose up -d`, then `uv run python scripts/contract_smoke.py` → exit 0 and a final line `CONTRACT SMOKE: <N>/<N> checks passed`. It writes the fixtures, `manifest.json` and the evidence report.
2. **Determinism.** A second clean `down -v && up -d`, then `uv run python scripts/contract_smoke.py --out "$TMPDIR/p0-rerun" --compare-to tests/fixtures/contract/sim-1.0.0` → exit 0. Normalised fixtures must be identical, including the normalised SSE event sequence.
3. **Offline suites green.** `uv run pytest tests/unit tests/contract -q` → all pass with no simulator running.
4. **Hygiene.** `uv run ruff check scripts tests` and `uv run mypy scripts` → clean. `docs/PROGRESS.md` shows Phase 0 done with the evidence path.

---

### Task 1: Pinned simulator, script skeleton, recorder, preflight

**Files:**
- Create: `docker-compose.yml`, `scripts/__init__.py`, `scripts/contract_smoke.py`
- Create: `tests/unit/test_contract_smoke_helpers.py`, `tests/unit/test_contract_smoke_cleanup.py`
- Modify: `pyproject.toml` (pytest ini options above)

**Interfaces — Produces** (all in `scripts/contract_smoke.py`):
- `PROVENANCE: str`, `VOLATILE_BODY_KEYS: frozenset[str] = {"wall_time", "start_wall_time", "end_wall_time"}`, `DROPPED_HEADERS = {"date", "server"}`
- `@dataclass Exchange(name, section, method, path, query: dict, req_body: Any, status: int, headers: dict[str, str | None], body: Any, body_text: str | None, elapsed_ms: float, sim_tick: int | None, volatile: bool)`
- `class FixtureWriter(out_dir: Path)`: `.write(ex: Exchange) -> Path` (raises `ValueError` on a duplicate name), `.write_raw(name: str, section: str, payload: dict) -> Path`, `.index: list[dict]`
- `def envelope_code(body: Any) -> str | None`: returns `detail.code`, `error.code`, or `"VALIDATION"` when `detail` is a list.
- `def is_stale(headers: Mapping[str, str]) -> bool`
- `def normalize(fixture: dict) -> dict`: drops `VOLATILE_BODY_KEYS` at any depth, plus `elapsed_ms`.
- `class SmokeContext`:
  - `async call(name, method, path, *, section, params=None, json=None, volatile=False) -> Exchange`
  - `check(name: str, ok: bool, detail: str = "") -> bool`
  - `async step(n: int) -> int` (records each `admin_step_NN`, returns the last tick)
  - `checks: list[dict]`, `current_tick: int | None`
- `SECTIONS: list[tuple[str, Callable[[SmokeContext], Awaitable[None]]]]`
- `async def run_smoke(client: httpx.AsyncClient, out_dir: Path, only: set[str] | None) -> int`: returns the exit code. **Always** runs `POST /admin/faults/clear` and `POST /admin/pause` in `finally`.
- CLI: `--base-url` (default `http://localhost:8000`), `--out` (default `tests/fixtures/contract/sim-1.0.0`), `--section NAME` (repeatable, default all), `--compare-to DIR`
- Client: `httpx.AsyncClient(base_url=..., timeout=httpx.Timeout(10.0, connect=2.0))`. These are lenient recon timeouts, not the product's §3.2 values.

- [ ] **Step 1: Write failing helper tests** in `tests/unit/test_contract_smoke_helpers.py`:
  - `test_envelope_code_domain`: `{"detail":{"code":"ROUTE_MISMATCH"}}` → `"ROUTE_MISMATCH"`
  - `test_envelope_code_fault`: `{"error":{"code":"FAULT_INJECTED"}}` → `"FAULT_INJECTED"`
  - `test_envelope_code_validation`: `{"detail":[{"loc":["body","quantity"]}]}` → `"VALIDATION"`, and `envelope_code({"id":1}) is None`
  - `test_is_stale_case_insensitive`: `{"X-Simulator-Stale":"True"}` → True, `{}` → False, `{"x-simulator-stale":"false"}` → False (use `httpx.Headers`)
  - `test_normalize_drops_volatile_keys_at_any_depth`: nested `wall_time` inside a list of dicts removed; other keys kept
  - `test_writer_rejects_duplicate_name`: second `.write` with the same name → `ValueError`
  - `test_writer_stores_non_json_body_as_text`: `body=None, body_text="<html>"` round-trips; file name matches `^\d{3}_name\.json$`; `provenance` present
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_contract_smoke_helpers.py -v` → FAIL (import error)
- [ ] **Step 3: Implement** the helpers, `Exchange`, `FixtureWriter` and `SmokeContext` above.
- [ ] **Step 4: Write the failing cleanup test** `test_cleanup_runs_when_section_raises` in `tests/unit/test_contract_smoke_cleanup.py`:
  - Build `httpx.MockTransport` with a handler that records `(method, path)` and returns 200 `{}`.
  - Monkeypatch `SECTIONS` to one section that raises `RuntimeError`.
  - `asyncio.run(run_smoke(client, tmp_path, None))` returns `1`.
  - The recorded calls end with `("POST","/admin/faults/clear")` and `("POST","/admin/pause")`.
- [ ] **Step 5: Implement** `run_smoke`, the CLI, and the `preflight` section:
  - Poll `GET /v1/health` until 200 (≤ 30 s, 0.5 s interval; record the final one as `health`).
  - `GET /openapi.json` → `openapi`. Check it has `paths` containing `/admin/events` and `/admin/faults`; if not, **fail and stop** (Decision 3).
  - `POST /admin/faults/clear` → `admin_faults_clear_start`
  - `POST /admin/reset` → `admin_reset`, then `POST /admin/pause` → `admin_pause`
  - `GET /v1/instance` → `instance_initial`; check `tick == 0` and `status == "PAUSED"`; store `seed` and `scenario_id` in the manifest.
- [ ] **Step 6: Run the unit tests** → PASS.
- [ ] **Step 7: Live check.**
  - `docker compose up -d`, then `uv run python scripts/contract_smoke.py --section preflight` → exit 0, and `tests/fixtures/contract/sim-1.0.0/002_openapi.json` exists.
  - `docker compose ps` shows the service healthy. If the healthcheck fails because the image lacks `curl`, change the compose healthcheck to `python -c` urllib; the script's own health poll is the gate either way.

### Task 2: SSE recorder

**Interfaces:**
- Produces: `@dataclass SseEvent(event: str, data: Any)`, `@dataclass SseSegment(status: int, content_type: str | None, lines: list[str], events: list[SseEvent])`
- `def parse_sse_lines(lines: Iterable[str]) -> tuple[list[SseEvent], list[str]]`: returns (events, comments). A blank line dispatches the event. `data` is JSON-decoded, falling back to the raw string.
- `class SseRecorder(client)`:
  - `start()`, `async stop(timeout_s: float = 5.0)`, `async wait_for(event: str | None = None, comment: str | None = None, timeout_s: float) -> bool`
  - `segments: list[SseSegment]`
  - Loop: `client.stream("GET", "/v1/stream", timeout=httpx.Timeout(10.0, read=None))`. On non-200, `aread()` the body and record a segment with the body as a single line. On EOF or error, start a new segment after 0.5 s. Exit on stop.
- `run_smoke` starts the recorder after preflight and stops it after the finale. It writes `sse_stream` via `write_raw`.

- [ ] **Step 1: Write failing tests:**
  - `test_parse_sse_lines_events_and_comments`: input `[": connected", "", "event: simulation.tick", 'data: {"tick": 1}', "", ": keepalive", ""]` → one event `("simulation.tick", {"tick": 1})` and comments `["connected", "keepalive"]`
  - `test_parse_sse_lines_non_json_data`: `data: hello` → `data == "hello"`
  - `test_parse_sse_lines_trailing_event_without_blank_is_dropped`
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `parse_sse_lines`, `SseRecorder`. **Step 4: Run** → PASS.
- [ ] **Step 5: Live check.** A temporary `--section preflight` run with the recorder plus one `ctx.step(1)`:
  - checks `sse_connected_comment_first` (the first line of the first segment is `: connected`) and `sse_tick_after_step` (`wait_for(event="simulation.tick", timeout_s=3)`)
  - the recorder stops within 5 s (check `sse_stop_within_deadline`)

### Task 3: Reads section (every `/v1` GET)

**Interfaces:** Consumes `SmokeContext.call/step/check` and topology from the responses. Produces `ctx.topology: dict` (depots, stations, routes by id) for later sections.

- [ ] **Step 1: Implement `reads`:**
  - `ctx.step(4)` (fixtures `admin_step_01..04`); check that the returned ticks are `1,2,3,4`.
  - GETs, each with check `status == 200` and a JSON list or object:
    - `regions`, `depots`, `stations`, `routes`, `supply_arrivals`, `events_initial`, `allocations_initial`, `metrics_initial`, `instance_after_steps` (check `tick == 4`)
    - `depot_by_id` and `station_by_id` (first id from the lists)
  - `depot_unknown_404` / `station_unknown_404` (`/v1/depots/does-not-exist`) → check 404 and `envelope_code == "NOT_FOUND"`
  - `demand_history` (`station_id=<first station>`, `limit=12`) → check 200 and ≤ 12 rows
  - `demand_history_limit_over` (`limit=2001`) → record only, with no status assertion. Clamp or 422 is a finding.
  - Check `stale_header_absent_baseline`: `is_stale` is False on `depots`.
- [ ] **Step 2: Live check.** `--section preflight --section reads` → exit 0.

### Task 4: Allocations section (create, 200/201 replay, cancel, error bodies)

**Interfaces:** Consumes `ctx.topology`. Picks route `R` = the first `AVAILABLE` route; fuel `DIESEL`; `q = min(1000, R.max_shipment, station.capacity.DIESEL − station.inventory.DIESEL)`. If `q < 1`, check-fail with the reason. Base body `B` = `{idempotency_key, source_depot_id, destination_station_id, route_id, fuel_type, quantity}` from `R`.

- [ ] **Step 1: Implement `allocations`.** Each call has its check:

| Fixture | Request | Check |
|---|---|---|
| `alloc_create` | `B`, key `p0-create-1` | 201, `status == "PENDING"`; remember `id` |
| `alloc_replay` | identical `B` | status ∈ {200, 201} **and** same `id`; record actual status in manifest `findings.replay_status` |
| `alloc_key_mismatch_409` | same key, `quantity = q − 1` | 409 `IDEMPOTENCY_KEY_MISMATCH` |
| `alloc_unknown_depot_404` | key `p0-404-1`, depot `does-not-exist` | 404 `NOT_FOUND` |
| `alloc_route_mismatch_409` | key `p0-mismatch-1`, `R` with a station it doesn't serve | 409 `ROUTE_MISMATCH` |
| `alloc_route_capacity_409` | key `p0-cap-1`, `quantity = R.max_shipment + 1` | 409 `ROUTE_CAPACITY_EXCEEDED` |
| `alloc_invalid_422` | key `p0-422-1`, `quantity = 0` | 422, `envelope_code == "VALIDATION"` |
| `alloc_cancel` | `POST /v1/allocations/{id}/cancel` | 200, `status == "CANCELLED"` |
| `alloc_cancel_again_409` | same | 409 `CANNOT_CANCEL` |
| `alloc_cancel_unknown_404` | id `999999` | 404 `ALLOCATION_NOT_FOUND` |
| `alloc_lifecycle_create` | key `p0-lifecycle-1`, `quantity = min(500, q)` | 201 |
| `allocations_after_lifecycle` | after `ctx.step(R.transit_ticks + 2)` | lifecycle allocation `status == "ARRIVED"` |
| `metrics_after_lifecycle` | GET | 200; record only |

- [ ] **Step 2: Live check.** `--section preflight --section reads --section allocations` → exit 0. Also check that `sse_stream` contains `allocation.status_changed` and `inventory.updated` (`wait_for`, 3 s each).

### Task 5: Events section (admin event bodies, route/station 409s)

**Interfaces:** Admin event body field names come from `openapi` fixture `components.schemas` for `POST /admin/events` (Decision 3). Record the exact schema name used in `docs/contract-findings.md`. Use `start_tick = ctx.current_tick`, duration 3 ticks, type values `route_disruption` and `station_outage` (pipeline §13.2).

- [ ] **Step 1: Implement `events`:**
  - `admin_event_route_disruption` on `R`; check 201 (or 200); record the body.
  - `ctx.step(1)`, then `routes_disrupted`; check `R.status == "DISRUPTED"`.
  - `alloc_route_disrupted_409` (key `p0-disrupted-1`) → 409 `ROUTE_DISRUPTED`.
  - Pick route `S` with `S.destination_station_id != R.destination_station_id` and `S.status == "AVAILABLE"`.
  - `admin_event_station_outage` on S's station, `ctx.step(1)`, then `stations_outage`; check `status == "OUTAGE"`.
  - `alloc_station_closed_409` (key `p0-closed-1`, via `S`) → 409 `STATION_CLOSED`.
  - `events_list`; check both events present.
- [ ] **Step 2: Live check.** Sections through `events` → exit 0.

### Task 6: Faults section (stale header, envelopes, bypasses)

**Interfaces:** Fault body field names come from the `openapi` fixture (Decision 3). Each fault uses `duration_seconds = 60` and is cleared explicitly before the next one, so no check races the expiry.

- [ ] **Step 1: Implement `faults`.** For each block: inject → probe → `admin_faults_clear_<type>` (check 200, record body) → probe that it recovered.

| Fault | Probe fixtures and checks |
|---|---|
| `stale_data` | `depots_stale`: 200 and `is_stale`; after clear `depots_fresh`: not `is_stale` |
| `unavailable` | `instance_unavailable_503`: 503, `envelope_code == "FAULT_INJECTED"`, body top key `error`; `health_during_unavailable`: 200 (bypass); `admin_audit_during_unavailable` (`limit=5`): 200 (admin bypass) |
| `stream_disconnect` | `stream_disconnect_503`: a fresh `client.stream` GET `/v1/stream` → 503, top key `detail`, code `FAULT_INJECTED` |
| `latency` (`delay_ms: 300`) | `instance_latency` (volatile): `elapsed_ms ≥ 250`; `health_latency` (volatile): `elapsed_ms < 250`; record both in manifest timings |
| `error_rate` (`rate: 1.0`) | `instance_error_rate_503`: 503 `FAULT_INJECTED`. If the image rejects `rate: 1.0` with 422, the check fails → **stop and ask** |

- [ ] **Step 2: Live check.** Sections through `faults` → exit 0; then `curl -s http://localhost:8000/v1/instance` returns 200 (no fault left behind).

### Task 7: Finale, manifest and evidence, determinism compare, offline gate, docs

**Interfaces:**
- Produces: `REQUIRED_FIXTURES: tuple[str, ...]` in `scripts/contract_smoke.py`. It holds every fixture name from Tasks 1 and 3–6, plus `sse_stream`, `admin_audit`, `admin_run`, `admin_pause_final`, `admin_reset_final` and `instance_final`.
- `def compare_dirs(recorded: Path, fresh: Path) -> list[str]`: returns diffs by fixture name, comparing `normalize()`d, non-volatile fixtures. For `sse_stream`, it compares the `(event, data)` sequence of 200 segments only.

- [ ] **Step 1: Write failing tests:**
  - `test_compare_dirs_ignores_volatile_and_wall_time`: two dirs differing only in `wall_time` and a `volatile: true` fixture → `[]`
  - `test_compare_dirs_reports_changed_body`: a changed `quantity` → one diff naming the fixture
- [ ] **Step 2: Implement `finale`:**
  - `sse_keepalive`: `wait_for(comment="keepalive", timeout_s=20)`; record the measured gap in manifest timings.
  - `admin_audit` (`limit=50`) → 200 list.
  - `admin_run` (volatile) → 200, then immediately `admin_pause_final` → 200.
  - `admin_reset_final` → 200; `wait_for(event="simulator.notice", timeout_s=3)`.
  - `instance_final`: `tick == 0`.
  - Check `sse_all_event_names_seen`: all four names present.
- [ ] **Step 3: Implement** `compare_dirs`, `--compare-to`, `manifest.json` writing, and the evidence report `evidence/phase-0/contract-smoke-<YYYYmmddTHHMMSSZ>.json`. The report carries the manifest contents plus the image digest (read with `docker image inspect --format '{{index .RepoDigests 0}}'`, null if the CLI is missing), `git_sha: null`, and the fixture sha256 list. Print `CONTRACT SMOKE: <passed>/<total> checks passed` and list failures.
- [ ] **Step 4: Write** `tests/contract/test_phase0_fixtures.py` (offline, reads the committed dir):
  - `test_manifest_all_passed`: `manifest["all_passed"] is True`
  - `test_required_fixtures_present`: every name in `REQUIRED_FIXTURES` has exactly one file
  - `test_every_fixture_is_labelled_simulated`: each JSON has `provenance == PROVENANCE`
  - `test_image_digest_matches_compose`: the manifest digest string appears in `docker-compose.yml`
  - `test_replay_status_recorded`: `manifest["findings"]["replay_status"] in (200, 201)`
- [ ] **Step 5: Run proofs 1–4** from "Proof of done", in order, from a clean `down -v`. All must be green. If the determinism diff flags a new wall-clock field, add it to `VOLATILE_BODY_KEYS` **and** record it in `docs/contract-findings.md`. Any other diff is a finding → stop and ask.
- [ ] **Step 6: Write docs:**
  - `docs/contract-findings.md`, measured facts only, each with its fixture name:
    - the actual replay status
    - `: connected` and the measured keepalive gap
    - admin event and fault schemas (from openapi)
    - the `limit=2001` behaviour
    - observed envelopes per case
    - volatile fields
    - GET latency p50 and max from the manifest (input to tuning the §3.2 timeouts; pipeline.md is not edited here)
  - `docs/PROGRESS.md`: phase table 0–10 from pipeline §14 with Phase 0 = done, the date, and the evidence report path.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Admin body field names are not in `docs/` | Wrong payloads → 422, recon blocked | Take them from the image's `/openapi.json` (Task 1). Stop and ask if it is absent |
| Leftover fault from a crashed run | Next run fails confusingly | `finally` clear + pause; preflight clears again; unit test pins it |
| Wall-clock nondeterminism (audit `wall_time`, fault timestamps, `run` tick drift, keepalive timing, SSE reconnect count during faults) | Determinism diff flakes | Volatile flag, `VOLATILE_BODY_KEYS`, SSE compared by event sequence only, `run` placed just before the final reset |
| `error_rate` is random | Flaky 503 probe | `rate: 1.0`; stop and ask if rejected |
| Existing SSE stream behaviour under `stream_disconnect` is undocumented | Recorder hangs or misses events | Segmented recorder with reconnect and hard stop deadline; only 200-segment events are compared |
| Keepalive needs 15 s+ of idle | Adds ~20 s per run | Accepted; it measures the value architect §3.1's 45 s dead timer depends on |
| 200 vs 201 replay documented both ways (prd §11, F-DEC-4) | Wrong assumption leaks into Phase 1 | Accept both, assert same id, record the actual status as a finding |
| Compose healthcheck uses `curl`, possibly absent from the image | Container reported unhealthy | The script's own health poll is the gate; swap to a python urllib check if needed (Task 1 Step 7) |
| Port 8000 already in use locally | Bring-up fails | `--base-url` flag; document an override port in PROGRESS if needed |
| `DISPATCH_CAPACITY_EXCEEDED`, `DESTINATION_CAPACITY_EXCEEDED`, `INSUFFICIENT_INVENTORY`, `DEPOT_CLOSED` not reproduced | Unrecorded envelopes | Out of scope for Phase 0 (same envelope shape as recorded 409s). Architect §10.1 contract tests cover them in Phase 1. `DEPOT_CLOSED` may be unreachable, since `depot_constraint` yields `CONSTRAINED`, which is still shippable |
| CLAUDE.md names `docs/simulator-guide.md`, which does not exist | Precedence chain points at nothing | Flagged for you to resolve; this plan uses `docs/` only |
