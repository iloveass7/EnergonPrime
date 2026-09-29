"""Detection: risk, disruptions, supply problems, demand anomalies, bottlenecks (pipeline §4.5).

Returns the set of currently-true conditions; the worker opens/closes alerts by stable key.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fuelops.domain.models import DemandRow
from fuelops.domain.snapshot import Snapshot
from fuelops.intelligence.forecast.profile import (
    FUEL_INDEX,
    StationProfile,
    expected_demand,
    noise_fraction,
)
from fuelops.intelligence.scoring.risk import RiskItem

ANOMALY_Z = 3.0


@dataclass
class Condition:
    key: str
    kind: str
    severity: str  # info | warning | critical
    title: str
    detail: str
    entity_id: str


def detect(
    snapshot: Snapshot,
    risks: list[RiskItem],
    history: list[DemandRow],
    previous: Snapshot | None = None,
) -> list[Condition]:
    out: list[Condition] = []
    for r in risks:
        if r.level in ("HIGH", "CRITICAL"):
            when = (
                f"stockout in {r.hours_to_stockout:.1f} h"
                if r.hours_to_stockout is not None
                else "at risk"
            )
            out.append(
                Condition(
                    f"risk:{r.station_id}:{r.fuel_type}",
                    "STOCKOUT_RISK",
                    "critical" if r.level == "CRITICAL" else "warning",
                    f"{r.station_name} {r.fuel_type}: {when}",
                    f"{r.inventory:,.0f} L on hand, {r.expected_demand:,.0f} L expected demand, "
                    f"{r.inbound:,.0f} L inbound; model-estimated shortage risk {r.shortage_probability:.0%}",
                    r.station_id,
                )
            )
    for s in snapshot.stations:
        if s.status != "OPEN":
            out.append(
                Condition(
                    f"outage:{s.id}",
                    "STATION_OUTAGE",
                    "critical",
                    f"{s.name} is {s.status}",
                    "demand cannot be served; shipments are rejected (STATION_CLOSED)",
                    s.id,
                )
            )
    for rt in snapshot.routes:
        if rt.status != "AVAILABLE":
            alt = [
                o.id
                for o in snapshot.routes
                if o.destination_station_id == rt.destination_station_id
                and o.id != rt.id
                and o.status == "AVAILABLE"
            ]
            detail = (
                f"alternative: {', '.join(alt)}" if alt else "no alternative route to this station"
            )
            out.append(
                Condition(
                    f"route:{rt.id}",
                    "ROUTE_DISRUPTED",
                    "warning" if alt else "critical",
                    f"{rt.id} is {rt.status}",
                    detail,
                    rt.id,
                )
            )
    for d in snapshot.depots:
        if d.status != "OPEN":
            out.append(
                Condition(
                    f"depot:{d.id}",
                    "DEPOT_CONSTRAINED",
                    "warning",
                    f"{d.name} is {d.status}",
                    "reduced capacity signalled; still shippable",
                    d.id,
                )
            )
    prev_supply = {a.id: a for a in previous.supply} if previous else {}
    for a in snapshot.supply:
        if a.status == "DELAYED":
            out.append(
                Condition(
                    f"supply-delay:{a.id}",
                    "SUPPLY_DELAYED",
                    "warning",
                    f"Supply {a.id} to {a.depot_id} delayed",
                    f"{a.fuel_type} {a.quantity:,.0f} L now planned for tick {a.planned_tick}",
                    a.depot_id,
                )
            )
        old = prev_supply.get(a.id)
        if old is not None and a.quantity < old.quantity - 1:
            out.append(
                Condition(
                    f"supply-short:{a.id}",
                    "SUPPLY_SHORTFALL",
                    "warning",
                    f"Supply {a.id} reduced",
                    f"{old.quantity:,.0f} L -> {a.quantity:,.0f} L",
                    a.depot_id,
                )
            )
    for alloc in snapshot.allocations:
        if alloc.status == "FAILED":
            out.append(
                Condition(
                    f"alloc-failed:{alloc.id}",
                    "ALLOCATION_FAILED",
                    "warning",
                    f"Allocation {alloc.id} failed",
                    alloc.failure_reason or "failed at departure",
                    alloc.destination_station_id,
                )
            )
    pending: dict[str, float] = {}
    for alloc in snapshot.allocations:
        if alloc.status == "PENDING":
            pending[alloc.source_depot_id] = (
                pending.get(alloc.source_depot_id, 0.0) + alloc.quantity
            )
    for d in snapshot.depots:
        if pending.get(d.id, 0.0) > 0.9 * d.dispatch_capacity_per_tick:
            out.append(
                Condition(
                    f"bottleneck:{d.id}",
                    "BOTTLENECK",
                    "warning",
                    f"{d.name} dispatch > 90%",
                    f"{pending[d.id]:,.0f} of {d.dispatch_capacity_per_tick:,.0f} L this tick",
                    d.id,
                )
            )
    out.extend(demand_anomalies(snapshot, history))
    return out


def demand_anomalies(snapshot: Snapshot, history: list[DemandRow]) -> list[Condition]:
    """Two consecutive robust z-scores above 3 on the latest ticks, per (station, fuel)."""
    if not history:
        return []
    latest = max(r.tick for r in history)
    recent = [r for r in history if r.tick >= latest - 1]
    stations = {s.id: s for s in snapshot.stations}
    active_spike = any(e.type == "demand_spike" and e.status == "ACTIVE" for e in snapshot.events)
    by_key: dict[tuple[str, str], list[float]] = {}
    for r in sorted(recent, key=lambda x: x.tick):
        st = stations.get(r.station_id)
        if st is None:
            continue
        # compare against the *unscaled* profile so a multiplier change shows up as a shift
        prof = StationProfile(st.id, st.demand_profile, snapshot.region_factor(st.region_id), 1.0)
        mu = float(
            expected_demand(prof, np.array([r.tick]), snapshot.instance.tick_minutes)[
                FUEL_INDEX[r.fuel_type], 0
            ]
        )
        if mu <= 1.0:
            continue
        z = (r.demand_liters - mu) / (noise_fraction(st.demand_profile) * mu)
        by_key.setdefault((r.station_id, r.fuel_type), []).append(z)
    out = []
    for (sid, fuel), zs in by_key.items():
        if len(zs) >= 2 and min(zs[-2:]) > ANOMALY_Z:
            st = stations[sid]
            known = active_spike or st.demand_multiplier > 1.001
            label = "known demand event" if known else "unexplained demand shift"
            out.append(
                Condition(
                    f"anomaly:{sid}:{fuel}",
                    "DEMAND_ANOMALY",
                    "info" if known else "warning",
                    f"{st.name} {fuel}: demand above profile ({label})",
                    f"last two ticks z = {zs[-2]:.1f}, {zs[-1]:.1f} (threshold {ANOMALY_Z}); multiplier x{st.demand_multiplier:.2f}",
                    sid,
                )
            )
    return out
