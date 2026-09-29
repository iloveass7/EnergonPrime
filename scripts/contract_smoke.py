"""Phase 0 contract smoke: drive the pinned simulator, assert the contract, record fixtures.

Recon tool, deliberately outside ``fuelops/`` (plan Decisions 1-2): it calls the simulator
with raw httpx and no retries so the recorded fixtures show true wire behaviour, and it
calls ``/admin/*`` directly. Product code must never import this module.
Every recorded quantity is simulated.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

IMAGE = "asifmahmoud414/bup-fuel-supply-simulator:1.0.0"
PROVENANCE = "SIMULATED — recorded from simulator image 1.0.0"
FIXTURE_VERSION = 1
VOLATILE_BODY_KEYS = frozenset({"wall_time", "start_wall_time", "end_wall_time"})
DROPPED_HEADERS = frozenset({"date", "server"})
_NORMALIZE_DROP = VOLATILE_BODY_KEYS | {"elapsed_ms", "content-length"}
DEFAULT_BASE_URL = "http://localhost:8000"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "tests/fixtures/contract/sim-1.0.0"
HEALTH_WAIT_S = 30.0
SSE_RECONNECT_S = 0.5
SSE_KEEPALIVE_WAIT_S = 20.0
LATENCY_DELAY_MS = 300
EVIDENCE_DIR = REPO_ROOT / "evidence/phase-0"
SSE_EVENT_NAMES = (
    "simulation.tick",
    "allocation.status_changed",
    "inventory.updated",
    "simulator.notice",
)
REQUIRED_FIXTURES: tuple[str, ...] = (
    # preflight
    "health", "openapi", "admin_faults_clear_start", "admin_reset", "admin_pause",
    "instance_initial",
    # reads
    "admin_step_01", "regions", "depots", "stations", "routes", "supply_arrivals",
    "events_initial", "allocations_initial", "metrics_initial", "instance_after_steps",
    "depot_by_id", "depot_unknown_404", "station_by_id", "station_unknown_404",
    "demand_history", "demand_history_limit_over",
    # allocations
    "alloc_create", "alloc_replay", "alloc_key_mismatch_409", "alloc_unknown_depot_404",
    "alloc_route_mismatch_409", "alloc_route_capacity_409", "alloc_invalid_422",
    "alloc_cancel", "alloc_cancel_again_409", "alloc_cancel_unknown_404",
    "alloc_lifecycle_create", "allocations_after_lifecycle", "metrics_after_lifecycle",
    "stations_before_capacity_probes", "depots_before_capacity_probes",
    "alloc_destination_capacity_409", "alloc_dispatch_leg_1", "alloc_dispatch_capacity_409",
    "alloc_dispatch_leg_1_cancel",
    # events
    "admin_event_route_disruption_no_filter_key", "route_disruption_no_filter_key_during",
    "admin_event_route_disruption_empty_filter", "route_disruption_empty_filter_during",
    "admin_event_route_disruption", "events_during_route_disruption",
    "route_disruption_during", "alloc_route_disrupted_409", "admin_event_station_outage",
    "events_during_station_outage", "station_outage_during", "alloc_station_closed_409",
    "events_list", "routes_after_events",
    # faults
    "admin_fault_stale_data", "depots_stale", "admin_faults_clear_stale_data",
    "depots_fresh", "admin_fault_unavailable", "instance_unavailable_503",
    "health_during_unavailable", "admin_audit_during_unavailable",
    "admin_faults_clear_unavailable", "instance_after_unavailable",
    "admin_fault_stream_disconnect", "stream_disconnect_503",
    "admin_faults_clear_stream_disconnect", "instance_before_latency",
    "admin_fault_latency", "instance_latency", "health_latency",
    "admin_faults_clear_latency", "admin_fault_error_rate", "instance_error_rate_503",
    "admin_faults_clear_error_rate", "instance_after_faults",
    # finale
    "admin_audit", "admin_run", "admin_pause_final", "admin_reset_final",
    "instance_final", "sse_stream",
)  # fmt: skip


@dataclass
class Exchange:
    name: str
    section: str
    method: str
    path: str
    query: dict[str, Any]
    req_body: Any
    status: int
    headers: dict[str, str | None]
    body: Any
    body_text: str | None
    elapsed_ms: float
    sim_tick: int | None
    volatile: bool


def envelope_code(body: Any) -> str | None:
    """Error code from any simulator envelope; ``VALIDATION`` for FastAPI 422 lists."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("detail"), list):
        return "VALIDATION"
    for key in ("detail", "error"):
        inner = body.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("code"), str):
            return str(inner["code"])
    return None


def is_stale(headers: Mapping[str, str | None]) -> bool:
    """True when the stale-data header is present with value ``true`` (any case)."""
    value = headers.get("x-simulator-stale")
    return value is not None and value.strip().lower() == "true"


def normalize(fixture: Any) -> Any:
    """Drop wall-clock keys at any depth so two runs can be compared."""
    if isinstance(fixture, dict):
        return {k: normalize(v) for k, v in fixture.items() if k not in _NORMALIZE_DROP}
    if isinstance(fixture, list):
        return [normalize(v) for v in fixture]
    return fixture


