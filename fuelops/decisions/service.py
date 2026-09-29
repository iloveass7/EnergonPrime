"""Approve / reject / cancel with the allocation-intent state machine (pipeline §3.4).

APPROVED -> SUBMITTING (intent + body hash committed first) -> ACCEPTED | REJECTED | RECONCILING
RECONCILING -> ACCEPTED (key found in /v1/allocations) | SUBMITTING (one same-key retry) | NEEDS_REVIEW
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from fuelops.domain.models import AllocationRequest
from fuelops.intelligence.validate.validator import LegSpec, validate_legs
from fuelops.observability import metrics as m
from fuelops.observability.logging import log
from fuelops.persistence.db import Database, IntentRow, RecommendationRow
from fuelops.simclient.client import SimulatorClient
from fuelops.simclient.errors import (
    SimAmbiguous,
    SimCircuitOpen,
    SimRejected,
    SimulatorError,
)
from fuelops.state.sync import fetch_snapshot


class _AlreadyClaimed(Exception):
    pass


@dataclass
class DecisionError(Exception):
    status: int
    code: str
    detail: str
    upstream_code: str | None = None


def idempotency_key(epoch: int, rec_id: str, version: int, leg: int, suffix: str = "") -> str:
    key = f"fo-{epoch}-{rec_id.removeprefix('rec-')}-v{version}-{leg}{suffix}"
    return key[:150]


def body_hash(body: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def intent_view(row: IntentRow) -> dict[str, Any]:
    return {"idempotency_key": row.idempotency_key, "status": row.status, "sim_allocation_id": row.sim_allocation_id,
            "upstream_code": row.upstream_code, "detail": row.detail, "quantity": row.body.get("quantity"),
            "route_id": row.body.get("route_id")}  # fmt: skip


class DecisionService:
    def __init__(self, db: Database, client: SimulatorClient, *, min_leg: float = 200.0) -> None:
        self.db = db
        self.client = client
        self.min_leg = min_leg

    async def approve(
        self,
        rec_id: str,
        actor: str,
        quantity: float | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        row = await self.db.get_rec(rec_id)
        if row is None:
            raise DecisionError(404, "REC_NOT_FOUND", f"no recommendation {rec_id}")
        if row.status in ("APPROVED", "EXECUTED"):
            return await self._result(
                row
            )  # double-click / client retry: same intents, one allocation
        if row.status != "PROPOSED":
            raise DecisionError(409, "REC_NOT_ACTIONABLE", f"recommendation is {row.status}")
        rec = dict(row.payload)

        # execution freshness gate (A1): a brand-new snapshot, fully fresh
        try:
            snap = await fetch_snapshot(self.client, None)
        except (SimulatorError, Exception) as exc:
            raise DecisionError(
                503, "SIMULATOR_UNAVAILABLE", f"cannot verify current state: {exc}"
            ) from exc
        if not snap.fresh:
            raise DecisionError(409, "STATE_STALE", "simulator data is stale; execution blocked")
        if snap.tick > rec["valid_until_tick"] + 8:
            await self._set_status(row, "EXPIRED", actor, "expired before approval")
            raise DecisionError(
                409,
                "REC_EXPIRED",
                f"created at tick {rec['created_tick']}, now {snap.tick}",
            )

        qty = float(quantity) if quantity is not None else float(rec["quantity"])
        version = int(rec.get("version", 1)) + (
            1 if quantity is not None and qty != rec["quantity"] else 0
        )
        leg = LegSpec(rec["route_id"], rec["depot_id"], rec["station_id"], rec["fuel_type"], qty)
        violations = validate_legs(snap, [leg], min_leg=self.min_leg)
        if violations:
            v = violations[0]
            await self._set_status(row, "INVALIDATED", actor, f"{v.code}: {v.detail}")
            raise DecisionError(409, "REC_INVALIDATED", v.detail, upstream_code=v.code)

        body = AllocationRequest(
            idempotency_key=idempotency_key(rec["epoch"], rec_id, version, 0),
            source_depot_id=rec["depot_id"],
            destination_station_id=rec["station_id"],
            route_id=rec["route_id"],
            fuel_type=rec["fuel_type"],
            quantity=qty,
        )
        # Claim the recommendation atomically, then record the intent BEFORE the POST (A12).
        # A concurrent approve loses the claim and gets the winner's result: one allocation.
        now = time.time()
        try:
            async with self.db.session() as s:
                claimed = await s.execute(
                    update(RecommendationRow)
                    .where(RecommendationRow.id == rec_id, RecommendationRow.status == "PROPOSED")
                    .values(status="APPROVED", decided_at=now, decided_by=actor, decision_note=note,
                            payload={**rec, "status": "APPROVED", "approved_quantity": qty})
                )  # fmt: skip
                if claimed.rowcount != 1:  # type: ignore[attr-defined]
                    raise _AlreadyClaimed
                s.add(IntentRow(rec_id=rec_id, leg=0, idempotency_key=body.idempotency_key, body=body.model_dump(),
                                body_hash=body_hash(body.model_dump()), status="SUBMITTING", created_at=now, updated_at=now))  # fmt: skip
        except (_AlreadyClaimed, IntegrityError):
            row = await self.db.get_rec(rec_id)
            assert row is not None
            if row.status not in ("APPROVED", "EXECUTED"):  # superseded/expired meanwhile
                raise DecisionError(
                    409, "REC_NOT_ACTIONABLE", f"recommendation is {row.status}"
                ) from None
            m.OPERATOR_ACTIONS.labels("approve_duplicate").inc()
            return await self._result(row)
        m.OPERATOR_ACTIONS.labels("approve").inc()
        await self.db.audit("recommendation.approved", actor, {"rec_id": rec_id, "quantity": qty, "note": note,
                                                               "idempotency_key": body.idempotency_key}, epoch=rec["epoch"], tick=snap.tick)  # fmt: skip
        status = await self.submit(body)
        async with self.db.session() as s:
            db_row = await s.get(RecommendationRow, rec_id)
            if db_row is not None and status == "ACCEPTED":
                db_row.status = "EXECUTED"
                db_row.payload = {**db_row.payload, "status": "EXECUTED"}
        row = await self.db.get_rec(rec_id)
        assert row is not None
        return await self._result(row)

    async def submit(self, body: AllocationRequest, retry_budget: int = 1) -> str:
        """POST with reconciliation. Returns the final intent status."""
        key = body.idempotency_key
        try:
            alloc, http_status = await self.client.create_allocation(body)
            await self._intent(key, "ACCEPTED", sim_id=alloc.id, detail=f"HTTP {http_status}")
            return "ACCEPTED"
        except SimRejected as exc:
            await self._intent(key, "REJECTED", code=exc.code, detail=str(exc))
            return "REJECTED"
        except SimCircuitOpen as exc:
            await self._intent(key, "NEEDS_REVIEW", code="CIRCUIT_OPEN", detail=str(exc))
            return "NEEDS_REVIEW"
        except (SimAmbiguous, SimulatorError) as exc:
            await self._intent(key, "RECONCILING", code=exc.code, detail=str(exc))
            log.warning("submit_ambiguous", key=key, error=str(exc))
        # reconcile: did the simulator record it?
        try:
            ledger = (await self.client.allocations()).data
            found = next((a for a in ledger if a.idempotency_key == key), None)
        except SimulatorError:
            found = None
            ledger = None
        if found is not None:
            await self._intent(
                key,
                "ACCEPTED",
                sim_id=found.id,
                detail="reconciled via GET /v1/allocations",
            )
            return "ACCEPTED"
        if ledger is not None and retry_budget > 0:
            await self._intent(
                key, "SUBMITTING", detail="not found after reconcile; same-key retry"
            )
            return await self.submit(body, retry_budget - 1)
        await self._intent(key, "NEEDS_REVIEW", detail="outcome unknown after reconcile and retry")
        return "NEEDS_REVIEW"

    async def reject(self, rec_id: str, actor: str, reason: str) -> dict[str, Any]:
        row = await self.db.get_rec(rec_id)
        if row is None:
            raise DecisionError(404, "REC_NOT_FOUND", f"no recommendation {rec_id}")
        if row.status != "PROPOSED":
            raise DecisionError(409, "REC_NOT_ACTIONABLE", f"recommendation is {row.status}")
        await self._set_status(row, "REJECTED", actor, reason)
        m.OPERATOR_ACTIONS.labels("reject").inc()
        await self.db.audit(
            "recommendation.rejected",
            actor,
            {"rec_id": rec_id, "reason": reason},
            epoch=row.epoch,
        )
        row2 = await self.db.get_rec(rec_id)
        assert row2 is not None
        return await self._result(row2)

    async def cancel(self, allocation_id: int, actor: str) -> dict[str, Any]:
        try:
            alloc, _ = await self.client.cancel_allocation(allocation_id)
        except SimRejected as exc:
            raise DecisionError(
                exc.status or 409, "CANCEL_REJECTED", str(exc), upstream_code=exc.code
            ) from exc
        except SimulatorError as exc:
            raise DecisionError(503, "SIMULATOR_UNAVAILABLE", str(exc)) from exc
        m.OPERATOR_ACTIONS.labels("cancel").inc()
        await self.db.audit("allocation.cancelled", actor, {"allocation_id": allocation_id})
        return alloc.model_dump()

    # ------------------------------------------------------------------ helpers
    async def _intent(
        self,
        key: str,
        status: str,
        *,
        sim_id: int | None = None,
        code: str | None = None,
        detail: str | None = None,
    ) -> None:
        m.INTENTS.labels(status).inc()
        async with self.db.session() as s:
            row = (
                await s.execute(select(IntentRow).where(IntentRow.idempotency_key == key))
            ).scalar_one()
            row.status = status
            row.updated_at = time.time()
            if sim_id is not None:
                row.sim_allocation_id = sim_id
            if code is not None:
                row.upstream_code = code
            if detail is not None:
                row.detail = detail
        log.info("intent", key=key, status=status, sim_id=sim_id, code=code)

    async def _set_status(self, row: RecommendationRow, status: str, actor: str, note: str) -> None:
        async with self.db.session() as s:
            db_row = await s.get(RecommendationRow, row.id)
            assert db_row is not None
            db_row.status = status
            db_row.payload = {**db_row.payload, "status": status}
            db_row.decided_at, db_row.decided_by, db_row.decision_note = (
                time.time(),
                actor,
                note,
            )

    async def _result(self, row: RecommendationRow) -> dict[str, Any]:
        async with self.db.session() as s:
            intents = (
                (
                    await s.execute(
                        select(IntentRow).where(IntentRow.rec_id == row.id).order_by(IntentRow.id)
                    )
                )
                .scalars()
                .all()
            )
        return {"recommendation": {**row.payload, "status": row.status, "decided_by": row.decided_by, "decision_note": row.decision_note},
                "intents": [intent_view(i) for i in intents]}  # fmt: skip
