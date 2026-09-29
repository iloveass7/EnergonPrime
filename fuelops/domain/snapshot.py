"""An atomic, validated view of the simulator at one tick."""

from __future__ import annotations

from dataclasses import dataclass, field

from fuelops.domain.models import (
    Allocation,
    Depot,
    Instance,
    Metrics,
    Region,
    Route,
    SimEvent,
    Station,
    SupplyArrival,
)

PARTS = (
    "regions",
    "depots",
    "stations",
    "routes",
    "supply",
    "events",
    "allocations",
    "metrics",
)


@dataclass
class Snapshot:
    instance: Instance
    regions: list[Region]
    depots: list[Depot]
    stations: list[Station]
    routes: list[Route]
    supply: list[SupplyArrival]
    events: list[SimEvent]
    allocations: list[Allocation]
    metrics: Metrics | None
    fetched_at: float  # wall clock (monotonic-free unix seconds), for data age only
    stale_header: bool = False  # X-Simulator-Stale seen on any part
    stale_parts: list[str] = field(default_factory=list)  # parts carried over from an older fetch
    epoch: int = 0
    state_version: int = 0
    errors: dict[str, str] = field(default_factory=dict)  # part -> last error

    @property
    def tick(self) -> int:
        return self.instance.tick

    @property
    def fresh(self) -> bool:
        """Execution-grade: every part fetched this refresh and no stale header (pipeline A1)."""
        return not self.stale_header and not self.stale_parts

    def depot(self, depot_id: str) -> Depot | None:
        return next((d for d in self.depots if d.id == depot_id), None)

    def station(self, station_id: str) -> Station | None:
        return next((s for s in self.stations if s.id == station_id), None)

    def route(self, route_id: str) -> Route | None:
        return next((r for r in self.routes if r.id == route_id), None)

    def region_factor(self, region_id: str) -> float:
        return next((r.demand_factor for r in self.regions if r.id == region_id), 1.0)
