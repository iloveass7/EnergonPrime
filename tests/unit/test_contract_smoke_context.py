"""SmokeContext recording behaviour."""

import asyncio

import httpx

from scripts.contract_smoke import FixtureWriter, SmokeContext


def test_unrecorded_call_writes_no_fixture(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "route-a", "status": "AVAILABLE"}])

    async def run() -> tuple[int, int]:
        async with httpx.AsyncClient(
            base_url="http://sim.test", transport=httpx.MockTransport(handler)
        ) as client:
            ctx = SmokeContext(client, FixtureWriter(tmp_path))
            polled = await ctx.call("poll", "GET", "/v1/routes", record=False)
            await ctx.call("routes", "GET", "/v1/routes")
            return polled.status, len(list(tmp_path.glob("*.json")))

    status, files = asyncio.run(run())
    assert status == 200
    assert files == 1
