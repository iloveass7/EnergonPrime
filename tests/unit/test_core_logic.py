"""Focused tests on the decision + ACL logic (built on the recorded Phase 0 fixtures)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pytest

from fuelops.domain.models import Allocation, Depot, Instance, Metrics, Region, Route, Station
from fuelops.domain.snapshot import Snapshot
from fuelops.intelligence.allocation.greedy import plan_greedy
from fuelops.intelligence.scoring.risk import project, score_all
from fuelops.intelligence.validate.validator import LegSpec, validate_legs
from fuelops.simclient.admin import EventInjection
from fuelops.simclient.breaker import CircuitBreaker
from fuelops.simclient.client import SimulatorClient
from fuelops.simclient.errors import SimCircuitOpen, SimRejected, SimUnavailable, map_status

FIX = Path(__file__).resolve().parents[1] / "fixtures/contract/sim-1.0.0"


def _body(name: str) -> object:
    path = next(FIX.glob(f"[0-9][0-9][0-9]_{name}.json"))
    return json.loads(path.read_text())["response"]["body"]


def _snapshot(tick_override: int | None = None, **station_inv: float) -> Snapshot:
    inst = Instance.model_validate(_body("instance_after_steps"))
    if tick_override is not None:
        inst = inst.model_copy(update={"tick": tick_override})
    stations = [Station.model_validate(s) for s in _body("stations")]  # type: ignore[union-attr]
    if station_inv:
        stations = [
            s.model_copy(
                update={
                    "inventory": {
                        **s.inventory,
                        "DIESEL": station_inv.get(s.id, s.inventory["DIESEL"]),
                    }
                }
            )
            for s in stations
        ]
    return Snapshot(
        instance=inst,
        regions=[Region.model_validate(r) for r in _body("regions")],  # type: ignore[union-attr]
        depots=[Depot.model_validate(d) for d in _body("depots")],  # type: ignore[union-attr]
        stations=stations,
        routes=[Route.model_validate(r) for r in _body("routes")],  # type: ignore[union-attr]
        supply=[],
        events=[],
        allocations=[],
        metrics=Metrics.model_validate(_body("metrics_initial")),
        fetched_at=time.time(),
    )


def test_recorded_envelopes_map_to_typed_errors() -> None:
    assert isinstance(map_status(503, _body("instance_unavailable_503")), SimUnavailable)
    err = map_status(409, _body("alloc_route_disrupted_409"))
    assert isinstance(err, SimRejected) and err.code == "ROUTE_DISRUPTED"
    assert map_status(422, _body("alloc_invalid_422")).code == "VALIDATION"


def test_breaker_opens_after_five_failures_and_half_opens() -> None:
    now = [0.0]
    b = CircuitBreaker(clock=lambda: now[0])
    for _ in range(5):
        b.failure()
    assert b.state == "open" and not b.allow()
    now[0] = 5.1
    assert b.allow() and not b.allow()  # exactly one half-open probe
    b.success()
    assert b.state == "closed"


def test_client_retries_503_then_opens_circuit() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={"error": {"code": "FAULT_INJECTED"}})

    async def run() -> None:
        client = SimulatorClient("http://sim", transport=httpx.MockTransport(handler))
        with pytest.raises(SimUnavailable):
            await client.instance()
        with pytest.raises((SimUnavailable, SimCircuitOpen)):
            await client.instance()
        with pytest.raises(SimCircuitOpen):
            await client.instance()
        await client.aclose()

    asyncio.run(run())
    assert len(calls) == 5  # 3 attempts, then 2 more until the 5-failure streak opens it


def test_stale_header_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_body("depots"), headers={"X-Simulator-Stale": "true"})

    async def run() -> bool:
        client = SimulatorClient("http://sim", transport=httpx.MockTransport(handler))
        result = await client.depots()
        await client.aclose()
        return result.stale

    assert asyncio.run(run()) is True


def test_projection_counts_arrivals_once_and_never_negative() -> None:
    inv, unmet = project(100.0, np.array([60.0, 60.0, 60.0]), np.array([0.0, 50.0, 0.0]))
    assert inv.tolist() == [40.0, 30.0, 0.0]
    assert unmet.tolist() == [0.0, 0.0, 30.0]


def test_planner_legs_always_pass_the_independent_validator() -> None:
    snap = _snapshot(
        **{"station-tongi": 500.0, "station-mirpur": 800.0, "station-karnaphuli": 300.0}
    )
    keys, _ = score_all(snap, 32)
    plan = plan_greedy(snap, keys)
    assert plan.legs, "a drained network must produce shipments"
    legs = [
        LegSpec(x.route_id, x.depot_id, x.station_id, x.fuel_type, x.quantity) for x in plan.legs
    ]
    assert validate_legs(snap, legs) == []
    per_depot: dict[str, float] = {}
    for leg in plan.legs:
        per_depot[leg.depot_id] = per_depot.get(leg.depot_id, 0.0) + leg.quantity
    caps = {d.id: d.dispatch_capacity_per_tick for d in snap.depots}
    assert all(q <= caps[d] for d, q in per_depot.items())


def test_validator_rejects_each_broken_rule() -> None:
    snap = _snapshot()
    ok = LegSpec("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 1000)
    assert validate_legs(snap, [ok]) == []
    cases = {
        "ROUTE_MISMATCH": LegSpec(
            "route-gazipur-mirpur", "depot-gazipur", "station-tongi", "DIESEL", 1000
        ),
        "ROUTE_CAPACITY_EXCEEDED": LegSpec(
            "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 7001
        ),
        "DESTINATION_CAPACITY_EXCEEDED": LegSpec(
            "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 6999
        ),
        "BELOW_MIN_LEG": LegSpec(
            "route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 50
        ),
    }
    for code, leg in cases.items():
        assert code in {v.code for v in validate_legs(snap, [leg])}, code
    two = [LegSpec("route-gazipur-mirpur", "depot-gazipur", "station-mirpur", "DIESEL", 5600),
           LegSpec("route-gazipur-tongi", "depot-gazipur", "station-tongi", "DIESEL", 6500)]  # fmt: skip
    assert "DISPATCH_CAPACITY_EXCEEDED" in {v.code for v in validate_legs(snap, two)}


def test_pending_allocation_counts_as_inbound() -> None:
    snap = _snapshot(**{"station-tongi": 300.0})
    before = next(
        r
        for r in score_all(snap, 16)[1]
        if r.station_id == "station-tongi" and r.fuel_type == "DIESEL"
    )
    snap.allocations = [Allocation(id=1, idempotency_key="k", source_depot_id="depot-gazipur",
                                   destination_station_id="station-tongi", route_id="route-gazipur-tongi",
                                   fuel_type="DIESEL", quantity=5000, created_tick=snap.tick, status="PENDING")]  # fmt: skip
    after = next(
        r
        for r in score_all(snap, 16)[1]
        if r.station_id == "station-tongi" and r.fuel_type == "DIESEL"
    )
    assert after.inbound == 5000 and after.expected_unmet < before.expected_unmet


def test_test_plane_rejects_empty_event_filters() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        EventInjection(
            type="route_disruption", start_tick=0, duration_ticks=2, parameters={"route_ids": []}
        )
    EventInjection(
        type="route_disruption", start_tick=0, duration_ticks=2, parameters={"route_ids": ["r"]}
    )
