"""Liveness, readiness, Prometheus, component health, and the browser SSE push."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import orjson
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import ORJSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sse_starlette.sse import EventSourceResponse

from fuelops.api.deps import AppState, get_app_state, require
from fuelops.cache.store import UPDATES_CHANNEL

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(fo: AppState = Depends(get_app_state)) -> Any:
    redis_ok = await fo.store.ping()
    state, _ = await fo.state()
    ready = redis_ok and state is not None
    return ORJSONResponse(
        {"ready": ready, "redis": redis_ok, "state": state is not None},
        status_code=200 if ready else 503,
    )


@router.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _component(status: str, detail: str = "") -> dict[str, str]:
    return {"status": status, "detail": detail}


@router.get("/api/v1/system/status", dependencies=[Depends(require("viewer"))])
async def system_status(fo: AppState = Depends(get_app_state)) -> Any:
    state, source = await fo.state()
    hb = None
    try:
        hb = await fo.store.read_heartbeat()
    except Exception:  # noqa: BLE001 - redis down: worker shown as down
        hb = None
    db_ok = await fo.db.ping()
    p95, err_rate, n = fo.p95_and_error_rate()
    health = (state or {}).get("health", {})
    sim = health.get("simulator", {})
    meta = (state or {}).get("meta", {})
    age = round(time.time() - meta["fetched_at"], 1) if meta.get("fetched_at") else None
    worker_age = round(time.time() - hb["ts"], 1) if hb else None

    def sim_status() -> dict[str, str]:
        if not state:
            return _component("down", "no state published")
        if sim.get("circuit") != "closed":
            return _component("down", f"circuit {sim.get('circuit')}: {sim.get('last_error')}")
        if meta.get("stale"):
            return _component("degraded", "stale data (execution blocked)")
        return _component("healthy", f"tick {meta.get('tick')}, data age {age}s")

    components = {
        "backend_api": _component("healthy", f"p95 {p95} ms over {n} requests" if p95 is not None else "no traffic yet"),
        "redis": _component("healthy" if fo.redis_ok else "down", "hot state" if fo.redis_ok else f"serving {source}"),
        "database": _component("healthy" if db_ok and not fo.db.is_sqlite else ("degraded" if db_ok else "down"),
                               "sqlite fallback (set DATABASE_URL for Neon)" if fo.db.is_sqlite else "postgres"),
        "worker": _component("healthy" if worker_age is not None and worker_age < 10 else "down",
                             f"heartbeat {worker_age}s ago" if worker_age is not None else "no heartbeat"),
        "fuel_simulator": sim_status(),
        "event_stream": _component("healthy" if sim.get("stream") == "connected" else "degraded",
                                   "SSE connected" if sim.get("stream") == "connected" else "SSE down: 1 s polling"),
        "prediction_service": _component("healthy" if state else "down",
                                         f"profile WAPE {state['intelligence'].get('wape_overall')}" if state else ""),
        "decision_engine": _component("degraded" if (state or {}).get("execution_blocked") else ("healthy" if state else "down"),
                                      (state or {}).get("execution_blocked") or "greedy planner + validator"),
    }  # fmt: skip
    statuses = [c["status"] for c in components.values()]
    overall = (
        "down"
        if components["fuel_simulator"]["status"] == "down" and not state
        else ("degraded" if any(s != "healthy" for s in statuses) else "healthy")
    )
    return ORJSONResponse({"data": {"overall": overall, "components": components, "p95_latency_ms": p95,
                                    "error_rate": err_rate, "requests_60s": n, "data_age_s": age,
                                    "degraded": meta.get("degraded", [])}, "meta": {"source": source}})  # fmt: skip


@router.get("/api/v1/stream", dependencies=[Depends(require("viewer"))])
async def stream(request: Request, fo: AppState = Depends(get_app_state)) -> EventSourceResponse:
    async def events() -> AsyncIterator[dict[str, Any]]:
        pubsub = fo.store.redis.pubsub()
        await pubsub.subscribe(UPDATES_CHANNEL)
        try:
            yield {"event": "hello", "data": "{}"}
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15.0)
                if msg is None:
                    yield {"event": "keepalive", "data": "{}"}
                    continue
                payload = orjson.loads(msg["data"])
                yield {
                    "event": payload.get("type", "update"),
                    "data": orjson.dumps(payload).decode(),
                }
        finally:
            await pubsub.unsubscribe(UPDATES_CHANNEL)
            await pubsub.aclose()

    return EventSourceResponse(events(), ping=20)
