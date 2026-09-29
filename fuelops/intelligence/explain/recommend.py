"""Turn planner legs into explainable recommendations with a comparison panel (pipeline §4.4).

Numbers are model estimates on simulated data; every quantity is in simulated liters.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from fuelops.domain.snapshot import Snapshot
from fuelops.intelligence.allocation.greedy import Leg, PlanResult
from fuelops.intelligence.forecast.profile import MODEL_VERSION
from fuelops.intelligence.scoring.risk import (
    KeyState,
    RiskItem,
    project,
    shortage_probability,
)


def _stockout_hours(
    inv0: float, key: KeyState, arrivals: np.ndarray, tick_minutes: int
) -> float | None:
    _, unmet = project(inv0, key.mu, arrivals)
    if not np.any(unmet > 1e-6):
        return None
    return round((int(np.argmax(unmet > 1e-6)) + 1) * tick_minutes / 60, 2)


def build_recommendations(
    snapshot: Snapshot,
    keys: list[KeyState],
    risks: list[RiskItem],
    plan: PlanResult,
    *,
    valid_ticks: int,
) -> list[dict[str, Any]]:
    by_key = {(k.station_id, k.fuel): k for k in keys}
    risk_by_key = {(r.station_id, r.fuel_type): r for r in risks}
    tick_minutes = snapshot.instance.tick_minutes
    depots = {d.id: d for d in snapshot.depots}
    recs: list[dict[str, Any]] = []
    for leg in plan.legs:
        key = by_key[(leg.station_id, leg.fuel_type)]
        risk = risk_by_key[(leg.station_id, leg.fuel_type)]
        with_leg = key.arrivals.copy()
        with_leg[leg.arrival_index] += leg.quantity
        prob_before = shortage_probability(key.inventory, key.mu, key.arrivals, key.noise)
        prob_after = shortage_probability(key.inventory, key.mu, with_leg, key.noise)
        alts = sorted(
            (
                a
                for a in plan.alternatives.get((leg.station_id, leg.fuel_type), [])
                if a.route_id != leg.route_id
            ),
            key=lambda a: (a.quantity <= 0, a.unmet_after),
        )
        runner = alts[0] if alts else None
        comparison = [
            {"plan": "No new shipment", "projected_unmet_l": round(leg.unmet_before, 1),
             "risk": round(prob_before, 3), "binding_constraint": None, "why": "reference"},
            {"plan": f"Recommended: {leg.quantity:,.0f} L via {leg.route_id}", "projected_unmet_l": round(leg.unmet_after, 1),
             "risk": round(prob_after, 3), "binding_constraint": plan.binding.get((leg.station_id, leg.fuel_type)),
             "why": "largest projected unmet-liter reduction among feasible legs"},
        ]  # fmt: skip
        if runner is not None:
            comparison.append(
                {"plan": f"Runner-up: {runner.quantity:,.0f} L via {runner.route_id}" if runner.quantity > 0 else f"Not possible: {runner.route_id}",
                 "projected_unmet_l": round(runner.unmet_after, 1), "risk": None, "binding_constraint": None, "why": runner.reason}
            )  # fmt: skip
        reasons = list(risk.drivers)
        depot = depots.get(leg.depot_id)
        if depot is not None:
            reasons.append(
                f"{depot.name} holds {depot.inventory.get(leg.fuel_type, 0.0):,.0f} L {leg.fuel_type}"
            )
        if runner is not None and runner.quantity <= 0:
            reasons.append(f"{runner.route_id} not usable: {runner.reason}")
        n_routes = sum(1 for r in snapshot.routes if r.destination_station_id == leg.station_id)
        if n_routes == 1:
            reasons.append("station has a single route: no reroute possible if it is disrupted")
        confidence = "HIGH" if snapshot.fresh else "LOW"
        rec_id = (
            "rec-"
            + hashlib.sha1(
                f"{snapshot.epoch}|{snapshot.tick}|{leg.station_id}|{leg.fuel_type}|{leg.route_id}|{leg.quantity}".encode()
            ).hexdigest()[:10]
        )
        recs.append(
            {
                "id": rec_id,
                "version": 1,
                "epoch": snapshot.epoch,
                "created_tick": snapshot.tick,
                "valid_until_tick": snapshot.tick + valid_ticks,
                "state_version": snapshot.state_version,
                "status": "PROPOSED",
                "station_id": leg.station_id,
                "station_name": key.station_name,
                "fuel_type": leg.fuel_type,
                "depot_id": leg.depot_id,
                "depot_name": depot.name if depot else leg.depot_id,
                "route_id": leg.route_id,
                "quantity": leg.quantity,
                "transit_ticks": leg.transit_ticks,
                "eta_tick": snapshot.tick + leg.transit_ticks,
                "level": risk.level,
                "priority_unmet_l": round(leg.unmet_before, 1),
                "impact": {
                    "unmet_before_l": round(leg.unmet_before, 1),
                    "unmet_after_l": round(leg.unmet_after, 1),
                    "risk_before": round(prob_before, 3),
                    "risk_after": round(prob_after, 3),
                    "stockout_before_h": _stockout_hours(
                        key.inventory, key, key.arrivals, tick_minutes
                    ),
                    "stockout_after_h": _stockout_hours(key.inventory, key, with_leg, tick_minutes),
                    "inventory_before": [
                        round(float(v), 1) for v in project(key.inventory, key.mu, key.arrivals)[0]
                    ],
                    "inventory_after": [
                        round(float(v), 1) for v in project(key.inventory, key.mu, with_leg)[0]
                    ],
                    "capacity": key.capacity,
                },
                "comparison": comparison,
                "reasons": reasons,
                "confidence": confidence,
                "needs_review": confidence == "LOW",
                "policy_used": "greedy",
                "model_version": MODEL_VERSION,
                "label": "model-estimated on simulated data",
            }
        )
    recs.sort(key=lambda r: -r["priority_unmet_l"])
    return recs


def legs_equivalent(a: dict[str, Any], b: dict[str, Any], tolerance: float = 0.15) -> bool:
    """Keep an existing recommendation instead of churning it every tick."""
    return (
        a["route_id"] == b["route_id"]
        and a["fuel_type"] == b["fuel_type"]
        and abs(a["quantity"] - b["quantity"]) <= tolerance * max(a["quantity"], 1.0)
    )


def leg_of(rec: dict[str, Any]) -> Leg:  # for re-validation callers
    return Leg(rec["route_id"], rec["depot_id"], rec["station_id"], rec["fuel_type"], rec["quantity"],
               rec["transit_ticks"], rec["transit_ticks"] - 1, 0.0, 0.0)  # fmt: skip
