"""End-to-end smoke against the running stack (API on :8080). All data is simulated.

risk -> recommendation -> approve (x3 concurrently) -> exactly one allocation -> ARRIVED,
then stale_data blocks approval with 409 STATE_STALE, then recovery.
Uses only /api/v1 (including the dual-gated test plane).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any

import httpx

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {detail}"))
    return ok


async def wait_for(fn: Any, timeout: float = 15.0, every: float = 0.5) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = await fn()
        if value:
            return value
        await asyncio.sleep(every)
    return None


async def main(base: str) -> int:
    async with httpx.AsyncClient(base_url=base, timeout=15.0) as c:

        async def dash() -> dict[str, Any]:
            return (await c.get("/api/v1/dashboard")).json()

        async def step(n: int) -> None:
            for _ in range(n):
                (await c.post("/api/v1/test/simulator/step")).raise_for_status()

        await c.post("/api/v1/test/simulator/faults-clear")
        await c.post("/api/v1/test/simulator/reset")
        await c.post("/api/v1/test/simulator/pause")
        epoch0 = (await wait_for(lambda: _meta_tick_zero(c))) or {}
        check("reset_detected", epoch0.get("tick") == 0, f"{epoch0}")

        await step(48)  # 12 simulated hours: stations drain without our shipments
        state = await wait_for(lambda: _with_recs(c, 48), timeout=20)
        check("recommendations_generated", bool(state), "no recommendation after 48 ticks")
        if not state:
            return 1
        rec = state["data"]["recommendations"][0]
        check(
            "recommendation_explained",
            len(rec["comparison"]) >= 2 and bool(rec["reasons"]),
            f"{rec['comparison']}",
        )

        approvals = await asyncio.gather(
            *[
                c.post(f"/api/v1/recommendations/{rec['id']}/approve", json={"note": "e2e"})
                for _ in range(3)
            ]
        )
        bodies = [r.json() for r in approvals]
        check(
            "approve_statuses",
            all(r.status_code == 200 for r in approvals),
            f"{[(r.status_code, b.get('code'), b.get('detail')) for r, b in zip(approvals, bodies, strict=True)]}",
        )
        keys = {i["idempotency_key"] for b in bodies for i in b.get("data", {}).get("intents", [])}
        if not check("one_intent_key", len(keys) == 1, f"{keys} {bodies[0]}"):
            return 1
        key = next(iter(keys))
        final = await wait_for(lambda: _intent_status(c, rec["id"]), timeout=10)
        check("intent_accepted", final == "ACCEPTED", f"{final}")

        # reads are served from the worker's published state: allow one refresh (~1 s)
        await wait_for(lambda: _alloc_status(c, key, None), timeout=6)
        ledger = (await c.get("/api/v1/allocations")).json()["data"]["allocations"]
        ours = [a for a in ledger if a["idempotency_key"] == key]
        check("exactly_one_allocation", len(ours) == 1, f"{len(ours)} allocations for {key}")

        await step(rec["transit_ticks"] + 1)
        arrived = await wait_for(lambda: _alloc_status(c, key, "ARRIVED"), timeout=10)
        check("allocation_arrived", bool(arrived), "not ARRIVED after transit")

        await c.post(
            "/api/v1/test/simulator/faults/inject",
            json={"type": "stale_data", "duration_seconds": 30},
        )
        blocked = await wait_for(lambda: _blocked(c), timeout=6)
        check("stale_blocks_execution", bool(blocked), "execution not blocked")
        recs = (await c.get("/api/v1/recommendations")).json()["data"]
        if recs:
            r = await c.post(f"/api/v1/recommendations/{recs[0]['id']}/approve")
            check(
                "approve_while_stale_409",
                r.status_code == 409 and r.json().get("code") == "STATE_STALE",
                f"{r.status_code} {r.text[:120]}",
            )
        await c.post("/api/v1/test/simulator/faults-clear")
        recovered = await wait_for(lambda: _unblocked(c), timeout=8)
        check("recovers_after_clear", bool(recovered), "still blocked")

        status = (await c.get("/api/v1/system/status")).json()["data"]
        check(
            "system_status",
            status["components"]["fuel_simulator"]["status"] == "healthy",
            f"{status['components']['fuel_simulator']}",
        )

    passed = sum(ok for _, ok, _ in RESULTS)
    print(f"E2E SMOKE: {passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


async def _meta_tick_zero(c: httpx.AsyncClient) -> dict[str, Any] | None:
    r = await c.get("/api/v1/dashboard")
    meta = r.json().get("meta", {}) if r.status_code == 200 else {}
    return meta if meta.get("tick") == 0 else None


async def _with_recs(c: httpx.AsyncClient, tick: int) -> dict[str, Any] | None:
    """Recommendations planned on the settled tick (the world is paused after stepping)."""
    r = (await c.get("/api/v1/dashboard")).json()
    recs = r.get("data", {}).get("recommendations") or []
    meta = r.get("meta", {})
    settled = meta.get("tick") == tick and not meta.get("execution_blocked")
    fresh = all(x["valid_until_tick"] >= tick for x in recs)
    return r if settled and recs and fresh else None


async def _intent_status(c: httpx.AsyncClient, rec_id: str) -> str | None:
    intents = (await c.get(f"/api/v1/recommendations/{rec_id}")).json()["data"].get("intents", [])
    status = intents[0]["status"] if intents else None
    return status if status in ("ACCEPTED", "REJECTED", "NEEDS_REVIEW") else None


async def _alloc_status(c: httpx.AsyncClient, key: str, want: str | None) -> bool:
    ledger = (await c.get("/api/v1/allocations")).json()["data"]["allocations"]
    return any(
        a["idempotency_key"] == key and (want is None or a["status"] == want) for a in ledger
    )


async def _blocked(c: httpx.AsyncClient) -> bool:
    return bool((await c.get("/api/v1/dashboard")).json()["meta"].get("execution_blocked"))


async def _unblocked(c: httpx.AsyncClient) -> bool:
    return not (await c.get("/api/v1/dashboard")).json()["meta"].get("execution_blocked")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    sys.exit(asyncio.run(main(parser.parse_args().base_url)))