class FixtureWriter:
    """Writes one ``{seq:03d}_{name}.json`` file per exchange, in call order."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob("[0-9][0-9][0-9]_*.json"):
            stale.unlink()
        self.index: list[dict[str, Any]] = []
        self._names: set[str] = set()

    def write(self, ex: Exchange) -> Path:
        return self.write_raw(
            ex.name,
            ex.section,
            {
                "volatile": ex.volatile,
                "request": {
                    "method": ex.method,
                    "path": ex.path,
                    "query": ex.query,
                    "body": ex.req_body,
                },
                "response": {
                    "status": ex.status,
                    "headers": ex.headers,
                    "body": ex.body,
                    "body_text": ex.body_text,
                },
                "sim_tick": ex.sim_tick,
                "elapsed_ms": round(ex.elapsed_ms, 1),
            },
        )

    def write_raw(self, name: str, section: str, payload: dict[str, Any]) -> Path:
        if name in self._names:
            raise ValueError(f"duplicate fixture name: {name}")
        self._names.add(name)
        doc = {
            "fixture_version": FIXTURE_VERSION,
            "provenance": PROVENANCE,
            "name": name,
            "section": section,
            **payload,
        }
        path = self.out_dir / f"{len(self.index) + 1:03d}_{name}.json"
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
        self.index.append(
            {
                "seq": len(self.index) + 1,
                "name": name,
                "file": path.name,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        return path


@dataclass
class SseEvent:
    event: str
    data: Any


@dataclass
class SseSegment:
    """One connection attempt to /v1/stream: its status and every raw line received."""

    status: int
    content_type: str | None
    lines: list[str] = field(default_factory=list)
    events: list[SseEvent] = field(default_factory=list)
    error: str | None = None


def parse_sse_lines(lines: Iterable[str]) -> tuple[list[SseEvent], list[str]]:
    """Parse raw SSE lines into (events, comments). A blank line dispatches an event."""
    events: list[SseEvent] = []
    comments: list[str] = []
    name: str | None = None
    data: list[str] = []
    for line in lines:
        if line == "":
            if data:
                raw = "\n".join(data)
                try:
                    payload: Any = json.loads(raw)
                except ValueError:
                    payload = raw
                events.append(SseEvent(name or "message", payload))
            name, data = None, []
        elif line.startswith(":"):
            comments.append(line[1:].strip())
        else:
            key, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if key == "event":
                name = value
            elif key == "data":
                data.append(value)
    return events, comments


class SseRecorder:
    """Records /v1/stream across the whole run; reconnects as a new segment on EOF or error."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.segments: list[SseSegment] = []
        self.line_times: list[list[float]] = []
        self.crash: str | None = None
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while not self._stop.is_set():
            seg = SseSegment(status=-1, content_type=None)
            times: list[float] = []
            self.segments.append(seg)
            self.line_times.append(times)
            try:
                async with self.client.stream(
                    "GET", "/v1/stream", timeout=httpx.Timeout(10.0, read=None)
                ) as resp:
                    seg.status = resp.status_code
                    seg.content_type = resp.headers.get("content-type")
                    if resp.status_code != 200:
                        await resp.aread()
                        seg.lines.append(resp.text)
                        times.append(time.monotonic())
                    else:
                        async for line in resp.aiter_lines():
                            seg.lines.append(line)
                            times.append(time.monotonic())
            except httpx.HTTPError as exc:
                seg.error = f"{type(exc).__name__}: {exc}"
            try:
                await asyncio.wait_for(self._stop.wait(), SSE_RECONNECT_S)
            except TimeoutError:
                pass

    def _parsed(self) -> tuple[list[SseEvent], list[str]]:
        events: list[SseEvent] = []
        comments: list[str] = []
        for seg in self.segments:
            if seg.status == 200:
                seg_events, seg_comments = parse_sse_lines([*seg.lines, ""])
                events += seg_events
                comments += seg_comments
        return events, comments

    def count(self, event: str | None = None, comment: str | None = None) -> int:
        events, comments = self._parsed()
        if event is not None:
            return sum(e.event == event for e in events)
        return sum(c == comment for c in comments)

    async def wait_for(
        self,
        event: str | None = None,
        comment: str | None = None,
        timeout_s: float = 3.0,
        at_least: int = 1,
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while self.count(event, comment) < at_least:
            if time.monotonic() > deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    async def stop(self, timeout_s: float = 5.0) -> bool:
        """Stop recording; True if the loop ended within the deadline.

        Never raises for a crashed loop (the crash is kept in ``crash``) and never
        swallows a cancellation aimed at the caller.
        """
        self._stop.set()
        stopped = True
        if self._task is not None:
            self._task.cancel()
            done, _ = await asyncio.wait({self._task}, timeout=timeout_s)
            stopped = bool(done)
            if (
                done
                and not self._task.cancelled()
                and self._task.exception() is not None
            ):
                exc = self._task.exception()
                self.crash = f"{type(exc).__name__}: {exc}"
        for seg in self.segments:
            if seg.status == 200:
                seg.events = parse_sse_lines(seg.lines)[0]
        return stopped

    def to_fixture(self) -> dict[str, Any]:
        _, comments = self._parsed()
        return {
            "segments": [asdict(seg) for seg in self.segments],
            "comments_seen": sorted(set(comments)),
        }


class SmokeAbort(Exception):
    """A precondition failed; later sections would only produce noise."""


def _decode(resp: httpx.Response) -> tuple[Any, str | None]:
    if not resp.content:
        return None, None
    if "json" in resp.headers.get("content-type", ""):
        try:
            return resp.json(), None
        except ValueError:
            pass
    return None, resp.text


class SmokeContext:
    """Shared state for one smoke run: client, recorder, checks and discovered world."""

    def __init__(self, client: httpx.AsyncClient, writer: FixtureWriter) -> None:
        self.client = client
        self.writer = writer
        self.section = ""
        self.checks: list[dict[str, Any]] = []
        self.current_tick: int | None = None
        self.topology: dict[str, dict[str, dict[str, Any]]] = {}
        self.manifest: dict[str, Any] = {"instance": {}, "findings": {}, "timings": {}}
        self.sse: SseRecorder | None = None
        self._steps = 0

    async def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        volatile: bool = False,
        record: bool = True,
    ) -> Exchange:
        started = time.perf_counter()
        resp = await self.client.request(method, path, params=params, json=json)
        ex = self.exchange_from(
            resp,
            name,
            method,
            path,
            params,
            json,
            time.perf_counter() - started,
            volatile,
        )
        if record:
            self.writer.write(ex)
        return ex

    def exchange_from(
        self,
        resp: httpx.Response,
        name: str,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json: Any,
        elapsed_s: float,
        volatile: bool = False,
    ) -> Exchange:
        """Build an Exchange from a response whose body has already been read."""
        body, body_text = _decode(resp)
        headers: dict[str, str | None] = {
            k.lower(): v
            for k, v in resp.headers.items()
            if k.lower() not in DROPPED_HEADERS
        }
        headers.setdefault("x-simulator-stale", None)
        return Exchange(
            name=name,
            section=self.section,
            method=method,
            path=path,
            query=dict(params or {}),
            req_body=json,
            status=resp.status_code,
            headers=headers,
            body=body,
            body_text=body_text,
            elapsed_ms=elapsed_s * 1000,
            sim_tick=self.current_tick,
            volatile=volatile,
        )

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {detail}"))
        return bool(ok)

    def expect(
        self, ex: Exchange, status: int | tuple[int, ...], code: str | None = None
    ) -> bool:
        """Check an exchange's status (and envelope code); the check is named after the fixture."""
        wanted = status if isinstance(status, tuple) else (status,)
        got_code = envelope_code(ex.body)
        ok = ex.status in wanted and (code is None or got_code == code)
        detail = f"want {wanted} {code or ''} got {ex.status} {got_code or ''}".strip()
        return self.check(ex.name, ok, detail)

    async def step(self, n: int) -> int:
        """Advance ``n`` ticks via /admin/step, recording each as ``admin_step_NN``."""
        for _ in range(n):
            self._steps += 1
            ex = await self.call(f"admin_step_{self._steps:02d}", "POST", "/admin/step")
            if ex.status != 200 or not isinstance(ex.body, dict):
                raise SmokeAbort(f"/admin/step returned {ex.status}")
            self.current_tick = int(ex.body["tick"])
        return self.current_tick if self.current_tick is not None else -1


