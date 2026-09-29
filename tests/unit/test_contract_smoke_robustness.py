"""Review fixes: cleanup always runs, recorder never raises or swallows cancellation."""

import asyncio

import httpx
import pytest

import scripts.contract_smoke as smoke


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://sim.test", transport=httpx.MockTransport(handler))


def test_cleanup_runs_when_recorder_task_crashed(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    async def crashed() -> None:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte on stream")

    async def section(ctx: smoke.SmokeContext) -> None:
        ctx.sse = smoke.SseRecorder(ctx.client)
        ctx.sse._task = asyncio.create_task(crashed())
        await asyncio.sleep(0)

    monkeypatch.setattr(smoke, "SECTIONS", [("only", section)])

    async def run() -> int:
        async with _client(handler) as client:
            return await smoke.run_smoke(client, tmp_path, None)

    assert asyncio.run(run()) == 1
    assert ("POST", "/admin/faults/clear") in calls and (
        "POST",
        "/admin/pause",
    ) in calls


def test_repeated_identical_connection_errors_are_all_recorded(monkeypatch) -> None:
    monkeypatch.setattr(smoke, "SSE_RECONNECT_S", 0.01)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async def run() -> int:
        async with _client(handler) as client:
            rec = smoke.SseRecorder(client)
            rec.start()
            await asyncio.sleep(0.2)
            await rec.stop()
            return len(rec.segments)

    assert asyncio.run(run()) >= 3


def test_stop_does_not_swallow_cancellation_of_caller() -> None:
    async def stubborn() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(10)

    async def run() -> bool:
        async with _client(lambda r: httpx.Response(200)) as client:
            rec = smoke.SseRecorder(client)
            rec._task = asyncio.create_task(stubborn())
            caller = asyncio.create_task(rec.stop(timeout_s=5.0))
            await asyncio.sleep(0.05)
            caller.cancel()
            await asyncio.wait({caller})
            rec._task.cancel()
            return caller.cancelled()

    assert asyncio.run(run()) is True


@pytest.mark.parametrize("qty", [1, 2, 1000])
def test_mismatch_quantity_is_positive_and_different(qty: float) -> None:
    other = smoke.mismatch_quantity(qty)
    assert 0 < other and other != qty
