"""Independent replay validator (pipeline §3.3). Shares no code with intelligence.allocation.

Checks a set of legs against a snapshot exactly as the simulator would, plus our own
safety rules, and returns violations that mirror simulator codes where one exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from fuelops.domain.snapshot import Snapshot

VALID_FUELS = {"DIESEL", "PETROL", "OCTANE"}


@dataclass(frozen=True)
class LegSpec:
    route_id: str
    depot_id: str
    station_id: str
    fuel_type: str
    quantity: float


@dataclass(frozen=True)
class Violation:
    leg: int
    code: str
    detail: str


def validate_legs(
    snapshot: Snapshot, legs: list[LegSpec], *, min_leg: float = 200.0
) -> list[Violation]:
    out: list[Violation] = []
    depot_fuel_sum: dict[tuple[str, str], float] = {}
    depot_sum: dict[str, float] = {}
    station_sum: dict[tuple[str, str], float] = {}
    pending_now: dict[str, float] = {}
    for a in snapshot.allocations:
        if a.status == "PENDING" or (
            a.status == "IN_TRANSIT" and a.departure_tick == snapshot.tick
        ):
            pending_now[a.source_depot_id] = pending_now.get(a.source_depot_id, 0.0) + a.quantity

    for i, leg in enumerate(legs):
        route = snapshot.route(leg.route_id)
        depot = snapshot.depot(leg.depot_id)
        station = snapshot.station(leg.station_id)
        if route is None or depot is None or station is None:
            out.append(Violation(i, "NOT_FOUND", "unknown route, depot or station"))
            continue
        if (route.source_depot_id, route.destination_station_id) != (
            leg.depot_id,
            leg.station_id,
        ):
            out.append(
                Violation(
                    i,
                    "ROUTE_MISMATCH",
                    f"{route.id} does not connect {leg.depot_id} -> {leg.station_id}",
                )
            )
        if depot.status not in ("OPEN", "CONSTRAINED"):
            out.append(Violation(i, "DEPOT_CLOSED", f"{depot.id} is {depot.status}"))
        if station.status != "OPEN":
            out.append(Violation(i, "STATION_CLOSED", f"{station.id} is {station.status}"))
        if route.status != "AVAILABLE":
            out.append(Violation(i, "ROUTE_DISRUPTED", f"{route.id} is {route.status}"))
        if leg.fuel_type not in VALID_FUELS:
            out.append(Violation(i, "BAD_FUEL", leg.fuel_type))
            continue
        if not 0 < leg.quantity <= route.max_shipment:
            out.append(
                Violation(
                    i,
                    "ROUTE_CAPACITY_EXCEEDED",
                    f"{leg.quantity} vs max {route.max_shipment}",
                )
            )
        if leg.quantity < min_leg:
            out.append(Violation(i, "BELOW_MIN_LEG", f"{leg.quantity} < {min_leg}"))

        dk = (depot.id, leg.fuel_type)
        depot_fuel_sum[dk] = depot_fuel_sum.get(dk, 0.0) + leg.quantity
        if depot_fuel_sum[dk] > depot.inventory.get(leg.fuel_type, 0.0):
            out.append(Violation(i, "INSUFFICIENT_INVENTORY", f"{depot.id} {leg.fuel_type}"))
        depot_sum[depot.id] = depot_sum.get(depot.id, 0.0) + leg.quantity
        if depot_sum[depot.id] + pending_now.get(depot.id, 0.0) > depot.dispatch_capacity_per_tick:
            out.append(Violation(i, "DISPATCH_CAPACITY_EXCEEDED", f"{depot.id} this tick"))
        sk = (station.id, leg.fuel_type)
        station_sum[sk] = station_sum.get(sk, 0.0) + leg.quantity
        if station.inventory.get(leg.fuel_type, 0.0) + station_sum[sk] > station.capacity.get(
            leg.fuel_type, 0.0
        ):
            out.append(
                Violation(i, "DESTINATION_CAPACITY_EXCEEDED", f"{station.id} {leg.fuel_type}")
            )
    return out
