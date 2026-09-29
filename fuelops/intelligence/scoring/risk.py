"""Stockout risk: tick-by-tick inventory projection under the profile forecast (pipeline §4.2).

Probabilities are model-estimated: per-tick demand noise (published profile noise) plus a 5%
systematic forecast error, combined as a normal approximation on cumulative demand.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from fuelops.domain.models import FUELS
from fuelops.domain.snapshot import Snapshot
from fuelops.intelligence.forecast.profile import (
    FUEL_INDEX,
    StationProfile,
    expected_demand,
    noise_fraction,
)

SYSTEMATIC_ERROR = 0.05
LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


@dataclass
class KeyState:
    station_id: str
    station_name: str
    region_id: str
    fuel: str
    inventory: float
    capacity: float
    is_open: bool
    mu: np.ndarray  # expected demand per future tick, index k = tick now+1+k
    noise: float
    arrivals: np.ndarray  # liters already inbound, by arrival index
    multiplier: float


@dataclass
class RiskItem:
    station_id: str
    station_name: str
    region_id: str
    fuel_type: str
    level: str
    inventory: float
    capacity: float
    fill_pct: float
    expected_demand: float
    inbound: float
    expected_unmet: float
    shortage_probability: float
    stockout_tick: int | None
    hours_to_stockout: float | None
    cover_hours: float
    drivers: list[str] = field(default_factory=list)
    trajectory: list[float] = field(default_factory=list)


def project(
    inv0: float, mu: np.ndarray, arrivals: np.ndarray, is_open: bool = True
) -> tuple[np.ndarray, np.ndarray]:
    """Inventory after each future tick and unmet liters per tick."""
    horizon = len(mu)
    inv = np.empty(horizon)
    unmet = np.zeros(horizon)
    level = inv0
    for k in range(horizon):
        available = level + arrivals[k]
        demand = mu[k]
        served = min(demand, available) if is_open else 0.0
        unmet[k] = demand - served
        level = available - served
        inv[k] = level
    return inv, unmet


def shortage_probability(inv0: float, mu: np.ndarray, arrivals: np.ndarray, noise: float) -> float:
    cum_mu = np.cumsum(mu)
    sigma = np.sqrt(np.cumsum((noise * mu) ** 2) + (SYSTEMATIC_ERROR * cum_mu) ** 2)
    margin = inv0 + np.cumsum(arrivals) - cum_mu
    z = float(np.min(margin / np.maximum(sigma, 1e-9)))
    return 0.5 * math.erfc(z / math.sqrt(2))


def build_keys(snapshot: Snapshot, horizon: int) -> list[KeyState]:
    now = snapshot.tick
    tick_minutes = snapshot.instance.tick_minutes
    ticks = np.arange(now + 1, now + 1 + horizon)
    routes = {r.id: r for r in snapshot.routes}
    inbound: dict[tuple[str, str], np.ndarray] = {}
    for alloc in snapshot.allocations:
        if alloc.status not in ("PENDING", "IN_TRANSIT"):
            continue
        route = routes.get(alloc.route_id)
        arrival = alloc.expected_arrival_tick
        if arrival is None:
            arrival = now + (route.transit_ticks if route else 2)  # departs this tick
        idx = max(0, arrival - now - 1)
        if idx < horizon:
            key = (alloc.destination_station_id, alloc.fuel_type)
            inbound.setdefault(key, np.zeros(horizon))[idx] += alloc.quantity

    keys: list[KeyState] = []
    for st in snapshot.stations:
        prof = StationProfile(
            st.id,
            st.demand_profile,
            snapshot.region_factor(st.region_id),
            st.demand_multiplier,
        )
        mu_all = expected_demand(prof, ticks, tick_minutes)
        for fuel in FUELS:
            keys.append(
                KeyState(
                    station_id=st.id,
                    station_name=st.name,
                    region_id=st.region_id,
                    fuel=fuel,
                    inventory=float(st.inventory.get(fuel, 0.0)),
                    capacity=float(st.capacity.get(fuel, 0.0)),
                    is_open=st.status == "OPEN",
                    mu=mu_all[FUEL_INDEX[fuel]],
                    noise=noise_fraction(st.demand_profile),
                    arrivals=inbound.get((st.id, fuel), np.zeros(horizon)),
                    multiplier=st.demand_multiplier,
                )
            )
    return keys


def score_key(key: KeyState, now: int, tick_minutes: int) -> RiskItem:
    inv, unmet = project(key.inventory, key.mu, key.arrivals, key.is_open)
    prob = (
        shortage_probability(key.inventory, key.mu, key.arrivals, key.noise) if key.is_open else 1.0
    )
    stock_idx = int(np.argmax(unmet > 1e-6)) if np.any(unmet > 1e-6) else None
    hours = None if stock_idx is None else (stock_idx + 1) * tick_minutes / 60
    hourly = float(np.mean(key.mu)) * 60 / tick_minutes if len(key.mu) else 0.0
    cover = key.inventory / hourly if hourly > 0 else float("inf")
    expected_unmet = float(unmet.sum())

    drivers: list[str] = []
    if not key.is_open:
        drivers.append("station outage: demand cannot be served")
    if key.multiplier > 1.001:
        drivers.append(f"demand multiplier x{key.multiplier:.2f}")
    if hours is not None:
        drivers.append(f"projected stockout in {hours:.1f} h")
    if float(key.arrivals.sum()) > 0:
        drivers.append(f"{key.arrivals.sum():,.0f} L already inbound")

    if not key.is_open or (hours is not None and hours <= 1.0) or key.inventory <= 1.0:
        level = "CRITICAL"
    elif hours is not None or prob >= 0.5:
        level = "HIGH"
    elif prob >= 0.15 or cover < len(key.mu) * tick_minutes / 60 * 1.5:
        level = "MEDIUM"
    else:
        level = "LOW"

    return RiskItem(
        station_id=key.station_id,
        station_name=key.station_name,
        region_id=key.region_id,
        fuel_type=key.fuel,
        level=level,
        inventory=round(key.inventory, 1),
        capacity=key.capacity,
        fill_pct=round(100 * key.inventory / key.capacity, 1) if key.capacity else 0.0,
        expected_demand=round(float(key.mu.sum()), 1),
        inbound=round(float(key.arrivals.sum()), 1),
        expected_unmet=round(expected_unmet, 1),
        shortage_probability=round(prob, 4),
        stockout_tick=None if stock_idx is None else now + 1 + stock_idx,
        hours_to_stockout=None if hours is None else round(hours, 2),
        cover_hours=round(cover, 1) if math.isfinite(cover) else 9999.0,
        drivers=drivers,
        trajectory=[round(float(v), 1) for v in inv],
    )


def score_all(snapshot: Snapshot, horizon: int) -> tuple[list[KeyState], list[RiskItem]]:
    keys = build_keys(snapshot, horizon)
    items = [score_key(k, snapshot.tick, snapshot.instance.tick_minutes) for k in keys]
    items.sort(
        key=lambda r: (
            -LEVELS.index(r.level),
            -r.expected_unmet,
            -r.shortage_probability,
        )
    )
    return keys, items
