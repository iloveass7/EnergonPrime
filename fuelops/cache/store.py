"""Redis hot state. Only the worker writes state keys (one pipeline per tick); the API only reads."""

from __future__ import annotations

import time
from typing import Any

import orjson
from redis.asyncio import Redis

STATE_KEY = "fo:state"
HEARTBEAT_KEY = "fo:hb:worker"
LEADER_KEY = "fo:lock:leader"
UPDATES_CHANNEL = "fo:updates"
STATE_TTL_S = 3600


class StateStore:
    def __init__(self, url: str, client: Redis | None = None) -> None:
        self.redis: Redis = client or Redis.from_url(
            url, socket_timeout=0.5, socket_connect_timeout=0.5
        )

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:  # noqa: BLE001
            return False

    async def publish_state(self, state: dict[str, Any]) -> None:
        payload = orjson.dumps(state)
        event = orjson.dumps(
            {
                "type": "state",
                "tick": state["meta"]["tick"],
                "state_version": state["meta"]["state_version"],
            }
        )
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.set(STATE_KEY, payload, ex=STATE_TTL_S)
            pipe.publish(UPDATES_CHANNEL, event)
            await pipe.execute()

    async def read_state(self) -> dict[str, Any] | None:
        raw = await self.redis.get(STATE_KEY)
        return orjson.loads(raw) if raw else None

    async def heartbeat(self, info: dict[str, Any]) -> None:
        await self.redis.set(HEARTBEAT_KEY, orjson.dumps({**info, "ts": time.time()}), ex=60)

    async def read_heartbeat(self) -> dict[str, Any] | None:
        raw = await self.redis.get(HEARTBEAT_KEY)
        return orjson.loads(raw) if raw else None

    async def acquire_leader(self, owner: str, ttl_ms: int = 5000) -> bool:
        if await self.redis.set(LEADER_KEY, owner, nx=True, px=ttl_ms):
            return True
        current = await self.redis.get(LEADER_KEY)
        if (
            current is not None
            and (current.decode() if isinstance(current, bytes) else current) == owner
        ):
            await self.redis.pexpire(LEADER_KEY, ttl_ms)
            return True
        return False

    async def notify(self, event: dict[str, Any]) -> None:
        await self.redis.publish(UPDATES_CHANNEL, orjson.dumps(event))

    async def close(self) -> None:
        await self.redis.aclose()
