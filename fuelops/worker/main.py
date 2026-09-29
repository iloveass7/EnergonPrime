"""Worker process (x1, leader lock): SSE hint + 1 s safety poll -> coalesced refresh."""

from __future__ import annotations

import asyncio
import os
import random
import socket

from prometheus_client import start_http_server

from fuelops.cache.store import StateStore
from fuelops.config import get_settings
from fuelops.observability import metrics as m
from fuelops.observability.logging import configure_logging, log
from fuelops.persistence.db import Database
from fuelops.simclient.client import SimulatorClient
from fuelops.simclient.errors import SimulatorError
from fuelops.state.sync import SnapshotUnavailable
from fuelops.worker.engine import Engine


class Worker:
    def __init__(self) -> None:
        self.s = get_settings()
        self.client = SimulatorClient(
            self.s.sim_base_url,
            connect_timeout=self.s.sim_connect_timeout_s,
            read_timeout=self.s.sim_read_timeout_s,
            total_timeout=self.s.sim_total_timeout_s,
        )
        self.store = StateStore(self.s.redis_url)
        self.db = Database(self.s.sqlalchemy_url)
        self.engine = Engine(self.s, self.client, self.store, self.db)
        self.dirty = asyncio.Event()
        self.owner = f"{socket.gethostname()}:{os.getpid()}"
        self.is_leader = False

    async def sse_loop(self) -> None:
        backoff = 0.5
        while True:
            try:
                async for msg in self.client.stream():
                    if msg.event == "_connected":
                        self.engine.sse_state = "connected"
                        m.SIM_STREAM_CONNECTED.set(1)
                        backoff = 0.5
                        log.info("sse_connected")
                    elif msg.event == "simulator.notice" and "reset" in str(msg.data).lower():
                        self.engine.reset_hint = True
                    self.dirty.set()  # SSE is a hint: every event triggers a REST refresh
            except (SimulatorError, Exception) as exc:  # noqa: BLE001
                log.info("sse_down", error=f"{type(exc).__name__}: {exc}")
            self.engine.sse_state = "polling"
            m.SIM_STREAM_CONNECTED.set(0)
            m.SSE_RECONNECTS.inc()
            self.dirty.set()  # no Last-Event-ID replay: full resync on reconnect
            await asyncio.sleep(backoff + random.uniform(0, backoff / 2))
            backoff = min(8.0, backoff * 2)

    async def poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self.s.poll_interval_ms / 1000)
            self.dirty.set()

    async def leader_loop(self) -> None:
        while True:
            try:
                was = self.is_leader
                self.is_leader = await self.store.acquire_leader(self.owner)
                if self.is_leader != was:
                    log.info("leadership", leader=self.is_leader, owner=self.owner)
                await self.store.heartbeat({"owner": self.owner, "leader": self.is_leader, "sse": self.engine.sse_state,
                                            "tick": self.engine.prev.tick if self.engine.prev else None})  # fmt: skip
            except Exception as exc:  # noqa: BLE001 - redis down: keep refreshing standalone
                log.warning("redis_unavailable", error=str(exc))
                self.is_leader = True
            await asyncio.sleep(2)

    async def refresh_loop(self) -> None:
        while True:
            await self.dirty.wait()
            self.dirty.clear()
            if not self.is_leader:
                continue
            try:
                await self.engine.refresh_once()
            except (SnapshotUnavailable, SimulatorError) as exc:
                log.warning("refresh_failed", error=str(exc))
                await self._safe(self.engine.publish_degraded(getattr(exc, "kind", "unavailable")))
            except Exception as exc:  # noqa: BLE001 - never let the loop die
                log.exception("refresh_crashed", error=str(exc))
                await self._safe(self.engine.publish_degraded("error"))

    async def _safe(self, coro: object) -> None:
        try:
            await coro  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            log.warning("publish_failed", error=str(exc))

    async def run(self) -> None:
        configure_logging(self.s.log_level)
        start_http_server(self.s.worker_metrics_port)
        try:
            await self.db.init()
        except Exception as exc:  # noqa: BLE001
            log.warning("db_init_failed", error=str(exc))
            self.engine.db_ok = False
        await self._safe(self.engine.restore())
        log.info(
            "worker_start",
            sim=self.s.sim_base_url,
            db="sqlite" if self.db.is_sqlite else "postgres",
        )
        self.dirty.set()
        await asyncio.gather(
            self.leader_loop(), self.sse_loop(), self.poll_loop(), self.refresh_loop()
        )


def main() -> None:
    asyncio.run(Worker().run())


if __name__ == "__main__":
    main()
