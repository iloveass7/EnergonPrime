"""Choosing a feasible happy-path allocation from live topology."""

import pytest

from scripts.contract_smoke import SmokeAbort, choose_route_and_quantity, other_station


def _topology(routes: list[dict], stations: dict[str, dict]) -> dict:
    return {"depots": {}, "stations": stations, "routes": {r["id"]: r for r in routes}}


def _station(inv: float, cap: float) -> dict:
    return {"capacity": {"DIESEL": cap}, "inventory": {"DIESEL": inv}}


def _route(
    rid: str, station: str, max_shipment: float, status: str = "AVAILABLE"
) -> dict:
    return {
        "id": rid,
        "source_depot_id": "d1",
        "destination_station_id": station,
        "max_shipment": max_shipment,
        "status": status,
    }


def test_quantity_capped_by_destination_headroom() -> None:
    topo = _topology([_route("r1", "s1", 7000)], {"s1": _station(inv=14400, cap=15000)})
    route, qty = choose_route_and_quantity(topo, "DIESEL", cap=1000)
    assert (route["id"], qty) == ("r1", 600)


def test_skips_disrupted_and_full_routes() -> None:
    topo = _topology(
        [
            _route("r1", "s1", 7000, "DISRUPTED"),
            _route("r2", "s2", 7000),
            _route("r3", "s3", 700),
        ],
        {
            "s1": _station(0, 15000),
            "s2": _station(15000, 15000),
            "s3": _station(0, 15000),
        },
    )
    route, qty = choose_route_and_quantity(topo, "DIESEL", cap=1000)
    assert (route["id"], qty) == ("r3", 700)


def test_no_feasible_route_aborts() -> None:
    topo = _topology([_route("r1", "s1", 7000)], {"s1": _station(15000, 15000)})
    with pytest.raises(SmokeAbort):
        choose_route_and_quantity(topo, "DIESEL", cap=1000)


def test_other_station_differs_from_route_destination() -> None:
    topo = _topology(
        [_route("r1", "s1", 7000)], {"s1": _station(0, 1), "s2": _station(0, 1)}
    )
    assert other_station(topo, topo["routes"]["r1"]) == "s2"


def _world(
    routes: list[dict],
    stations: dict[str, dict],
    cap: float = 12000,
    inv: float = 60000,
) -> dict:
    depot = {
        "id": "d1",
        "dispatch_capacity_per_tick": cap,
        "inventory": {"DIESEL": inv},
    }
    return {
        "depots": {"d1": depot},
        "stations": stations,
        "routes": {r["id"]: r for r in routes},
    }


def test_destination_overflow_exceeds_headroom_by_one_litre() -> None:
    from scripts.contract_smoke import destination_overflow

    world = _world(
        [_route("r1", "s1", 5000), _route("r2", "s2", 7000)],
        {"s1": _station(0, 15000), "s2": _station(inv=8999.4, cap=15000)},
    )
    route, qty = destination_overflow(world, "DIESEL")
    assert (route["id"], qty) == ("r2", 6001)


def test_dispatch_overflow_last_leg_crosses_depot_capacity() -> None:
    from scripts.contract_smoke import dispatch_overflow

    world = _world(
        [_route("r1", "s1", 6500), _route("r2", "s2", 7000), _route("r3", "s3", 5000)],
        {
            "s1": _station(0, 18000),
            "s2": _station(9000, 15000),
            "s3": _station(0, 14000),
        },
    )
    depot_id, legs = dispatch_overflow(world, "DIESEL")
    assert depot_id == "d1"
    assert [(r["id"], q) for r, q in legs] == [("r1", 6500), ("r2", 6000)]


def test_dispatch_overflow_impossible_aborts() -> None:
    from scripts.contract_smoke import dispatch_overflow

    world = _world([_route("r1", "s1", 5000)], {"s1": _station(0, 15000)})
    with pytest.raises(SmokeAbort):
        dispatch_overflow(world, "DIESEL")
