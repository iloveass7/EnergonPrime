"""P0 planner: marginal-benefit greedy (pipeline §4.3, CLAUDE.md override).

Each pick is the feasible leg (route, fuel, quantity) that removes the most projected unmet
liters, recomputed after every pick with resources reserved jointly across legs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from fuelops.domain.snapshot import Snapshot
from fuelops.intelligence.scoring.risk import KeyState, project

SAFETY_QUANTILE_Z = 1.2816  # ~0.9 quantile of cumulative demand when sizing a leg
MAX_LEGS = 8


@dataclass
class Leg:
    route_id: str
    depot_id: str
    station_id: str
    fuel_type: str
    quantity: float
    transit_ticks: int
    arrival_index: int
    unmet_before: float
    unmet_after: float
    binding: str | None = None  # the limit that cut the leg below its sized need

    @property
    def benefit(self) -> float:
        return self.unmet_before - self.unmet_after


@dataclass
class Alternative:
    route_id: str
    depot_id: str
    quantity: float
    unmet_after: float
    reason: str


@dataclass
class PlanResult:
    legs: list[Leg]
    alternatives: dict[tuple[str, str], list[Alternative]] = field(default_factory=dict)
    binding: dict[tuple[str, str], str] = field(default_factory=dict)


@dataclass
class Resources:
    depot_inventory: dict[tuple[str, str], float]
    dispatch_left: dict[str, float]
    station_room: dict[tuple[str, str], float]


def initial_resources(snapshot: Snapshot, reserve_fraction: float) -> Resources:
    now = snapshot.tick
    pending_now: dict[str, float] = {}
    inbound: dict[tuple[str, str], float] = {}
    for a in snapshot.allocations:
        if a.status == "PENDING" or (a.status == "IN_TRANSIT" and a.departure_tick == now):
            pending_now[a.source_depot_id] = pending_now.get(a.source_depot_id, 0.0) + a.quantity
        if a.status in ("PENDING", "IN_TRANSIT"):
            key = (a.destination_station_id, a.fuel_type)
            inbound[key] = inbound.get(key, 0.0) + a.quantity
    depot_inventory: dict[tuple[str, str], float] = {
        (d.id, f): max(0.0, q - reserve_fraction * d.capacity.get(f, 0.0))
        for d in snapshot.depots
        for f, q in d.inventory.items()
    }
    dispatch_left = {
        d.id: max(0.0, d.dispatch_capacity_per_tick - pending_now.get(d.id, 0.0))
        for d in snapshot.depots
    }
    # Simulator checks current inventory; we also count inbound so arrivals never overflow.
    station_room: dict[tuple[str, str], float] = {
        (s.id, f): max(0.0, s.capacity[f] - q - inbound.get((s.id, f), 0.0))
        for s in snapshot.stations
        for f, q in s.inventory.items()
    }
    return Resources(depot_inventory, dispatch_left, station_room)


def size_leg(key: KeyState, arrivals: np.ndarray, arrival_index: int) -> float:
    """Liters needed at ``arrival_index`` to cover the ~90% quantile of demand to the horizon."""
    mu = key.mu
    cum_mu = np.cumsum(mu)
    sigma = np.sqrt(np.cumsum((key.noise * mu) ** 2) + (0.05 * cum_mu) ** 2)
    avail = key.inventory + np.cumsum(arrivals)
    shortfall = cum_mu + SAFETY_QUANTILE_Z * sigma - avail
    return float(max(0.0, shortfall[arrival_index:].max(initial=0.0)))


def plan_greedy(
    snapshot: Snapshot,
    keys: list[KeyState],
    *,
    reserve_fraction: float = 0.05,
    min_leg: float = 200.0,
    max_legs: int = MAX_LEGS,
) -> PlanResult:
    res = initial_resources(snapshot, reserve_fraction)
    horizon = len(keys[0].mu) if keys else 0
    by_key = {(k.station_id, k.fuel): k for k in keys}
    planned: dict[tuple[str, str], np.ndarray] = {
        key: k.arrivals.copy() for key, k in by_key.items()
    }
    depots = {d.id: d for d in snapshot.depots}
    stations = {s.id: s for s in snapshot.stations}
    result = PlanResult(legs=[])

    for _ in range(max_legs):
        best: Leg | None = None
        for route in snapshot.routes:
            depot = depots.get(route.source_depot_id)
            station = stations.get(route.destination_station_id)
            if depot is None or station is None:
                continue
            arrival_index = route.transit_ticks - 1  # observed: arrives at created_tick + transit
            if arrival_index >= horizon:
                continue
            for fuel in ("DIESEL", "PETROL", "OCTANE"):
                key = by_key.get((station.id, fuel))
                if key is None or not key.is_open:
                    continue
                _, unmet = project(key.inventory, key.mu, planned[(station.id, fuel)])
                before = float(unmet.sum())
                if before <= 1.0:
                    continue
                blocked = None
                if route.status != "AVAILABLE":
                    blocked = f"route {route.status}"
                elif depot.status not in ("OPEN", "CONSTRAINED"):
                    blocked = f"depot {depot.status}"
                need = size_leg(key, planned[(station.id, fuel)], arrival_index)
                limits = {
                    "route max_shipment": route.max_shipment,
                    "station capacity": res.station_room[(station.id, fuel)],
                    "depot inventory": res.depot_inventory.get((depot.id, fuel), 0.0),
                    "depot dispatch capacity": res.dispatch_left.get(depot.id, 0.0),
                }
                binding_name, cap = min(limits.items(), key=lambda kv: kv[1])
                qty = math.floor(min(need, cap))
                if blocked is None and qty < min_leg:
                    blocked = f"{binding_name} leaves {max(qty, 0):,.0f} L"
                alt_key = (station.id, fuel)
                if blocked is not None:
                    result.alternatives.setdefault(alt_key, []).append(
                        Alternative(route.id, depot.id, 0.0, before, blocked)
                    )
                    continue
                trial = planned[(station.id, fuel)].copy()
                trial[arrival_index] += qty
                _, unmet_after = project(key.inventory, key.mu, trial)
                leg = Leg(
                    route_id=route.id,
                    depot_id=depot.id,
                    station_id=station.id,
                    fuel_type=fuel,
                    quantity=float(qty),
                    transit_ticks=route.transit_ticks,
                    arrival_index=arrival_index,
                    unmet_before=before,
                    unmet_after=float(unmet_after.sum()),
                    binding=binding_name if cap < need else None,
                )
                if leg.benefit <= 0:
                    continue
                result.alternatives.setdefault(alt_key, []).append(
                    Alternative(route.id, depot.id, leg.quantity, leg.unmet_after, "feasible")
                )
                if best is None or (leg.benefit, -leg.transit_ticks) > (
                    best.benefit,
                    -best.transit_ticks,
                ):
                    best = leg
        if best is None:
            break
        result.legs.append(best)
        key2 = (best.station_id, best.fuel_type)
        planned[key2][best.arrival_index] += best.quantity
        res.station_room[key2] -= best.quantity
        res.depot_inventory[(best.depot_id, best.fuel_type)] -= best.quantity
        res.dispatch_left[best.depot_id] -= best.quantity
        result.alternatives = {}  # recomputed each round; keep only the final round's view
    # final alternatives view for explanation
    _collect_final_alternatives(snapshot, keys, planned, res, result, min_leg)
    return result


def _collect_final_alternatives(
    snapshot: Snapshot,
    keys: list[KeyState],
    planned: dict[tuple[str, str], np.ndarray],
    res: Resources,
    result: PlanResult,
    min_leg: float,
) -> None:
    chosen = {(leg.station_id, leg.fuel_type): leg for leg in result.legs}
    by_key = {(k.station_id, k.fuel): k for k in keys}
    for (station_id, fuel), leg in chosen.items():
        key = by_key[(station_id, fuel)]
        without = planned[(station_id, fuel)].copy()
        without[leg.arrival_index] -= leg.quantity
        alts: list[Alternative] = []
        for route in snapshot.routes:
            if route.destination_station_id != station_id or route.id == leg.route_id:
                continue
            idx = route.transit_ticks - 1
            if route.status != "AVAILABLE":
                alts.append(
                    Alternative(
                        route.id,
                        route.source_depot_id,
                        0.0,
                        leg.unmet_before,
                        f"route {route.status}",
                    )
                )
                continue
            if idx >= len(key.mu):
                continue
            qty = min(
                leg.quantity,
                route.max_shipment,
                res.depot_inventory.get((route.source_depot_id, fuel), 0.0) + 0.0,
            )
            if qty < min_leg:
                alts.append(
                    Alternative(
                        route.id,
                        route.source_depot_id,
                        0.0,
                        leg.unmet_before,
                        "depot stock reserved",
                    )
                )
                continue
            trial = without.copy()
            trial[idx] += qty
            _, unmet = project(key.inventory, key.mu, trial)
            alts.append(
                Alternative(
                    route.id,
                    route.source_depot_id,
                    float(qty),
                    float(unmet.sum()),
                    f"{route.transit_ticks}-tick transit",
                )
            )
        result.alternatives[(station_id, fuel)] = alts
