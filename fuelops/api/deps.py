"""Shared API state, response envelopes, auth and the L1 read cache."""

from __future__ import annotations

import time
import uuid
from collections import deque
from typing import Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, ORJSONResponse

from fuelops.cache.store import StateStore
from fuelops.config import Settings
from fuelops.decisions.service import DecisionService
from fuelops.persistence.db import Database
from fuelops.simclient.admin import AdminTestPort
from fuelops.simclient.client import SimulatorClient

ROLES = {"viewer": 0, "operator": 1, "admin": 2}


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = StateStore(settings.redis_url)
        self.db = Database(settings.sqlalchemy_url)
        self.client = SimulatorClient(settings.sim_base_url, connect_timeout=settings.sim_connect_timeout_s,
                                      read_timeout=settings.sim_read_timeout_s, total_timeout=settings.sim_total_timeout_s)  # fmt: skip
        self.decisions = DecisionService(self.db, self.client, min_leg=settings.min_leg_liters)
        self.admin = AdminTestPort(settings.sim_base_url) if settings.test_plane_enabled else None
        self._l1: tuple[float, dict[str, Any]] | None = None
        self.last_good: dict[str, Any] | None = None
        self.latencies: deque[tuple[float, float, bool]] = deque(
            maxlen=5000
        )  # (ts, seconds, error)
        self.redis_ok = True

    async def state(self) -> tuple[dict[str, Any] | None, str]:
        """L1 (500 ms) -> Redis -> last good in-process copy. Returns (state, source)."""
        now = time.monotonic()
        if self._l1 is not None and now - self._l1[0] < 0.5:
            return self._l1[1], "l1"
        try:
            state = await self.store.read_state()
            self.redis_ok = True
        except Exception:  # noqa: BLE001 - redis down: serve last good copy, labelled
            self.redis_ok = False
            return self.last_good, "l1-last-good"
        if state is not None:
            self._l1 = (now, state)
            self.last_good = state
        return state, "cache"

    def p95_and_error_rate(self, window_s: float = 60.0) -> tuple[float | None, float | None, int]:
        cutoff = time.time() - window_s
        recent = [(d, e) for ts, d, e in self.latencies if ts >= cutoff]
        if not recent:
            return None, None, 0
        durations = sorted(d for d, _ in recent)
        p95 = durations[min(len(durations) - 1, int(0.95 * len(durations)))]
        return (
            round(p95 * 1000, 1),
            round(sum(1 for _, e in recent if e) / len(recent), 4),
            len(recent),
        )


def get_app_state(request: Request) -> AppState:
    return request.app.state.fo  # type: ignore[no-any-return]


def envelope(data: Any, state: dict[str, Any] | None, source: str) -> ORJSONResponse:
    meta = dict(state["meta"]) if state else {}
    if state:
        meta["age_s"] = round(time.time() - meta.get("fetched_at", time.time()), 2)
        meta["execution_blocked"] = state.get("execution_blocked")
    meta.update(source=source, as_of=time.time())
    return ORJSONResponse({"data": data, "meta": meta})


def problem(
    status: int,
    code: str,
    detail: str,
    *,
    upstream_code: str | None = None,
    retryable: bool = False,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"https://fuelops/errors/{code.lower().replace('_', '-')}",
            "title": code.replace("_", " ").title(),
            "status": status,
            "code": code,
            "detail": detail,
            "correlation_id": str(uuid.uuid4()),
            "retryable": retryable,
            "upstream_code": upstream_code,
        },
    )


def require(role: str) -> Any:
    async def _check(request: Request, fo: AppState = Depends(get_app_state)) -> str:
        if fo.settings.effective_auth_mode == "off":
            return "local-operator"
        key = request.headers.get("x-api-key")
        keys = {
            fo.settings.api_key_viewer: "viewer",
            fo.settings.api_key_operator: "operator",
            fo.settings.api_key_admin: "admin",
        }
        granted = keys.get(key) if key else None
        if granted is None or ROLES[granted] < ROLES[role]:
            raise HTTPException(
                status_code=401 if granted is None else 403, detail="insufficient role"
            )
        return granted

    return _check
