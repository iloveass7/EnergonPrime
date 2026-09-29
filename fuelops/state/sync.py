"""Observe + validate: parallel REST refresh into an atomic Snapshot, with per-part fallback."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fuelops.domain.models import DemandRow
from fuelops.domain.snapshot import PARTS, Snapshot
from fuelops.simclient.client import SimulatorClient
from fuelops.simclient.errors import SimulatorError


class SnapshotUnavailable(Exception):
    pass


async def fetch_snapshot(client: SimulatorClient, previous: Snapshot | None) -> Snapshot:
    calls = {
        "instance": client.instance(),
        "regions": client.regions(),
        "depots": client.depots(),
        "stations": client.stations(),
        "routes": client.routes(),
        "supply": client.supply_arrivals(),
        "events": client.events(),
        "allocations": client.allocations(),
        "metrics": client.metrics(),
    }
    results = await asyncio.gather(*calls.values(), return_exceptions=True)
    got: dict[str, Any] = {}
    stale_header = False
    stale_parts: list[str] = []
    errors: dict[str, str] = {}
    for name, result in zip(calls, results, strict=True):
        if isinstance(result, SimulatorError):
            errors[name] = f"{result.kind}: {result}"
            if previous is None:
                raise SnapshotUnavailable(f"{name}: {result}") from result
            got[name] = previous.instance if name == "instance" else getattr(previous, name)
            stale_parts.append(name)
        elif isinstance(result, BaseException):
            raise result
        else:
            got[name] = result.data
            stale_header = stale_header or result.stale
    snap = Snapshot(
        instance=got["instance"],
        regions=got["regions"],
        depots=got["depots"],
        stations=got["stations"],
        routes=got["routes"],
        supply=got["supply"],
        events=got["events"],
        allocations=got["allocations"],
        metrics=got["metrics"],
        fetched_at=time.time(),
        stale_header=stale_header,
        stale_parts=stale_parts,
        errors=errors,
    )
    return snap


def is_reset(previous: Snapshot | None, current: Snapshot) -> bool:
    if previous is None:
        return False
    a, b = previous.instance, current.instance
    return b.tick < a.tick or b.seed != a.seed or b.scenario_id != a.scenario_id


class DemandHistory:
    """Our own copy of demand observations, de-duplicated by id (pipeline §4.1)."""

    def __init__(self, keep_ticks: int = 400) -> None:
        self.rows: dict[int, DemandRow] = {}
        self.last_tick = -1
        self.keep_ticks = keep_ticks

    def reset(self) -> None:
        self.rows.clear()
        self.last_tick = -1

    async def ingest(self, client: SimulatorClient, tick: int) -> int:
        limit = 2000 if self.last_tick < 0 else min(2000, 12 * (tick - self.last_tick) + 24)
        result = await client.demand_history(limit)
        new = 0
        for row in result.data:
            if row.id not in self.rows:
                self.rows[row.id] = row
                new += 1
        if self.rows:
            self.last_tick = max(r.tick for r in self.rows.values())
            floor = self.last_tick - self.keep_ticks
            for rid in [rid for rid, r in self.rows.items() if r.tick < floor]:
                del self.rows[rid]
        return new

    def recent(self, ticks: int) -> list[DemandRow]:
        floor = self.last_tick - ticks
        return [r for r in self.rows.values() if r.tick > floor]


__all__ = [
    "PARTS",
    "DemandHistory",
    "SnapshotUnavailable",
    "fetch_snapshot",
    "is_reset",
]
