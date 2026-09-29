"""Read endpoints: served from the worker's published state (never call the simulator)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import ORJSONResponse
from sqlalchemy import desc, select

from fuelops.api.deps import AppState, envelope, get_app_state, problem, require
from fuelops.persistence.db import AlertRow, AuditRow, IntentRow, RecommendationRow

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require("viewer"))])


async def _state(fo: AppState) -> tuple[dict[str, Any] | None, str]:
    return await fo.state()


def _unavailable() -> Any:
    return problem(
        503,
        "STATE_UNAVAILABLE",
        "no published state yet: worker not running or simulator never reached",
        retryable=True,
    )


@router.get("/dashboard")
async def dashboard(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    if state is None:
        return _unavailable()
    data = {
        k: state[k]
        for k in (
            "kpi",
            "network",
            "recommendations",
            "alerts",
            "health",
            "intelligence",
            "execution_blocked",
        )
    }
    data["risks"] = state["risks"][:12]
    data["allocations"] = state["allocations"][:20]
    data["events"] = state["events"][:20]
    return envelope(data, state, source)


@router.get("/network/snapshot")
async def network(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["network"], state, source) if state else _unavailable()


@router.get("/stations")
async def stations(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["network"]["stations"], state, source) if state else _unavailable()


@router.get("/stations/{station_id}")
async def station(station_id: str, fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    if state is None:
        return _unavailable()
    st = next((s for s in state["network"]["stations"] if s["id"] == station_id), None)
    if st is None:
        return problem(404, "NOT_FOUND", f"no station {station_id}")
    data = {**st, "risks": [r for r in state["risks"] if r["station_id"] == station_id],
            "recommendations": [r for r in state["recommendations"] if r["station_id"] == station_id],
            "routes": [r for r in state["network"]["routes"] if r["destination_station_id"] == station_id]}  # fmt: skip
    return envelope(data, state, source)


@router.get("/depots")
async def depots(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["network"]["depots"], state, source) if state else _unavailable()


@router.get("/routes")
async def routes(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["network"]["routes"], state, source) if state else _unavailable()


@router.get("/supply-arrivals")
async def supply(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["supply"], state, source) if state else _unavailable()


@router.get("/events")
async def events(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["events"], state, source) if state else _unavailable()


@router.get("/risk")
async def risk(
    level: str | None = None,
    fuel_type: str | None = None,
    fo: AppState = Depends(get_app_state),
) -> Any:
    state, source = await _state(fo)
    if state is None:
        return _unavailable()
    items = [
        r
        for r in state["risks"]
        if (level is None or r["level"] == level)
        and (fuel_type is None or r["fuel_type"] == fuel_type)
    ]
    return envelope(items, state, source)


@router.get("/recommendations")
async def recommendations(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["recommendations"], state, source) if state else _unavailable()


@router.get("/recommendations/{rec_id}")
async def recommendation(rec_id: str, fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    rec = next((r for r in (state or {}).get("recommendations", []) if r["id"] == rec_id), None)
    if rec is None:
        row = await fo.db.get_rec(rec_id)
        if row is None:
            return problem(404, "REC_NOT_FOUND", f"no recommendation {rec_id}")
        rec = {
            **row.payload,
            "status": row.status,
            "decided_by": row.decided_by,
            "decision_note": row.decision_note,
        }
    async with fo.db.session() as s:
        intents = (
            (await s.execute(select(IntentRow).where(IntentRow.rec_id == rec_id))).scalars().all()
        )
    rec = {**rec, "intents": [{"idempotency_key": i.idempotency_key, "status": i.status, "sim_allocation_id": i.sim_allocation_id,
                                "upstream_code": i.upstream_code, "detail": i.detail} for i in intents]}  # fmt: skip
    return envelope(rec, state, source)


@router.get("/allocations")
async def allocations(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    if state is None:
        return _unavailable()
    async with fo.db.session() as s:
        intents = (
            (await s.execute(select(IntentRow).order_by(desc(IntentRow.id)).limit(200)))
            .scalars()
            .all()
        )
    by_key = {i.idempotency_key: i for i in intents}
    rows = []
    for a in state["allocations"]:
        intent = by_key.get(a["idempotency_key"])
        rows.append(
            {
                **a,
                "ours": intent is not None,
                "rec_id": intent.rec_id if intent else None,
            }
        )
    pending_intents = [{"idempotency_key": i.idempotency_key, "status": i.status, "detail": i.detail, "body": i.body}
                       for i in intents if i.status in ("SUBMITTING", "RECONCILING", "NEEDS_REVIEW", "REJECTED")]  # fmt: skip
    return envelope({"allocations": rows, "intents_attention": pending_intents[:50]}, state, source)


@router.get("/alerts")
async def alerts(open: bool = True, fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    if state is None:
        return _unavailable()
    return envelope(
        state["alerts"]["open"] if open else state["alerts"]["recent_closed"],
        state,
        source,
    )


@router.get("/kpi")
async def kpi(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["kpi"], state, source) if state else _unavailable()


@router.get("/intelligence")
async def intelligence(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await _state(fo)
    return envelope(state["intelligence"], state, source) if state else _unavailable()


@router.get("/decisions")
async def decisions(
    limit: int = Query(50, ge=1, le=500), fo: AppState = Depends(get_app_state)
) -> Any:
    try:
        async with fo.db.session() as s:
            audit = (
                (await s.execute(select(AuditRow).order_by(desc(AuditRow.id)).limit(limit)))
                .scalars()
                .all()
            )
            recs = (await s.execute(select(RecommendationRow).where(RecommendationRow.status != "PROPOSED")
                                    .order_by(desc(RecommendationRow.created_at)).limit(limit))).scalars().all()  # fmt: skip
    except Exception as exc:  # noqa: BLE001
        return problem(503, "DATABASE_UNAVAILABLE", str(exc), retryable=True)
    return ORJSONResponse({"data": {
        "audit": [{"id": a.id, "ts": a.ts, "kind": a.kind, "actor": a.actor, "epoch": a.epoch, "tick": a.tick, "payload": a.payload} for a in audit],
        "recommendations": [{"id": r.id, "status": r.status, "station_id": r.station_id, "fuel_type": r.fuel_type, "created_tick": r.created_tick,
                             "quantity": r.payload.get("quantity"), "route_id": r.payload.get("route_id"), "decided_by": r.decided_by,
                             "decision_note": r.decision_note, "impact": r.payload.get("impact", {}).get("unmet_before_l")} for r in recs],
    }, "meta": {"source": "db"}})  # fmt: skip


@router.get("/alerts/history")
async def alert_history(
    limit: int = Query(100, ge=1, le=1000), fo: AppState = Depends(get_app_state)
) -> Any:
    async with fo.db.session() as s:
        rows = (
            (await s.execute(select(AlertRow).order_by(desc(AlertRow.id)).limit(limit)))
            .scalars()
            .all()
        )
    return ORJSONResponse({"data": [{"id": r.id, "kind": r.kind, "severity": r.severity, "title": r.title, "detail": r.detail,
                                     "opened_tick": r.opened_tick, "closed_tick": r.closed_tick, "epoch": r.epoch} for r in rows],
                           "meta": {"source": "db"}})  # fmt: skip


@router.get("/capabilities")
async def capabilities(fo: AppState = Depends(get_app_state)) -> Any:
    return ORJSONResponse({"data": {"test_plane": fo.settings.test_plane_enabled, "auth_mode": fo.settings.effective_auth_mode,
                                    "policy": "MANUAL", "planner": "greedy", "directives": False}})  # fmt: skip
