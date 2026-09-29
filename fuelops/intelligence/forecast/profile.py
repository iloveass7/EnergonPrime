"""Champion forecast: the published structural demand profile (pipeline §4.1).

expected liters per tick = daily base / ticks_per_day x region factor x hour factor x live multiplier.
Constants are the simulator's published world (docs/simulator-guide.md §8.5-8.6).
Pure NumPy: no IO, no framework imports.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

FUEL_INDEX = {"DIESEL": 0, "PETROL": 1, "OCTANE": 2}

# liters per simulated day (DIESEL, PETROL, OCTANE) and relative per-tick noise
DAILY_BASE: dict[str, tuple[float, float, float]] = {
    "urban_high": (8500.0, 10500.0, 5600.0),
    "industrial": (14000.0, 4500.0, 2200.0),
    "highway": (10500.0, 11000.0, 6200.0),
    "regional": (7200.0, 7600.0, 3600.0),
}
NOISE: dict[str, float] = {
    "urban_high": 0.10,
    "industrial": 0.08,
    "highway": 0.12,
    "regional": 0.10,
}

# (inclusive hour ranges that are busy, busy factor, off-peak factor)
HOUR_FACTORS: dict[str, tuple[tuple[tuple[int, int], ...], float, float]] = {
    "industrial": (((6, 17),), 1.55, 0.45),
    "highway": (((6, 9), (16, 20)), 1.35, 0.75),
    "urban_high": (((7, 9), (16, 20)), 1.45, 0.70),
    "regional": (((7, 20),), 1.25, 0.65),
}

MODEL_VERSION = "profile-v1"


def hour_factor(profile: str, hour: int) -> float:
    ranges, busy, off = HOUR_FACTORS.get(profile, (((0, 23),), 1.0, 1.0))
    return busy if any(lo <= hour <= hi for lo, hi in ranges) else off


@dataclass(frozen=True)
class StationProfile:
    station_id: str
    profile: str
    region_factor: float
    multiplier: float


def tick_hours(ticks: np.ndarray, tick_minutes: int, hour_offset_ticks: int = 0) -> np.ndarray:
    """Hour of day for each tick (sim clock starts at 00:00 on tick 0)."""
    minutes = (ticks + hour_offset_ticks) * tick_minutes
    return (minutes // 60) % 24


def expected_demand(
    station: StationProfile,
    ticks: np.ndarray,
    tick_minutes: int,
    hour_offset_ticks: int = 0,
) -> np.ndarray:
    """Expected liters per tick, shape [3 fuels, len(ticks)]."""
    per_tick_base = np.array(DAILY_BASE.get(station.profile, (0.0, 0.0, 0.0))) * tick_minutes / 1440
    hours = tick_hours(ticks, tick_minutes, hour_offset_ticks)
    factors = np.array([hour_factor(station.profile, int(h)) for h in hours])
    scale = station.region_factor * station.multiplier
    return per_tick_base[:, None] * factors[None, :] * scale


def noise_fraction(profile: str) -> float:
    return NOISE.get(profile, 0.12)
