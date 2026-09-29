"""Typed simulator entities (wire == domain here; the ACL rejects anything that does not parse).

All quantities are simulated liters.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

Fuel = Literal["DIESEL", "PETROL", "OCTANE"]
FUELS: tuple[Fuel, ...] = ("DIESEL", "PETROL", "OCTANE")


def _utc(value: Any) -> Any:
    # Image 1.0.0 serialises sim_time without an offset (docs/contract-findings.md): it is UTC.
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value


class Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Instance(Wire):
    id: int
    scenario_id: str
    scenario_version: str
    seed: int
    sim_time: datetime
    tick: int
    tick_minutes: int
    status: Literal["PAUSED", "RUNNING"]

    _utc_time = field_validator("sim_time", mode="before")(_utc)


class Region(Wire):
    id: str
    name: str
    demand_factor: float


class Depot(Wire):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "CONSTRAINED"] | str
    dispatch_capacity_per_tick: float
    capacity: dict[str, float]
    inventory: dict[str, float]


class Station(Wire):
    id: str
    name: str
    region_id: str
    status: Literal["OPEN", "OUTAGE"] | str
    demand_profile: str
    demand_multiplier: float
    capacity: dict[str, float]
    inventory: dict[str, float]


class Route(Wire):
    id: str
    source_depot_id: str
    destination_station_id: str
    transit_ticks: int
    max_shipment: float
    status: Literal["AVAILABLE", "DISRUPTED"] | str


class SupplyArrival(Wire):
    id: str
    depot_id: str
    fuel_type: Fuel
    quantity: float
    planned_tick: int
    actual_tick: int | None = None
    status: str


class SimEvent(Wire):
    id: int
    type: str
    start_tick: int
    end_tick: int
    status: str
    parameters: dict[str, Any] = {}


class Allocation(Wire):
    id: int
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: Fuel
    quantity: float
    created_tick: int
    departure_tick: int | None = None
    expected_arrival_tick: int | None = None
    actual_arrival_tick: int | None = None
    status: Literal["PENDING", "IN_TRANSIT", "ARRIVED", "FAILED", "CANCELLED"] | str
    failure_reason: str | None = None


class DemandRow(Wire):
    id: int
    station_id: str
    fuel_type: Fuel
    tick: int
    sim_time: datetime
    demand_liters: float
    served_liters: float
    unmet_liters: float

    _utc_time = field_validator("sim_time", mode="before")(_utc)


class Metrics(Wire):
    served_demand_liters: float
    unmet_demand_liters: float
    service_level: float
    allocation_liters: float
    allocation_failures: int


class AllocationRequest(BaseModel):
    idempotency_key: str
    source_depot_id: str
    destination_station_id: str
    route_id: str
    fuel_type: Fuel
    quantity: float
