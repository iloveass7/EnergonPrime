"""One refresh = observe -> validate -> snapshot -> detect -> predict -> plan -> verify -> publish.

Pure orchestration over the ACL, intelligence and stores; the loop lives in worker/main.py.
"""

from __future__ import annotations

import time
from dataclasses import asdict
from typing import Any

import numpy as np

from fuelops.cache.store import StateStore
from fuelops.config import Settings
from fuelops.domain.snapshot import Snapshot
from fuelops.intelligence.allocation.greedy import plan_greedy
from fuelops.intelligence.detection.detectors import Condition, detect
from fuelops.intelligence.explain.recommend import (
    build_recommendations,
    legs_equivalent,
)
from fuelops.intelligence.forecast.profile import (
    FUEL_INDEX,
    MODEL_VERSION,
    StationProfile,
    expected_demand,
)
from fuelops.intelligence.scoring.risk import LEVELS, score_all
from fuelops.intelligence.validate.validator import LegSpec, validate_legs
from fuelops.observability import metrics as m
from fuelops.observability.logging import log
from fuelops.persistence.db import AlertRow, Database, RecommendationRow
from fuelops.simclient.client import SimulatorClient
from fuelops.state.sync import DemandHistory, fetch_snapshot, is_reset


class Engine:
    def __init__(
        self,
        settings: Settings,
        client: SimulatorClient,
        store: StateStore,
        db: Database | None,
    ) -> None:
        self.s = settings
        self.client = client
        self.store = store
        self.db = db
        self.prev: Snapshot | None = None
        self.epoch = 0
        self.state_version = 0
        self.history = DemandHistory()
        self.active_recs: dict[tuple[str, str], dict[str, Any]] = {}
        self.open_alerts: dict[str, dict[str, Any]] = {}
        self.closed_alerts: list[dict[str, Any]] = []
        self.sse_state = "connecting"
        self.reset_hint = False
        self.last_state: dict[str, Any] | None = None
        self.last_plan_ms = 0.0
        self.last_refresh_ms = 0.0
        self.db_ok = db is not None

    async def restore(self) -> None:
        state = await self.store.read_state()
        if state:
            self.epoch = int(state["meta"].get("epoch", 0))
            self.state_version = int(state["meta"].get("state_version", 0))

    # ------------------------------------------------------------------ refresh
    async def refresh_once(self) -> dict[str, Any]:
        started = time.perf_counter()
        snap = await fetch_snapshot(self.client, self.prev)
        if self.reset_hint or is_reset(self.prev, snap):
            await self._on_reset(snap)
        self.reset_hint = False
        self.state_version += 1
        snap.epoch, snap.state_version = self.epoch, self.state_version
        m.SIM_TICK.set(snap.tick)

        try:
            await self.history.ingest(self.client, snap.tick)
        except Exception as exc:  # noqa: BLE001 - history is optional for the hot path
            log.warning("history_ingest_failed", error=str(exc))

        keys, risks = score_all(snap, self.s.risk_horizon_ticks)
        for level in LEVELS:
            m.RISK_KEYS.labels(level).set(sum(1 for r in risks if r.level == level))

        blocked = self._execution_block(snap)
        plan_started = time.perf_counter()
        if blocked is None:
            plan = plan_greedy(
                snap,
                keys,
                reserve_fraction=self.s.depot_reserve_fraction,
                min_leg=self.s.min_leg_liters,
            )
            legs = [
                LegSpec(x.route_id, x.depot_id, x.station_id, x.fuel_type, x.quantity)
                for x in plan.legs
            ]
            violations = validate_legs(snap, legs, min_leg=self.s.min_leg_liters)
            for v in violations:
                m.VALIDATOR_REJECTIONS.labels(v.code).inc()
            bad = {v.leg for v in violations}
            plan.legs = [leg for i, leg in enumerate(plan.legs) if i not in bad]
            fresh_recs = build_recommendations(
                snap, keys, risks, plan, valid_ticks=self.s.rec_valid_ticks
            )
            m.FALLBACK_ACTIVE.labels("planner").set(0)
        else:
            fresh_recs = None
            m.FALLBACK_ACTIVE.labels("planner").set(1)
        self.last_plan_ms = (time.perf_counter() - plan_started) * 1000
        m.PLANNER_SECONDS.labels("greedy").observe(self.last_plan_ms / 1000)
        await self._update_recs(snap, fresh_recs)

        conditions = detect(snap, risks, self.history.recent(4), self.prev)
        if not snap.fresh:
            conditions.append(Condition("stale", "STALE_DATA", "warning", "Simulator data is stale",
                                        f"stale header={snap.stale_header}, carried parts={snap.stale_parts}", "simulator"))  # fmt: skip
        await self._update_alerts(snap, conditions)

        self.last_refresh_ms = (time.perf_counter() - started) * 1000
        state = self._build_state(snap, risks, blocked)
        await self.store.publish_state(state)
        self.last_state = state
        self.prev = snap
        m.SYNC_RUNS.labels("ok").inc()
        m.SYNC_SECONDS.observe(self.last_refresh_ms / 1000)
        m.STATE_VERSION.set(self.state_version)
        m.STATE_AGE.set(0)
        return state

    async def publish_degraded(self, reason: str) -> None:
        """Simulator unreachable: keep serving the last state, clearly labelled."""
        m.SYNC_RUNS.labels("error").inc()
        if self.last_state is None:
            return
        state = dict(self.last_state)
        meta = dict(state["meta"])
        meta["stale"] = True
        meta["degraded"] = sorted(set(meta.get("degraded", [])) | {f"simulator:{reason}"})
        meta["as_of_age_s"] = round(time.time() - meta["fetched_at"], 1)
        state["meta"] = meta
        state["health"] = self._health(None)
        state["execution_blocked"] = f"simulator {reason}: showing last known state"
        m.STATE_AGE.set(meta["as_of_age_s"])
        await self.store.publish_state(state)

    # ------------------------------------------------------------------ pieces
    def _execution_block(self, snap: Snapshot) -> str | None:
        if snap.stale_header:
            return "STATE_STALE: X-Simulator-Stale present"
        if snap.stale_parts:
            return f"STATE_STALE: parts not refreshed {snap.stale_parts}"
        if self.client.breaker.state != "closed":
            return f"simulator circuit {self.client.breaker.state}"
        return None

    async def _on_reset(self, snap: Snapshot) -> None:
        self.epoch += 1
        m.EPOCH.set(self.epoch)
        log.info("simulator_reset_detected", epoch=self.epoch, tick=snap.tick)
        self.history.reset()
        superseded = [r["id"] for r in self.active_recs.values()]
        self.active_recs.clear()
        for key in list(self.open_alerts):
            await self._close_alert(key, snap.tick)
        if self.db is not None:
            await self._db(self.db.set_rec_status(superseded, "SUPERSEDED"))
            await self._db(
                self.db.audit(
                    "simulator.reset",
                    "worker",
                    {"new_epoch": self.epoch},
                    epoch=self.epoch,
                    tick=snap.tick,
                )
            )
        await self.store.notify({"type": "resync", "epoch": self.epoch})

    async def _db(self, coro: Any) -> Any:
        try:
            result = await coro
            self.db_ok = True
            return result
        except Exception as exc:  # noqa: BLE001 - Postgres down: keep running, report it
            self.db_ok = False
            log.warning("db_write_failed", error=str(exc))
            return None

    async def _update_recs(self, snap: Snapshot, fresh: list[dict[str, Any]] | None) -> None:
        # drop recs the operator already decided (API writes the DB)
        if self.db is not None and self.active_recs:
            ids = [r["id"] for r in self.active_recs.values()]
            decided = await self._db(self._decided(ids))
            for key in [k for k, r in self.active_recs.items() if decided and r["id"] in decided]:
                del self.active_recs[key]
        if fresh is None:
            return  # execution blocked: keep what we have, flagged in state
        new_by_key = {(r["station_id"], r["fuel_type"]): r for r in fresh}
        to_insert: list[dict[str, Any]] = []
        superseded: list[str] = []
        for key, rec in new_by_key.items():
            old = self.active_recs.get(key)
            if (
                old is not None
                and snap.tick <= old["valid_until_tick"]
                and legs_equivalent(old, rec)
            ):
                continue
            if old is not None:
                superseded.append(old["id"])
            self.active_recs[key] = rec
            to_insert.append(rec)
            m.RECOMMENDATIONS.labels("greedy").inc()
        for key in [k for k in self.active_recs if k not in new_by_key]:
            old = self.active_recs.pop(key)
            superseded.append(old["id"])
        if self.db is not None:
            await self._db(self.db.set_rec_status(superseded, "SUPERSEDED"))
            await self._db(self.db.upsert_recommendations(to_insert))

    async def _decided(self, ids: list[str]) -> set[str]:
        from sqlalchemy import select

        assert self.db is not None
        async with self.db.session() as s:
            rows = await s.execute(
                select(RecommendationRow.id).where(
                    RecommendationRow.id.in_(ids),
                    RecommendationRow.status != "PROPOSED",
                )
            )
            return set(rows.scalars())

    async def _update_alerts(self, snap: Snapshot, conditions: list[Condition]) -> None:
        current = {c.key: c for c in conditions}
        for key, c in current.items():
            if key in self.open_alerts:
                self.open_alerts[key].update(title=c.title, detail=c.detail, severity=c.severity)
                continue
            alert = {
                **asdict(c),
                "opened_tick": snap.tick,
                "opened_at": time.time(),
                "epoch": self.epoch,
            }
            self.open_alerts[key] = alert
            m.ALERTS_OPENED.labels(c.kind).inc()
            if self.db is not None:
                await self._db(self._insert_alert(alert))
            await self.store.notify({"type": "alert.opened", "key": key, "title": c.title})
        for key in [k for k in self.open_alerts if k not in current]:
            await self._close_alert(key, snap.tick)

    async def _insert_alert(self, a: dict[str, Any]) -> None:
        assert self.db is not None
        async with self.db.session() as s:
            s.add(AlertRow(key=a["key"], kind=a["kind"], severity=a["severity"], title=a["title"], detail=a["detail"],
                           entity_id=a["entity_id"], epoch=a["epoch"], opened_tick=a["opened_tick"], opened_at=a["opened_at"]))  # fmt: skip

    async def _close_alert(self, key: str, tick: int) -> None:
        alert = self.open_alerts.pop(key)
        alert.update(closed_tick=tick, closed_at=time.time())
        self.closed_alerts = [alert, *self.closed_alerts][:50]
        if self.db is not None:
            from sqlalchemy import update

            async def _close() -> None:
                assert self.db is not None
                async with self.db.session() as s:
                    await s.execute(
                        update(AlertRow)
                        .where(AlertRow.key == key, AlertRow.closed_at.is_(None))
                        .values(closed_tick=tick, closed_at=alert["closed_at"])
                    )

            await self._db(_close())

    def _forecast_quality(self, snap: Snapshot) -> dict[str, Any]:
        rows = self.history.recent(96)
        stations = {s.id: s for s in snap.stations}
        err: dict[str, float] = {f: 0.0 for f in FUEL_INDEX}
        tot: dict[str, float] = {f: 0.0 for f in FUEL_INDEX}
        for r in rows:
            st = stations.get(r.station_id)
            if st is None:
                continue
            prof = StationProfile(
                st.id,
                st.demand_profile,
                snap.region_factor(st.region_id),
                st.demand_multiplier,
            )
            mu = float(
                expected_demand(prof, np.array([r.tick]), snap.instance.tick_minutes)[
                    FUEL_INDEX[r.fuel_type], 0
                ]
            )
            err[r.fuel_type] += abs(r.demand_liters - mu)
            tot[r.fuel_type] += r.demand_liters
        wape = {f: round(err[f] / tot[f], 4) if tot[f] else None for f in FUEL_INDEX}
        for f, v in wape.items():
            if v is not None:
                m.FORECAST_WAPE.labels(f).set(v)
        overall = round(sum(err.values()) / sum(tot.values()), 4) if sum(tot.values()) else None
        return {"model_version": MODEL_VERSION, "method": "structural profile (champion)", "wape_by_fuel": wape,
                "wape_overall": overall, "window_ticks": 96, "history_rows": len(self.history.rows)}  # fmt: skip

    def _health(self, snap: Snapshot | None) -> dict[str, Any]:
        last_ok = self.client.last_success_at
        return {
            "simulator": {
                "circuit": self.client.breaker.state,
                "circuit_opens": self.client.breaker.opens,
                "last_success_age_s": round(time.time() - last_ok, 1) if last_ok else None,
                "last_error": self.client.last_error,
                "stream": self.sse_state,
                "stale": bool(snap and not snap.fresh),
            },
            "worker": {
                "refresh_ms": round(self.last_refresh_ms, 1),
                "plan_ms": round(self.last_plan_ms, 2),
                "epoch": self.epoch,
            },
            "database": {
                "ok": self.db_ok,
                "kind": None
                if self.db is None
                else ("sqlite" if self.db.is_sqlite else "postgres"),
            },
        }

    def _build_state(self, snap: Snapshot, risks: list[Any], blocked: str | None) -> dict[str, Any]:
        degraded: list[str] = []
        if snap.stale_header:
            degraded.append("simulator:stale_data")
        if snap.stale_parts:
            degraded.append("simulator:partial_refresh")
        if self.sse_state != "connected":
            degraded.append("stream:polling")
        if not self.db_ok:
            degraded.append("database:unavailable")
        elif self.db is not None and self.db.is_sqlite:
            degraded.append("database:local_sqlite")
        pending: dict[str, float] = {}
        for a in snap.allocations:
            if a.status == "PENDING":
                pending[a.source_depot_id] = pending.get(a.source_depot_id, 0.0) + a.quantity
        risk_by_station: dict[str, dict[str, str]] = {}
        for r in risks:
            risk_by_station.setdefault(r.station_id, {})[r.fuel_type] = r.level
        metrics = snap.metrics.model_dump() if snap.metrics else None
        return {
            "meta": {
                "tick": snap.tick,
                "sim_time": snap.instance.sim_time.isoformat(),
                "sim_status": snap.instance.status,
                "seed": snap.instance.seed,
                "scenario_id": snap.instance.scenario_id,
                "tick_minutes": snap.instance.tick_minutes,
                "epoch": self.epoch,
                "state_version": self.state_version,
                "fetched_at": snap.fetched_at,
                "stale": not snap.fresh,
                "stale_parts": snap.stale_parts,
                "degraded": degraded,
                "label": "SIMULATED",
            },
            "execution_blocked": blocked,
            "kpi": {
                **(metrics or {}),
                "risk_counts": {lvl: sum(1 for r in risks if r.level == lvl) for lvl in LEVELS},
                "open_alerts": len(self.open_alerts),
                "active_recommendations": len(self.active_recs),
            },
            "network": {
                "regions": [r.model_dump() for r in snap.regions],
                "depots": [
                    {
                        **d.model_dump(),
                        "pending_dispatch": pending.get(d.id, 0.0),
                        "dispatch_utilisation": round(
                            pending.get(d.id, 0.0) / d.dispatch_capacity_per_tick, 3
                        )
                        if d.dispatch_capacity_per_tick
                        else 0,
                        "next_supply": [
                            a.model_dump()
                            for a in snap.supply
                            if a.depot_id == d.id and a.status != "ARRIVED"
                        ][:3],
                    }
                    for d in snap.depots
                ],
                "stations": [
                    {**s.model_dump(), "risk": risk_by_station.get(s.id, {})} for s in snap.stations
                ],
                "routes": [r.model_dump() for r in snap.routes],
            },
            "supply": [a.model_dump() for a in snap.supply],
            "events": [e.model_dump() for e in snap.events],
            "allocations": [a.model_dump() for a in snap.allocations[:100]],
            "risks": [asdict(r) for r in risks],
            "recommendations": sorted(
                self.active_recs.values(), key=lambda r: -r["priority_unmet_l"]
            ),
            "alerts": {
                "open": sorted(
                    self.open_alerts.values(), key=lambda a: a["severity"] != "critical"
                ),
                "recent_closed": self.closed_alerts[:20],
            },
            "intelligence": {
                **self._forecast_quality(snap),
                "planner": {
                    "policy": "greedy",
                    "duration_ms": round(self.last_plan_ms, 2),
                },
            },
            "health": self._health(snap),
        }
