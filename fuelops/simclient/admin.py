"""AdminTestPort: the dual-gated test plane's only path to /admin/* (pipeline §8 A7).

Decision code must never import this module (import-linter contract).
Allowlisted actions and payload shapes only; event filters must name explicit ids because
image 1.0.0 applies empty filters to nothing (docs/contract-findings.md).
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, model_validator

EVENT_FILTER_KEYS = {
    "demand_spike": ("station_ids", "region_ids"),
    "route_disruption": ("route_ids",),
    "station_outage": ("station_ids",),
    "depot_constraint": ("depot_ids",),
    "shipment_delay": ("depot_ids",),
    "supply_shortfall": ("depot_ids",),
}


class EventInjection(BaseModel):
    type: Literal[
        "demand_spike",
        "route_disruption",
        "station_outage",
        "depot_constraint",
        "shipment_delay",
        "supply_shortfall",
    ]
    start_tick: int = Field(ge=0)
    duration_ticks: int = Field(gt=0, le=2000)
    parameters: dict[str, Any] = {}

    @model_validator(mode="after")
    def _explicit_targets(self) -> EventInjection:
        keys = EVENT_FILTER_KEYS[self.type]
        if not any(self.parameters.get(k) for k in keys):
            raise ValueError(f"{self.type} needs a non-empty {' or '.join(keys)}")
        allowed = set(keys) | {"multiplier", "delay_ticks", "factor", "fuel_types"}
        unknown = set(self.parameters) - allowed
        if unknown:
            raise ValueError(f"unknown parameters {sorted(unknown)}")
        return self


class FaultInjection(BaseModel):
    type: Literal["latency", "unavailable", "error_rate", "stale_data", "stream_disconnect"]
    duration_seconds: int = Field(gt=0, le=600)
    parameters: dict[str, Any] = {}

    @model_validator(mode="after")
    def _params(self) -> FaultInjection:
        allowed = {"latency": {"delay_ms"}, "error_rate": {"rate"}}.get(self.type, set())
        unknown = set(self.parameters) - allowed
        if unknown:
            raise ValueError(f"unknown parameters {sorted(unknown)}")
        return self


AdminAction = Literal["run", "pause", "step", "reset", "faults-clear"]
_PATHS = {
    "run": "/admin/run",
    "pause": "/admin/pause",
    "step": "/admin/step",
    "reset": "/admin/reset",
    "faults-clear": "/admin/faults/clear",
}


class AdminTestPort:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=5.0, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def action(self, action: AdminAction) -> Any:
        resp = await self._http.post(_PATHS[action])
        resp.raise_for_status()
        return resp.json()

    async def inject_event(self, event: EventInjection) -> Any:
        resp = await self._http.post("/admin/events", json=event.model_dump())
        resp.raise_for_status()
        return resp.json()

    async def inject_fault(self, fault: FaultInjection) -> Any:
        resp = await self._http.post("/admin/faults", json=fault.model_dump())
        resp.raise_for_status()
        return resp.json()

    async def audit(self, limit: int = 50) -> Any:
        resp = await self._http.get("/admin/audit", params={"limit": limit})
        resp.raise_for_status()
        return resp.json()