async def section_preflight(ctx: SmokeContext) -> None:
    deadline = time.monotonic() + HEALTH_WAIT_S
    while True:
        try:
            if (await ctx.client.get("/v1/health")).status_code == 200:
                break
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            ctx.check(
                "health_ready",
                False,
                f"no 200 from /v1/health within {HEALTH_WAIT_S:.0f} s",
            )
            raise SmokeAbort("simulator not reachable")
        await asyncio.sleep(0.5)

    health = await ctx.call("health", "GET", "/v1/health")
    ctx.check(
        "health",
        health.status == 200
        and isinstance(health.body, dict)
        and health.body.get("status") == "ok",
        f"got {health.status} {health.body}",
    )

    spec = await ctx.call("openapi", "GET", "/openapi.json")
    paths = spec.body.get("paths", {}) if isinstance(spec.body, dict) else {}
    has_admin = "/admin/events" in paths and "/admin/faults" in paths
    ctx.check(
        "openapi",
        spec.status == 200 and has_admin,
        "admin schemas absent (plan Decision 3)",
    )
    if not has_admin:
        raise SmokeAbort("openapi.json lacks admin paths — stop and ask")

    ctx.expect(
        await ctx.call("admin_faults_clear_start", "POST", "/admin/faults/clear"), 200
    )
    ctx.expect(await ctx.call("admin_reset", "POST", "/admin/reset"), 200)
    ctx.expect(await ctx.call("admin_pause", "POST", "/admin/pause"), 200)

    inst = await ctx.call("instance_initial", "GET", "/v1/instance")
    body = inst.body if isinstance(inst.body, dict) else {}
    ctx.check(
        "instance_initial",
        inst.status == 200 and body.get("tick") == 0 and body.get("status") == "PAUSED",
        f"got {inst.status} {body}",
    )
    ctx.current_tick = body.get("tick")
    ctx.manifest["instance"] = {
        k: body.get(k)
        for k in ("seed", "scenario_id", "scenario_version", "tick_minutes")
    }


def _is_list(ex: Exchange) -> bool:
    return ex.status == 200 and isinstance(ex.body, list)


async def section_reads(ctx: SmokeContext) -> None:
    tick = await ctx.step(4)
    ctx.check("admin_steps_advance_ticks", tick == 4, f"tick after 4 steps = {tick}")
    if ctx.sse is not None:
        ok = await ctx.sse.wait_for(event="simulation.tick", at_least=4, timeout_s=3.0)
        ctx.check(
            "sse_tick_after_step", ok, f"{ctx.sse.count('simulation.tick')} tick events"
        )

    lists: dict[str, Exchange] = {}
    for name, path in (
        ("regions", "/v1/regions"),
        ("depots", "/v1/depots"),
        ("stations", "/v1/stations"),
        ("routes", "/v1/routes"),
        ("supply_arrivals", "/v1/supply-arrivals"),
        ("events_initial", "/v1/events"),
        ("allocations_initial", "/v1/allocations"),
    ):
        ex = lists[name] = await ctx.call(name, "GET", path)
        ctx.check(name, _is_list(ex), f"got {ex.status} {type(ex.body).__name__}")
    for name in ("regions", "depots", "stations", "routes"):
        ctx.check(f"{name}_non_empty", bool(lists[name].body), "empty list")
    ctx.check(
        "stale_header_absent_baseline",
        not is_stale(lists["depots"].headers),
        "stale on baseline",
    )
    ctx.topology = {
        kind: {item["id"]: item for item in lists[kind].body or []}
        for kind in ("depots", "stations", "routes")
    }

    metrics = await ctx.call("metrics_initial", "GET", "/v1/metrics")
    ctx.check(
        "metrics_initial",
        metrics.status == 200 and isinstance(metrics.body, dict),
        f"{metrics.status}",
    )
    inst = await ctx.call("instance_after_steps", "GET", "/v1/instance")
    inst_tick = inst.body.get("tick") if isinstance(inst.body, dict) else None
    ctx.check(
        "instance_after_steps",
        inst.status == 200 and inst_tick == 4,
        f"tick {inst_tick}",
    )

    for kind, singular in (("depots", "depot"), ("stations", "station")):
        first_id = next(iter(ctx.topology[kind]))
        one = await ctx.call(f"{singular}_by_id", "GET", f"/v1/{kind}/{first_id}")
        ctx.check(
            f"{singular}_by_id",
            one.status == 200
            and isinstance(one.body, dict)
            and one.body.get("id") == first_id,
            f"got {one.status}",
        )
        ctx.expect(
            await ctx.call(
                f"{singular}_unknown_404", "GET", f"/v1/{kind}/does-not-exist"
            ),
            404,
            "NOT_FOUND",
        )

    station_id = next(iter(ctx.topology["stations"]))
    hist = await ctx.call(
        "demand_history",
        "GET",
        "/v1/demand-history",
        params={"station_id": station_id, "limit": 12},
    )
    rows = hist.body if isinstance(hist.body, list) else []
    ctx.check(
        "demand_history",
        _is_list(hist)
        and 0 < len(rows) <= 12
        and all(r.get("station_id") == station_id for r in rows),
        f"got {hist.status} with {len(rows)} rows",
    )
    over = await ctx.call(
        "demand_history_limit_over", "GET", "/v1/demand-history", params={"limit": 2001}
    )
    ctx.manifest["findings"]["demand_history_limit_2001"] = {
        "status": over.status,
        "rows": len(over.body) if isinstance(over.body, list) else None,
        "code": envelope_code(over.body),
    }


