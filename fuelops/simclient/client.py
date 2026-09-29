"""The one resilient integration client (CLAUDE.md hard rule): every simulator call goes here.

GET: timeouts, up to 3 attempts with 100 ms * 2^n + jitter, circuit breaker, stale header.
POST: never retried blindly; ambiguous outcomes raise SimAmbiguous for reconciliation.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator
from typing import Any, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from fuelops.domain.models import (
    Allocation,
    AllocationRequest,
    DemandRow,
    Depot,
    Instance,
    Metrics,
    Region,
    Route,
    SimEvent,
    Station,
    SupplyArrival,
)
from fuelops.observability import metrics as m
from fuelops.simclient.breaker import CircuitBreaker
from fuelops.simclient.errors import (
    SimAmbiguous,
    SimCircuitOpen,
    SimContractViolation,
    SimTimeout,
    SimulatorError,
    SimUnavailable,
    map_status,
)

T = TypeVar("T")
_ADAPTERS: dict[Any, TypeAdapter[Any]] = {}


def _adapter(tp: Any) -> TypeAdapter[Any]:
    if tp not in _ADAPTERS:
        _ADAPTERS[tp] = TypeAdapter(tp)
    return _ADAPTERS[tp]


class SimResult:
    """A parsed GET result plus whether the stale-data header was present."""

    __slots__ = ("data", "stale")

    def __init__(self, data: Any, stale: bool) -> None:
        self.data = data
        self.stale = stale


class SseMessage:
    __slots__ = ("data", "event")

    def __init__(self, event: str, data: Any) -> None:
        self.event = event
        self.data = data


class SimulatorClient:
    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = 0.5,
        read_timeout: float = 2.0,
        total_timeout: float = 3.0,
        attempts: int = 3,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self.attempts = attempts
        self.total_timeout = total_timeout
        self.breaker = breaker or CircuitBreaker()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(read_timeout, connect=connect_timeout),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            transport=transport,
        )
        self.last_success_at: float | None = None
        self.last_error: str | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---------------------------------------------------------------- core
    def _update_gauge(self) -> None:
        m.SIM_CIRCUIT_STATE.set({"closed": 0, "half_open": 1, "open": 2}[self.breaker.state])

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> SimResult:
        last: SimulatorError | None = None
        for attempt in range(self.attempts):
            if not self.breaker.allow():
                self._update_gauge()
                raise SimCircuitOpen(f"circuit open, not calling {path}")
            started = time.perf_counter()
            try:
                resp = await asyncio.wait_for(
                    self._http.get(path, params=params), self.total_timeout
                )
            except (TimeoutError, httpx.TimeoutException) as exc:
                last = SimTimeout(f"{path}: {type(exc).__name__}")
            except httpx.TransportError as exc:
                last = SimUnavailable(f"{path}: {type(exc).__name__}: {exc}")
            else:
                m.SIM_REQUEST_SECONDS.labels(path).observe(time.perf_counter() - started)
                if resp.status_code == 200:
                    self.breaker.success()
                    self._update_gauge()
                    self.last_success_at = time.time()
                    stale = resp.headers.get("x-simulator-stale", "").lower() == "true"
                    if stale:
                        m.SIM_STALE_RESPONSES.inc()
                    try:
                        return SimResult(resp.json(), stale)
                    except ValueError as exc:
                        raise SimContractViolation(f"{path}: body is not JSON") from exc
                last = map_status(resp.status_code, _json_or_none(resp))
                if not last.retryable:
                    self.breaker.success()  # the simulator answered; it is alive
                    self._update_gauge()
                    raise last
            m.SIM_ERRORS.labels(path, last.kind).inc()
            self.breaker.failure()
            self._update_gauge()
            self.last_error = str(last)
            if attempt < self.attempts - 1:
                await asyncio.sleep(0.1 * 2**attempt + random.uniform(0, 0.05))
        assert last is not None
        raise last

    async def _get_as(self, path: str, tp: Any, params: dict[str, Any] | None = None) -> SimResult:
        result = await self._get(path, params)
        try:
            result.data = _adapter(tp).validate_python(result.data)
        except ValidationError as exc:
            m.SIM_ERRORS.labels(path, "contract_violation").inc()
            raise SimContractViolation(f"{path}: {exc.error_count()} schema errors") from exc
        return result

    # ---------------------------------------------------------------- reads
    async def instance(self) -> SimResult:
        return await self._get_as("/v1/instance", Instance)

    async def regions(self) -> SimResult:
        return await self._get_as("/v1/regions", list[Region])

    async def depots(self) -> SimResult:
        return await self._get_as("/v1/depots", list[Depot])

    async def stations(self) -> SimResult:
        return await self._get_as("/v1/stations", list[Station])

    async def routes(self) -> SimResult:
        return await self._get_as("/v1/routes", list[Route])

    async def supply_arrivals(self) -> SimResult:
        return await self._get_as("/v1/supply-arrivals", list[SupplyArrival])

    async def events(self) -> SimResult:
        return await self._get_as("/v1/events", list[SimEvent])

    async def allocations(self) -> SimResult:
        return await self._get_as("/v1/allocations", list[Allocation])

    async def metrics(self) -> SimResult:
        return await self._get_as("/v1/metrics", Metrics)

    async def demand_history(self, limit: int, station_id: str | None = None) -> SimResult:
        params: dict[str, Any] = {"limit": max(1, min(2000, limit))}
        if station_id:
            params["station_id"] = station_id
        return await self._get_as("/v1/demand-history", list[DemandRow], params)

    async def health(self) -> dict[str, Any]:
        """Liveness only: /v1/health bypasses faults, so it never proves operational health."""
        resp = await self._http.get("/v1/health")
        return resp.json() if resp.status_code == 200 else {"status": f"http {resp.status_code}"}

    # ---------------------------------------------------------------- writes
    async def create_allocation(self, req: AllocationRequest) -> tuple[Allocation, int]:
        """One attempt. 201/200 -> (allocation, status). Domain errors -> SimRejected.

        Timeouts and 5xx raise SimAmbiguous: the caller must reconcile via GET /v1/allocations
        before any same-key retry (pipeline §3.4).
        """
        return await self._write("POST", "/v1/allocations", req.model_dump())

    async def cancel_allocation(self, allocation_id: int) -> tuple[Allocation, int]:
        return await self._write("POST", f"/v1/allocations/{allocation_id}/cancel", None)

    async def _write(self, method: str, path: str, body: Any) -> tuple[Allocation, int]:
        if not self.breaker.allow():
            raise SimCircuitOpen(f"circuit open, not calling {path}")
        started = time.perf_counter()
        try:
            resp = await asyncio.wait_for(
                self._http.request(method, path, json=body), self.total_timeout
            )
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            self.breaker.failure()
            m.SIM_ERRORS.labels(path, "ambiguous").inc()
            raise SimAmbiguous(f"{path}: {type(exc).__name__}") from exc
        m.SIM_REQUEST_SECONDS.labels(path.split("/")[2]).observe(time.perf_counter() - started)
        if resp.status_code in (200, 201):
            self.breaker.success()
            try:
                return Allocation.model_validate(resp.json()), resp.status_code
            except (ValueError, ValidationError) as exc:
                raise SimContractViolation(f"{path}: bad allocation body") from exc
        err = map_status(resp.status_code, _json_or_none(resp))
        if err.retryable:
            self.breaker.failure()
            m.SIM_ERRORS.labels(path, "ambiguous").inc()
            raise SimAmbiguous(str(err), status=err.status, code=err.code) from err
        self.breaker.success()
        raise err

    # ---------------------------------------------------------------- stream
    async def stream(self) -> AsyncIterator[SseMessage]:
        """Yield SSE messages, first a synthetic ``_connected``. Comments (': connected', ': keepalive') are skipped.

        Raises SimUnavailable when the stream cannot open (e.g. stream_disconnect -> 503).
        """
        async with self._http.stream(
            "GET", "/v1/stream", timeout=httpx.Timeout(10.0, read=45.0)
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                raise map_status(resp.status_code, _json_or_none(resp))
            yield SseMessage("_connected", None)
            name: str | None = None
            data: list[str] = []
            async for line in resp.aiter_lines():
                if line == "":
                    if data:
                        raw = "\n".join(data)
                        try:
                            payload: Any = json.loads(raw)
                        except ValueError:
                            payload = raw
                        yield SseMessage(name or "message", payload)
                    name, data = None, []
                elif line.startswith(":"):
                    continue
                else:
                    key, _, value = line.partition(":")
                    value = value.removeprefix(" ")
                    if key == "event":
                        name = value
                    elif key == "data":
                        data.append(value)


def _json_or_none(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
