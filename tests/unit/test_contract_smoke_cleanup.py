"""An aborted smoke run must never leave a fault active or the simulator running."""

import asyncio
import json

import httpx
import pytest

import scripts.contract_smoke as smoke


async def _boom(ctx: smoke.SmokeContext) -> None:
    await ctx.call("instance", "GET", "/v1/instance")
    raise RuntimeError("section blew up mid-fault")


def test_cleanup_runs_when_section_raises(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    monkeypatch.setattr(smoke, "SECTIONS", [("boom", _boom)])

    async def run() -> int:
        async with httpx.AsyncClient(
            base_url="http://sim.test", transport=httpx.MockTransport(handler)
        ) as client:
            return await smoke.run_smoke(client, tmp_path, None)

    assert asyncio.run(run()) == 1
    assert calls[0] == ("GET", "/v1/instance")
    assert calls[-2:] == [("POST", "/admin/faults/clear"), ("POST", "/admin/pause")]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["all_passed"] is False
    failed = [c for c in manifest["checks"] if not c["ok"]]
    assert [c["name"] for c in failed] == ["section_boom_completed"]
    assert "section blew up mid-fault" in failed[0]["detail"]
