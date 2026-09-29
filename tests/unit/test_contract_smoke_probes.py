"""Fault probes: the stream probe records its envelope; random faults record one hit."""

import asyncio
import json
from collections.abc import Callable

import httpx

from scripts.contract_smoke import (
    FixtureWriter,
    SmokeContext,
    call_until_status,
    probe_stream,
)


def _run(handler: Callable[[httpx.Request], httpx.Response], tmp_path, coro_fn):
    async def run():
        async with httpx.AsyncClient(
            base_url="http://sim.test", transport=httpx.MockTransport(handler)
        ) as client:
            return await coro_fn(SmokeContext(client, FixtureWriter(tmp_path)))

    return asyncio.run(run())


def test_probe_stream_records_disconnect_envelope(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/stream"
        return httpx.Response(503, json={"detail": {"code": "FAULT_INJECTED"}})

    ex = _run(handler, tmp_path, lambda ctx: probe_stream(ctx, "stream_disconnect_503"))
    assert ex.status == 503
    saved = json.loads((tmp_path / "001_stream_disconnect_503.json").read_text())
    assert saved["response"]["body"] == {"detail": {"code": "FAULT_INJECTED"}}


def test_call_until_status_records_only_the_hit(tmp_path) -> None:
    replies = iter([200, 200, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(replies), json={"error": {"code": "FAULT_INJECTED"}})

    ex, attempts = _run(
        handler,
        tmp_path,
        lambda ctx: call_until_status(ctx, "instance_error_rate_503", "/v1/instance", 503, 5),
    )
    assert (ex.status, attempts) == (503, 3)
    assert [p.name for p in tmp_path.glob("*.json")] == ["001_instance_error_rate_503.json"]


def test_call_until_status_gives_up_without_recording(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    ex, attempts = _run(
        handler,
        tmp_path,
        lambda ctx: call_until_status(ctx, "x", "/v1/instance", 503, 4),
    )
    assert (ex, attempts) == (None, 4)
    assert list(tmp_path.glob("*.json")) == []