def choose_route_and_quantity(
    topology: Mapping[str, Mapping[str, dict[str, Any]]], fuel: str, cap: float
) -> tuple[dict[str, Any], float]:
    """First AVAILABLE route whose shipment fits the route limit and destination headroom."""
    for route in topology["routes"].values():
        if route.get("status") != "AVAILABLE":
            continue
        station = topology["stations"][route["destination_station_id"]]
        headroom = station["capacity"][fuel] - station["inventory"][fuel]
        qty = min(cap, route["max_shipment"], headroom)
        if qty >= 1:
            return route, qty
    raise SmokeAbort(f"no AVAILABLE route can take >= 1 L of {fuel}")


def other_station(
    topology: Mapping[str, Mapping[str, dict[str, Any]]], route: Mapping[str, Any]
) -> str:
    """A station the route does not serve, for provoking ROUTE_MISMATCH."""
    return next(
        sid for sid in topology["stations"] if sid != route["destination_station_id"]
    )


def _available_routes(
    topology: Mapping[str, Mapping[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    return [r for r in topology["routes"].values() if r.get("status") == "AVAILABLE"]


def _headroom(
    topology: Mapping[str, Mapping[str, dict[str, Any]]],
    route: Mapping[str, Any],
    fuel: str,
) -> float:
    station = topology["stations"][route["destination_station_id"]]
    return float(station["capacity"][fuel] - station["inventory"][fuel])


def destination_overflow(
    topology: Mapping[str, Mapping[str, dict[str, Any]]], fuel: str
) -> tuple[dict[str, Any], float]:
    """A shipment 1 L over the destination's headroom that passes every earlier check."""
    for route in _available_routes(topology):
        depot = topology["depots"][route["source_depot_id"]]
        qty = math.floor(_headroom(topology, route, fuel)) + 1
        limit = min(
            route["max_shipment"],
            depot["inventory"][fuel],
            depot["dispatch_capacity_per_tick"],
        )
        if 0 < qty <= limit:
            return route, qty
    raise SmokeAbort(
        f"no route can overflow a station's {fuel} headroom within its limits"
    )


def dispatch_overflow(
    topology: Mapping[str, Mapping[str, dict[str, Any]]], fuel: str
) -> tuple[str, list[tuple[dict[str, Any], float]]]:
    """Legs from one depot whose total first exceeds its per-tick dispatch capacity.

    Every leg fits its route and destination; only the last one crosses the depot limit.
    """
    for depot_id, depot in topology["depots"].items():
        legs: list[tuple[dict[str, Any], float]] = []
        total = 0.0
        for route in _available_routes(topology):
            if route["source_depot_id"] != depot_id:
                continue
            qty = min(
                route["max_shipment"], math.floor(_headroom(topology, route, fuel))
            )
            if qty < 1:
                continue
            legs.append((route, qty))
            total += qty
            if total > depot["dispatch_capacity_per_tick"]:
                if total <= depot["inventory"][fuel]:
                    return depot_id, legs
                break
    raise SmokeAbort(f"no depot can exceed its dispatch capacity with {fuel} legs")


def mismatch_quantity(qty: float) -> float:
    """A different, still valid quantity for provoking IDEMPOTENCY_KEY_MISMATCH."""
    return qty / 2


def _alloc_body(
    route: Mapping[str, Any], key: str, qty: float, fuel: str = "DIESEL"
) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "source_depot_id": route["source_depot_id"],
        "destination_station_id": route["destination_station_id"],
        "route_id": route["id"],
        "fuel_type": fuel,
        "quantity": qty,
    }


async def section_allocations(ctx: SmokeContext) -> None:
    route, qty = choose_route_and_quantity(ctx.topology, "DIESEL", cap=1000)
    ctx.manifest["findings"]["happy_path"] = {
        "route_id": route["id"],
        "fuel": "DIESEL",
        "quantity": qty,
    }
    body = _alloc_body(route, "p0-create-1", qty)

    created = await ctx.call("alloc_create", "POST", "/v1/allocations", json=body)
    created_body = created.body if isinstance(created.body, dict) else {}
    ctx.check(
        "alloc_create",
        created.status == 201 and created_body.get("status") == "PENDING",
        f"got {created.status} {created_body.get('status')}",
    )
    alloc_id = created_body.get("id")

    replay = await ctx.call("alloc_replay", "POST", "/v1/allocations", json=body)
    replay_id = replay.body.get("id") if isinstance(replay.body, dict) else None
    ctx.manifest["findings"]["replay_status"] = replay.status
    ctx.check(
        "alloc_replay",
        replay.status in (200, 201) and replay_id == alloc_id and alloc_id is not None,
        f"got {replay.status} id {replay_id} (created id {alloc_id})",
    )

    ctx.expect(
        await ctx.call(
            "alloc_key_mismatch_409",
            "POST",
            "/v1/allocations",
            json={**body, "quantity": mismatch_quantity(qty)},
        ),
        409,
        "IDEMPOTENCY_KEY_MISMATCH",
    )
    bad_depot = {
        **_alloc_body(route, "p0-404-1", qty),
        "source_depot_id": "does-not-exist",
    }
    ctx.expect(
        await ctx.call(
            "alloc_unknown_depot_404", "POST", "/v1/allocations", json=bad_depot
        ),
        404,
        "NOT_FOUND",
    )
    mismatch = {
        **_alloc_body(route, "p0-mismatch-1", qty),
        "destination_station_id": other_station(ctx.topology, route),
    }
    ctx.expect(
        await ctx.call(
            "alloc_route_mismatch_409", "POST", "/v1/allocations", json=mismatch
        ),
        409,
        "ROUTE_MISMATCH",
    )
    too_big = _alloc_body(route, "p0-cap-1", route["max_shipment"] + 1)
    ctx.expect(
        await ctx.call(
            "alloc_route_capacity_409", "POST", "/v1/allocations", json=too_big
        ),
        409,
        "ROUTE_CAPACITY_EXCEEDED",
    )
    ctx.expect(
        await ctx.call(
            "alloc_invalid_422",
            "POST",
            "/v1/allocations",
            json=_alloc_body(route, "p0-422-1", 0),
        ),
        422,
        "VALIDATION",
    )

    cancel = await ctx.call(
        "alloc_cancel", "POST", f"/v1/allocations/{alloc_id}/cancel"
    )
    cancel_status = cancel.body.get("status") if isinstance(cancel.body, dict) else None
    ctx.check(
        "alloc_cancel",
        cancel.status == 200 and cancel_status == "CANCELLED",
        f"got {cancel.status} {cancel_status}",
    )
    ctx.expect(
        await ctx.call(
            "alloc_cancel_again_409", "POST", f"/v1/allocations/{alloc_id}/cancel"
        ),
        409,
        "CANNOT_CANCEL",
    )
    ctx.expect(
        await ctx.call(
            "alloc_cancel_unknown_404", "POST", "/v1/allocations/999999/cancel"
        ),
        404,
        "ALLOCATION_NOT_FOUND",
    )

    life = await ctx.call(
        "alloc_lifecycle_create",
        "POST",
        "/v1/allocations",
        json=_alloc_body(route, "p0-lifecycle-1", min(500, qty)),
    )
    ctx.expect(life, 201)
    life_id = life.body.get("id") if isinstance(life.body, dict) else None
    await ctx.step(int(route["transit_ticks"]) + 2)
    ledger = await ctx.call("allocations_after_lifecycle", "GET", "/v1/allocations")
    states = (
        {a.get("id"): a.get("status") for a in ledger.body}
        if isinstance(ledger.body, list)
        else {}
    )
    ctx.check(
        "allocations_after_lifecycle",
        states.get(life_id) == "ARRIVED" and states.get(alloc_id) == "CANCELLED",
        f"lifecycle {states.get(life_id)}, cancelled {states.get(alloc_id)}",
    )
    metrics = await ctx.call("metrics_after_lifecycle", "GET", "/v1/metrics")
    ctx.check("metrics_after_lifecycle", metrics.status == 200, f"got {metrics.status}")
    await _capacity_probes(ctx)
    if ctx.sse is not None:
        for name in ("allocation.status_changed", "inventory.updated"):
            ctx.check(
                f"sse_{name}",
                await ctx.sse.wait_for(event=name, timeout_s=3.0),
                "not seen on stream",
            )


async def _capacity_probes(ctx: SmokeContext) -> None:
    """Reproduce DESTINATION_ and DISPATCH_CAPACITY_EXCEEDED from freshly read inventory."""
    world: dict[str, dict[str, dict[str, Any]]] = {"routes": ctx.topology["routes"]}
    for kind in ("stations", "depots"):
        ex = await ctx.call(f"{kind}_before_capacity_probes", "GET", f"/v1/{kind}")
        world[kind] = {i["id"]: i for i in ex.body} if isinstance(ex.body, list) else {}

    route, qty = destination_overflow(world, "DIESEL")
    ctx.expect(
        await ctx.call(
            "alloc_destination_capacity_409",
            "POST",
            "/v1/allocations",
            json=_alloc_body(route, "p0-dest-cap-1", qty),
        ),
        409,
        "DESTINATION_CAPACITY_EXCEEDED",
    )

    depot_id, legs = dispatch_overflow(world, "DIESEL")
    ctx.manifest["findings"]["dispatch_probe"] = {
        "depot_id": depot_id,
        "legs": [[r["id"], q] for r, q in legs],
        "dispatch_capacity_per_tick": world["depots"][depot_id][
            "dispatch_capacity_per_tick"
        ],
    }
    accepted: list[Any] = []
    for i, (leg_route, leg_qty) in enumerate(legs[:-1], start=1):
        leg = await ctx.call(
            f"alloc_dispatch_leg_{i}",
            "POST",
            "/v1/allocations",
            json=_alloc_body(leg_route, f"p0-dispatch-{i}", leg_qty),
        )
        ctx.expect(leg, 201)
        accepted.append(leg.body.get("id") if isinstance(leg.body, dict) else None)
    last_route, last_qty = legs[-1]
    ctx.expect(
        await ctx.call(
            "alloc_dispatch_capacity_409",
            "POST",
            "/v1/allocations",
            json=_alloc_body(last_route, f"p0-dispatch-{len(legs)}", last_qty),
        ),
        409,
        "DISPATCH_CAPACITY_EXCEEDED",
    )
    for i, alloc_id in enumerate(
        accepted, start=1
    ):  # refund, so later sections see normal stock
        ctx.expect(
            await ctx.call(
                f"alloc_dispatch_leg_{i}_cancel",
                "POST",
                f"/v1/allocations/{alloc_id}/cancel",
            ),
            200,
        )


def _statuses(ex: Exchange) -> dict[Any, str]:
    return {i["id"]: i["status"] for i in ex.body} if isinstance(ex.body, list) else {}


async def _run_event(
    ctx: SmokeContext,
    label: str,
    kind: str,
    parameters: dict[str, Any],
    path: str,
    bad: str,
) -> tuple[Any, list[Any]]:
    """Inject a 2-tick event, step into it, and return (event id, entities now ``bad``)."""
    event = {
        "type": kind,
        "start_tick": ctx.current_tick,
        "duration_ticks": 2,
        "parameters": parameters,
    }
    created = await ctx.call(
        f"admin_event_{label}", "POST", "/admin/events", json=event
    )
    ctx.expect(created, (200, 201))
    event_id = created.body.get("id") if isinstance(created.body, dict) else None
    await ctx.step(1)
    during = await ctx.call(f"events_during_{label}", "GET", "/v1/events")
    ctx.check(
        f"{label}_active",
        _statuses(during).get(event_id) == "ACTIVE",
        f"{_statuses(during)}",
    )
    hit = await ctx.call(f"{label}_during", "GET", path)
    affected = sorted(i for i, st in _statuses(hit).items() if st == bad)
    ctx.manifest["findings"][f"event_{label}_affects"] = affected
    return event_id, affected


async def _await_resolved(ctx: SmokeContext, label: str, event_id: Any) -> None:
    for _ in range(3):
        await ctx.step(1)
        poll = await ctx.call("poll", "GET", "/v1/events", record=False)
        if _statuses(poll).get(event_id) == "RESOLVED":
            ctx.check(f"{label}_resolved", True)
            return
    ctx.check(
        f"{label}_resolved", False, f"event {event_id} not RESOLVED within 3 ticks"
    )


async def section_events(ctx: SmokeContext) -> None:
    route = ctx.topology["routes"][ctx.manifest["findings"]["happy_path"]["route_id"]]
    station_id = route["destination_station_id"]

    # Guide §7.8 says an empty filter list applies the event to every entity of that
    # type. Image 1.0.0 disagrees: both an absent key and an empty list affect nothing,
    # so crisis injection must always name explicit ids. Both forms are pinned here.
    eid, affected = await _run_event(
        ctx,
        "route_disruption_no_filter_key",
        "route_disruption",
        {},
        "/v1/routes",
        "DISRUPTED",
    )
    ctx.check(
        "route_disruption_no_filter_key_affects_none", affected == [], f"{affected}"
    )
    await _await_resolved(ctx, "route_disruption_no_filter_key", eid)

    eid, affected = await _run_event(
        ctx,
        "route_disruption_empty_filter",
        "route_disruption",
        {"route_ids": []},
        "/v1/routes",
        "DISRUPTED",
    )
    ctx.check(
        "route_disruption_empty_filter_affects_none",
        affected == [],
        f"{affected} (guide §7.8 predicts all routes)",
    )
    await _await_resolved(ctx, "route_disruption_empty_filter", eid)

    eid, affected = await _run_event(
        ctx,
        "route_disruption",
        "route_disruption",
        {"route_ids": [route["id"]]},
        "/v1/routes",
        "DISRUPTED",
    )
    ctx.check(
        "route_disruption_targets_route", affected == [route["id"]], f"{affected}"
    )
    ctx.expect(
        await ctx.call(
            "alloc_route_disrupted_409",
            "POST",
            "/v1/allocations",
            json=_alloc_body(route, "p0-disrupted-1", 100),
        ),
        409,
        "ROUTE_DISRUPTED",
    )
    await _await_resolved(ctx, "route_disruption", eid)

    eid, affected = await _run_event(
        ctx,
        "station_outage",
        "station_outage",
        {"station_ids": [station_id]},
        "/v1/stations",
        "OUTAGE",
    )
    ctx.check("station_outage_targets_station", affected == [station_id], f"{affected}")
    ctx.expect(
        await ctx.call(
            "alloc_station_closed_409",
            "POST",
            "/v1/allocations",
            json=_alloc_body(route, "p0-closed-1", 100),
        ),
        409,
        "STATION_CLOSED",
    )
    await _await_resolved(ctx, "station_outage", eid)

    listed = await ctx.call("events_list", "GET", "/v1/events")
    statuses = sorted(_statuses(listed).values())
    ctx.check("events_list", statuses == ["RESOLVED"] * 4, f"{_statuses(listed)}")
    after = await ctx.call("routes_after_events", "GET", "/v1/routes")
    ctx.check(
        "routes_after_events",
        set(_statuses(after).values()) == {"AVAILABLE"},
        f"{_statuses(after)}",
    )


async def probe_stream(ctx: SmokeContext, name: str) -> Exchange:
    """One GET /v1/stream that records a non-200 envelope and never waits on a live stream."""
    started = time.perf_counter()
    async with ctx.client.stream(
        "GET", "/v1/stream", timeout=httpx.Timeout(10.0, read=5.0)
    ) as resp:
        if resp.status_code != 200:
            await resp.aread()
            ex = ctx.exchange_from(
                resp,
                name,
                "GET",
                "/v1/stream",
                None,
                None,
                time.perf_counter() - started,
            )
        else:  # a live stream: record status and type only, never read the body
            ex = Exchange(
                name=name,
                section=ctx.section,
                method="GET",
                path="/v1/stream",
                query={},
                req_body=None,
                status=200,
                headers={
                    "content-type": resp.headers.get("content-type"),
                    "x-simulator-stale": None,
                },
                body=None,
                body_text=None,
                elapsed_ms=(time.perf_counter() - started) * 1000,
                sim_tick=ctx.current_tick,
                volatile=False,
            )
    ctx.writer.write(ex)
    return ex


async def call_until_status(
    ctx: SmokeContext, name: str, path: str, status: int, attempts: int
) -> tuple[Exchange | None, int]:
    """GET ``path`` until it returns ``status``; only that hit becomes a fixture."""
    for attempt in range(1, attempts + 1):
        ex = await ctx.call(name, "GET", path, record=False)
        if ex.status == status:
            ctx.writer.write(ex)
            return ex, attempt
    return None, attempts


def _top_key(ex: Exchange) -> str | None:
    return next(iter(ex.body), None) if isinstance(ex.body, dict) else None


async def _inject_fault(
    ctx: SmokeContext, kind: str, parameters: dict[str, Any] | None = None
) -> None:
    body = {"type": kind, "duration_seconds": 60, "parameters": parameters or {}}
    ctx.expect(
        await ctx.call(f"admin_fault_{kind}", "POST", "/admin/faults", json=body),
        (200, 201),
    )


async def _clear_fault(ctx: SmokeContext, kind: str) -> None:
    ctx.expect(
        await ctx.call(f"admin_faults_clear_{kind}", "POST", "/admin/faults/clear"), 200
    )


async def section_faults(ctx: SmokeContext) -> None:
    await _inject_fault(ctx, "stale_data")
    stale = await ctx.call("depots_stale", "GET", "/v1/depots")
    ctx.check(
        "depots_stale",
        stale.status == 200 and is_stale(stale.headers),
        f"{stale.headers}",
    )
    await _clear_fault(ctx, "stale_data")
    fresh = await ctx.call("depots_fresh", "GET", "/v1/depots")
    ctx.check(
        "depots_fresh",
        fresh.status == 200 and not is_stale(fresh.headers),
        f"{fresh.headers}",
    )

    await _inject_fault(ctx, "unavailable")
    down = await ctx.call("instance_unavailable_503", "GET", "/v1/instance")
    ctx.expect(down, 503, "FAULT_INJECTED")
    ctx.check(
        "unavailable_uses_error_envelope",
        _top_key(down) == "error",
        f"top key {_top_key(down)}",
    )
    ctx.expect(await ctx.call("health_during_unavailable", "GET", "/v1/health"), 200)
    ctx.expect(
        await ctx.call(
            "admin_audit_during_unavailable", "GET", "/admin/audit", params={"limit": 5}
        ),
        200,
    )
    await _clear_fault(ctx, "unavailable")
    ctx.expect(await ctx.call("instance_after_unavailable", "GET", "/v1/instance"), 200)

    await _inject_fault(ctx, "stream_disconnect")
    cut = await probe_stream(ctx, "stream_disconnect_503")
    ctx.expect(cut, 503, "FAULT_INJECTED")
    ctx.check(
        "stream_disconnect_uses_detail_envelope",
        _top_key(cut) == "detail",
        f"top key {_top_key(cut)}",
    )
    await _clear_fault(ctx, "stream_disconnect")

    base = await ctx.call(
        "instance_before_latency", "GET", "/v1/instance", volatile=True
    )
    await _inject_fault(ctx, "latency", {"delay_ms": LATENCY_DELAY_MS})
    slow = await ctx.call("instance_latency", "GET", "/v1/instance", volatile=True)
    health = await ctx.call("health_latency", "GET", "/v1/health", volatile=True)
    await _clear_fault(ctx, "latency")
    delta = slow.elapsed_ms - base.elapsed_ms
    ctx.manifest["timings"]["latency_fault_delta_ms"] = round(delta, 1)
    ctx.manifest["timings"]["latency_fault_health_ms"] = round(health.elapsed_ms, 1)
    ctx.check(
        "latency_applied",
        slow.status == 200 and delta >= LATENCY_DELAY_MS * 0.8,
        f"delta {delta:.0f} ms for delay_ms={LATENCY_DELAY_MS}",
    )
    ctx.check(
        "latency_health_bypass",
        health.elapsed_ms + 100 <= slow.elapsed_ms,
        f"health {health.elapsed_ms:.0f} ms vs instance {slow.elapsed_ms:.0f} ms",
    )

    await _inject_fault(
        ctx, "error_rate", {"rate": 1.0}
    )  # rate 1.0 keeps the run deterministic
    hit, attempts = await call_until_status(
        ctx, "instance_error_rate_503", "/v1/instance", 503, 3
    )
    ctx.manifest["timings"]["error_rate_attempts_to_first_503"] = attempts
    ctx.check(
        "instance_error_rate_503",
        hit is not None
        and attempts == 1
        and envelope_code(hit.body) == "FAULT_INJECTED",
        f"first 503 after {attempts} attempts (hit={hit is not None})",
    )
    await _clear_fault(ctx, "error_rate")

    after = await ctx.call("instance_after_faults", "GET", "/v1/instance")
    ctx.check(
        "instance_after_faults",
        after.status == 200 and not is_stale(after.headers),
        f"{after.status}",
    )
    if ctx.sse is not None:
        ctx.manifest["findings"]["sse_segments_after_faults"] = [
            {"status": seg.status, "lines": len(seg.lines), "error": seg.error}
            for seg in ctx.sse.segments
        ]


def _keepalive_gap_s(rec: SseRecorder) -> float | None:
    """Seconds between the first ``: keepalive`` and the line before it."""
    for seg, times in zip(rec.segments, rec.line_times, strict=True):
        for i, line in enumerate(seg.lines):
            if line == ": keepalive" and i > 0:
                return round(times[i] - times[i - 1], 2)
    return None


async def section_finale(ctx: SmokeContext) -> None:
    if ctx.sse is not None:
        kept = await ctx.sse.wait_for(
            comment="keepalive", timeout_s=SSE_KEEPALIVE_WAIT_S
        )
        ctx.manifest["timings"]["sse_keepalive_gap_s"] = _keepalive_gap_s(ctx.sse)
        ctx.check(
            "sse_keepalive", kept, f"no keepalive within {SSE_KEEPALIVE_WAIT_S:.0f} s"
        )

    audit = await ctx.call("admin_audit", "GET", "/admin/audit", params={"limit": 50})
    ctx.check(
        "admin_audit", _is_list(audit) and len(audit.body or []) > 0, f"{audit.status}"
    )

    # run advances the wall-clock-paced clock, so both bodies are volatile
    ctx.expect(await ctx.call("admin_run", "POST", "/admin/run", volatile=True), 200)
    ctx.expect(
        await ctx.call("admin_pause_final", "POST", "/admin/pause", volatile=True), 200
    )

    notices = ctx.sse.count("simulator.notice") if ctx.sse is not None else 0
    ctx.expect(await ctx.call("admin_reset_final", "POST", "/admin/reset"), 200)
    ctx.current_tick = 0
    if ctx.sse is not None:
        ok = await ctx.sse.wait_for(
            event="simulator.notice", at_least=notices + 1, timeout_s=3.0
        )
        ctx.check("sse_reset_notice", ok, "no simulator.notice after /admin/reset")
    final = await ctx.call("instance_final", "GET", "/v1/instance")
    final_tick = final.body.get("tick") if isinstance(final.body, dict) else None
    ctx.check(
        "instance_final", final.status == 200 and final_tick == 0, f"tick {final_tick}"
    )
    if ctx.sse is not None:
        missing = [n for n in SSE_EVENT_NAMES if ctx.sse.count(n) == 0]
        ctx.check("sse_all_event_names_seen", not missing, f"missing {missing}")


def _load_fixtures(directory: Path) -> list[tuple[str, dict[str, Any]]]:
    return [
        (path.name.split("_", 1)[1].removesuffix(".json"), json.loads(path.read_text()))
        for path in sorted(directory.glob("[0-9][0-9][0-9]_*.json"))
    ]


def _sse_sequence(doc: Mapping[str, Any]) -> list[Any]:
    return [
        [e["event"], normalize(e["data"])]
        for seg in doc.get("segments", [])
        if seg.get("status") == 200
        for e in seg.get("events", [])
    ]


def compare_dirs(recorded: Path, fresh: Path) -> list[str]:
    """Differences between two runs after dropping wall-clock data; [] means deterministic."""
    old, new = _load_fixtures(recorded), _load_fixtures(fresh)
    old_names, new_names = [n for n, _ in old], [n for n, _ in new]
    if old_names != new_names:
        missing = [n for n in old_names if n not in new_names]
        extra = [n for n in new_names if n not in old_names]
        return [f"fixture set/order differs: missing {missing}, extra {extra}"]
    diffs: list[str] = []
    for (name, a), (_, b) in zip(old, new, strict=True):
        if a.get("volatile") or b.get("volatile"):
            continue
        if name == "sse_stream":
            if _sse_sequence(a) != _sse_sequence(b):
                diffs.append("sse_stream: event sequence differs")
        elif normalize(a) != normalize(b):
            diffs.append(f"{name}: normalized fixture differs")
    return diffs


Section = Callable[[SmokeContext], Awaitable[None]]
SECTIONS: list[tuple[str, Section]] = [
    ("preflight", section_preflight),
    ("reads", section_reads),
    ("allocations", section_allocations),
    ("events", section_events),
    ("faults", section_faults),
    ("finale", section_finale),
]


async def _start_sse(ctx: SmokeContext) -> None:
    """Open the stream recorder; the first line on the wire must be the connect comment."""
    ctx.sse = SseRecorder(ctx.client)
    ctx.sse.start()
    connected = await ctx.sse.wait_for(comment="connected", timeout_s=5.0)
    first = (
        ctx.sse.segments[0].lines[0]
        if ctx.sse.segments and ctx.sse.segments[0].lines
        else None
    )
    ctx.check(
        "sse_connected_comment_first",
        connected and first == ": connected",
        f"first line {first!r}",
    )


async def _cleanup(client: httpx.AsyncClient) -> None:
    """Never leave a fault active or the clock running, whatever happened."""
    for path in ("/admin/faults/clear", "/admin/pause"):
        try:
            await client.post(path)
        except httpx.HTTPError as exc:
            print(f"  WARN  cleanup {path} failed: {exc}", file=sys.stderr)


def _write_manifest(
    ctx: SmokeContext, out_dir: Path, meta: Mapping[str, Any]
) -> dict[str, Any]:
    manifest = {
        "fixture_version": FIXTURE_VERSION,
        "provenance": PROVENANCE,
        **meta,
        **ctx.manifest,
        "fixtures": ctx.writer.index,
        "checks": ctx.checks,
        "all_passed": bool(ctx.checks) and all(c["ok"] for c in ctx.checks),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


async def run_smoke(
    client: httpx.AsyncClient,
    out_dir: Path,
    only: set[str] | None,
    meta: Mapping[str, Any] | None = None,
) -> int:
    """Run the selected sections in order; returns the process exit code."""
    ctx = SmokeContext(client, FixtureWriter(out_dir))
    sections_run: list[str] = []
    aborted = False
    try:
        for name, section in SECTIONS:
            if only and name not in only:
                continue
            ctx.section = name
            sections_run.append(name)
            print(f"[{name}]")
            try:
                await section(ctx)
                if name == "preflight":
                    await _start_sse(ctx)
            except Exception as exc:  # noqa: BLE001 - any failure aborts the run, cleanup still runs
                ctx.check(
                    f"section_{name}_completed", False, f"{type(exc).__name__}: {exc}"
                )
                aborted = True
                break
    finally:
        await _cleanup(client)
        if ctx.sse is not None:
            ctx.check(
                "sse_stop_within_deadline",
                await ctx.sse.stop(),
                "recorder did not stop in 5 s",
            )
            ctx.check("sse_recorder_healthy", ctx.sse.crash is None, f"{ctx.sse.crash}")
            ctx.writer.write_raw("sse_stream", "sse", ctx.sse.to_fixture())
        if not aborted and len(sections_run) == len(SECTIONS):
            recorded = {entry["name"] for entry in ctx.writer.index}
            missing = [n for n in REQUIRED_FIXTURES if n not in recorded]
            ctx.check("required_fixtures_recorded", not missing, f"missing {missing}")
        ctx.manifest["sections_run"] = sections_run
        manifest = _write_manifest(ctx, out_dir, meta or {})
    return 0 if manifest["all_passed"] else 1


def _run_quiet(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def git_meta(runner: Callable[[list[str]], str | None] = _run_quiet) -> dict[str, Any]:
    """HEAD plus whether tracked code differs from it (fixtures and evidence excluded)."""
    status = runner(
        ["git", "status", "--porcelain", "--untracked-files=no", "--", ".",
         ":!tests/fixtures", ":!evidence"]
    )  # fmt: skip
    return {"git_sha": runner(["git", "rev-parse", "HEAD"]), "git_dirty": bool(status)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--section", action="append", dest="sections", choices=[n for n, _ in SECTIONS]
    )
    parser.add_argument(
        "--compare-to",
        type=Path,
        help="fixture dir that this run must reproduce exactly",
    )
    args = parser.parse_args(argv)
    out = args.out.resolve()
    if args.compare_to is not None and args.compare_to.resolve() == out:
        parser.error(
            "--compare-to must differ from --out: the run rewrites --out first"
        )
    if args.sections and out == DEFAULT_OUT.resolve():
        parser.error(
            "--section runs are partial: pass --out <scratch dir> to keep fixtures"
        )

    meta = {
        "image": IMAGE,
        "image_digest": _run_quiet(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{index .RepoDigests 0}}",
                IMAGE,
            ]
        ),
        **git_meta(),
        "base_url": args.base_url,
    }

    async def _run() -> int:
        timeout = httpx.Timeout(10.0, connect=2.0)
        async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
            return await run_smoke(client, args.out, set(args.sections or ()), meta)

    code = asyncio.run(_run())
    manifest = json.loads((args.out / "manifest.json").read_text())

    if args.compare_to is not None:
        diffs = compare_dirs(args.compare_to, args.out)
        manifest["determinism_compare"] = {
            "against": str(args.compare_to),
            "diffs": diffs,
        }
        print(
            f"DETERMINISM vs {args.compare_to}: {'identical' if not diffs else 'DIFFERS'}"
        )
        for diff in diffs:
            print(f"  DIFF  {diff}")
        if diffs:
            code = 1

    if not args.sections:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        report = EVIDENCE_DIR / f"contract-smoke-{stamp}.json"
        report.write_text(
            json.dumps({**manifest, "exit_code": code}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"evidence: {report}")

    passed = sum(c["ok"] for c in manifest["checks"])
    print(f"CONTRACT SMOKE: {passed}/{len(manifest['checks'])} checks passed")
    return code


if __name__ == "__main__":
    sys.exit(main())
