"""Operator commands: approve / reject recommendations, cancel PENDING allocations."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field

from fuelops.api.deps import AppState, get_app_state, problem, require
from fuelops.decisions.service import DecisionError

router = APIRouter(prefix="/api/v1")


class ApproveBody(BaseModel):
    quantity: float | None = Field(default=None, gt=0, le=20000)
    note: str | None = Field(default=None, max_length=500)


class RejectBody(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


def _err(exc: DecisionError) -> Any:
    return problem(
        exc.status,
        exc.code,
        exc.detail,
        upstream_code=exc.upstream_code,
        retryable=exc.status == 503,
    )


@router.post("/recommendations/{rec_id}/approve")
async def approve(rec_id: str, body: ApproveBody | None = None, actor: str = Depends(require("operator")),
                  fo: AppState = Depends(get_app_state)) -> Any:  # fmt: skip
    body = body or ApproveBody()
    try:
        result = await fo.decisions.approve(rec_id, actor, body.quantity, body.note)
    except DecisionError as exc:
        return _err(exc)
    await fo.store.notify({"type": "recommendation.updated", "id": rec_id})
    return ORJSONResponse({"data": result})


@router.post("/recommendations/{rec_id}/reject")
async def reject(rec_id: str, body: RejectBody, actor: str = Depends(require("operator")),
                 fo: AppState = Depends(get_app_state)) -> Any:  # fmt: skip
    try:
        result = await fo.decisions.reject(rec_id, actor, body.reason)
    except DecisionError as exc:
        return _err(exc)
    await fo.store.notify({"type": "recommendation.updated", "id": rec_id})
    return ORJSONResponse({"data": result})


@router.post("/allocations/{allocation_id}/cancel")
async def cancel(
    allocation_id: int,
    actor: str = Depends(require("operator")),
    fo: AppState = Depends(get_app_state),
) -> Any:
    try:
        result = await fo.decisions.cancel(allocation_id, actor)
    except DecisionError as exc:
        return _err(exc)
    return ORJSONResponse({"data": result})
