"""Dual-gated test plane: registered only when APP_ENV in {local,test,demo} and the flag is on."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel

from fuelops.api.deps import AppState, get_app_state, problem, require
from fuelops.observability import metrics as m
from fuelops.simclient.admin import AdminAction, EventInjection, FaultInjection

router = APIRouter(prefix="/api/v1/test/simulator", dependencies=[Depends(require("admin"))])

SCENARIOS: dict[str, dict[str, Any]] = {
    "demand_spike_dhaka": {"events": [{"type": "demand_spike", "duration_ticks": 12, "parameters": {"region_ids": ["region-dhaka"], "multiplier": 1.8}}]},
    "route_disruption_mirpur": {"events": [{"type": "route_disruption", "duration_ticks": 16, "parameters": {"route_ids": ["route-gazipur-mirpur"]}}]},
    "shipment_delay_patiya": {"events": [{"type": "shipment_delay", "duration_ticks": 1, "parameters": {"depot_ids": ["depot-patiya"], "fuel_types": ["DIESEL"], "delay_ticks": 6}}]},
    "supply_shortfall_gazipur": {"events": [{"type": "supply_shortfall", "duration_ticks": 1, "parameters": {"depot_ids": ["depot-gazipur"], "fuel_types": ["PETROL"], "factor": 0.5}}]},
    "station_outage_tongi": {"events": [{"type": "station_outage", "duration_ticks": 8, "parameters": {"station_ids": ["station-tongi"]}}]},
    "depot_constraint_gazipur": {"events": [{"type": "depot_constraint", "duration_ticks": 12, "parameters": {"depot_ids": ["depot-gazipur"]}}]},
    "combined_chattogram": {"events": [
        {"type": "demand_spike", "duration_ticks": 12, "parameters": {"region_ids": ["region-chattogram"], "multiplier": 1.6}},
        {"type": "route_disruption", "duration_ticks": 12, "parameters": {"route_ids": ["route-patiya-karnaphuli"]}}],
        "faults": [{"type": "error_rate", "duration_seconds": 60, "parameters": {"rate": 0.25}}]},
}  # fmt: skip


class Confirm(BaseModel):
    confirm: str


def _admin(fo: AppState) -> Any:
    if fo.admin is None:
        return None
    return fo.admin


@router.post("/{action}")
async def action(action: AdminAction, fo: AppState = Depends(get_app_state)) -> Any:
    admin = _admin(fo)
    if admin is None:
        return problem(404, "TEST_PLANE_DISABLED", "test plane is disabled")
    m.TEST_PLANE_ACTIONS.labels(action).inc()
    result = await admin.action(action)
    await fo.db.audit("test_plane." + action, "operator", {"result": result})
    return ORJSONResponse({"data": result})


@router.post("/events/inject")
async def inject_event(event: EventInjection, fo: AppState = Depends(get_app_state)) -> Any:
    admin = _admin(fo)
    if admin is None:
        return problem(404, "TEST_PLANE_DISABLED", "test plane is disabled")
    m.TEST_PLANE_ACTIONS.labels("event").inc()
    result = await admin.inject_event(event)
    await fo.db.audit(
        "test_plane.event", "operator", {"event": event.model_dump(), "result": result}
    )
    return ORJSONResponse({"data": result})


@router.post("/faults/inject")
async def inject_fault(fault: FaultInjection, fo: AppState = Depends(get_app_state)) -> Any:
    admin = _admin(fo)
    if admin is None:
        return problem(404, "TEST_PLANE_DISABLED", "test plane is disabled")
    m.TEST_PLANE_ACTIONS.labels("fault").inc()
    result = await admin.inject_fault(fault)
    await fo.db.audit(
        "test_plane.fault", "operator", {"fault": fault.model_dump(), "result": result}
    )
    return ORJSONResponse({"data": result})


@router.get("/scenarios")
async def scenarios() -> Any:
    return ORJSONResponse({"data": SCENARIOS})


@router.post("/scenarios/{name}/run")
async def run_scenario(name: str, fo: AppState = Depends(get_app_state)) -> Any:
    admin = _admin(fo)
    if admin is None:
        return problem(404, "TEST_PLANE_DISABLED", "test plane is disabled")
    spec = SCENARIOS.get(name)
    if spec is None:
        return problem(404, "NOT_FOUND", f"no scenario {name}")
    state, _ = await fo.state()
    tick = (state or {}).get("meta", {}).get("tick", 0)
    created = []
    for ev in spec.get("events", []):
        created.append(await admin.inject_event(EventInjection(start_tick=tick, **ev)))
    for f in spec.get("faults", []):
        created.append(await admin.inject_fault(FaultInjection(**f)))
    m.TEST_PLANE_ACTIONS.labels("scenario").inc()
    await fo.db.audit("test_plane.scenario", "operator", {"scenario": name, "tick": tick})
    return ORJSONResponse({"data": {"scenario": name, "start_tick": tick, "created": created}})
