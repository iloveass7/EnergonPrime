"""Phase 0 contract smoke: drive the pinned simulator, assert the contract, record fixtures.

Recon tool, deliberately outside ``fuelops/`` (plan Decisions 1-2): it calls the simulator
with raw httpx and no retries so the recorded fixtures show true wire behaviour, and it
calls ``/admin/*`` directly. Product code must never import this module.
Every recorded quantity is simulated.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

IMAGE = "asifmahmoud414/bup-fuel-supply-simulator:1.0.0"
PROVENANCE = "SIMULATED — recorded from simulator image 1.0.0"
FIXTURE_VERSION = 1
VOLATILE_BODY_KEYS = frozenset({"wall_time", "start_wall_time", "end_wall_time"})
DROPPED_HEADERS = frozenset({"date", "server"})
_NORMALIZE_DROP = VOLATILE_BODY_KEYS | {"elapsed_ms"}
DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_OUT = Path("tests/fixtures/contract/sim-1.0.0")
HEALTH_WAIT_S = 30.0


@dataclass
class Exchange:
    name: str
    section: str
    method: str
    path: str
    query: dict[str, Any]
    req_body: Any
    status: int
    headers: dict[str, str | None]
    body: Any
    body_text: str | None
    elapsed_ms: float
    sim_tick: int | None
    volatile: bool


def envelope_code(body: Any) -> str | None:
    """Error code from any simulator envelope; ``VALIDATION`` for FastAPI 422 lists."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("detail"), list):
        return "VALIDATION"
    for key in ("detail", "error"):
        inner = body.get(key)
        if isinstance(inner, dict) and isinstance(inner.get("code"), str):
            return str(inner["code"])
    return None


def is_stale(headers: Mapping[str, str]) -> bool:
    """True when the stale-data header is present with value ``true`` (any case)."""
    value = headers.get("x-simulator-stale")
    return value is not None and value.strip().lower() == "true"


def normalize(fixture: Any) -> Any:
    """Drop wall-clock keys at any depth so two runs can be compared."""
    if isinstance(fixture, dict):
        return {k: normalize(v) for k, v in fixture.items() if k not in _NORMALIZE_DROP}
    if isinstance(fixture, list):
        return [normalize(v) for v in fixture]
    return fixture


class FixtureWriter:
    """Writes one ``{seq:03d}_{name}.json`` file per exchange, in call order."""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        for stale in out_dir.glob("[0-9][0-9][0-9]_*.json"):
            stale.unlink()
        self.index: list[dict[str, Any]] = []
        self._names: set[str] = set()

    def write(self, ex: Exchange) -> Path:
        return self.write_raw(
            ex.name,
            ex.section,
            {
                "volatile": ex.volatile,
                "request": {
                    "method": ex.method,
                    "path": ex.path,
                    "query": ex.query,
                    "body": ex.req_body,
                },
                "response": {
                    "status": ex.status,
                    "headers": ex.headers,
                    "body": ex.body,
                    "body_text": ex.body_text,
                },
                "sim_tick": ex.sim_tick,
                "elapsed_ms": round(ex.elapsed_ms, 1),
            },
        )

    def write_raw(self, name: str, section: str, payload: dict[str, Any]) -> Path:
        if name in self._names:
            raise ValueError(f"duplicate fixture name: {name}")
        self._names.add(name)
        doc = {
            "fixture_version": FIXTURE_VERSION,
            "provenance": PROVENANCE,
            "name": name,
            "section": section,
            **payload,
        }
        path = self.out_dir / f"{len(self.index) + 1:03d}_{name}.json"
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8")
        self.index.append(
            {
                "seq": len(self.index) + 1,
                "name": name,
                "file": path.name,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
        return path


class SmokeAbort(Exception):
    """A precondition failed; later sections would only produce noise."""


def _decode(resp: httpx.Response) -> tuple[Any, str | None]:
    if not resp.content:
        return None, None
    if "json" in resp.headers.get("content-type", ""):
        try:
            return resp.json(), None
        except ValueError:
            pass
    return None, resp.text


class SmokeContext:
    """Shared state for one smoke run: client, recorder, checks and discovered world."""

    def __init__(self, client: httpx.AsyncClient, writer: FixtureWriter) -> None:
        self.client = client
        self.writer = writer
        self.section = ""
        self.checks: list[dict[str, Any]] = []
        self.current_tick: int | None = None
        self.openapi: dict[str, Any] = {}
        self.topology: dict[str, dict[str, dict[str, Any]]] = {}
        self.manifest: dict[str, Any] = {"instance": {}, "findings": {}, "timings": {}}
        self._steps = 0

    async def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        section: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        volatile: bool = False,
    ) -> Exchange:
        started = time.perf_counter()
        resp = await self.client.request(method, path, params=params, json=json)
        elapsed_ms = (time.perf_counter() - started) * 1000
        body, body_text = _decode(resp)
        headers: dict[str, str | None] = {
            k.lower(): v for k, v in resp.headers.items() if k.lower() not in DROPPED_HEADERS
        }
        headers.setdefault("x-simulator-stale", None)
        ex = Exchange(
            name=name,
            section=section or self.section,
            method=method,
            path=path,
            query=dict(params or {}),
            req_body=json,
            status=resp.status_code,
            headers=headers,
            body=body,
            body_text=body_text,
            elapsed_ms=elapsed_ms,
            sim_tick=self.current_tick,
            volatile=volatile,
        )
        self.writer.write(ex)
        return ex

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": {detail}"))
        return bool(ok)

    def expect(self, ex: Exchange, status: int | tuple[int, ...], code: str | None = None) -> bool:
        """Check an exchange's status (and envelope code); the check is named after the fixture."""
        wanted = status if isinstance(status, tuple) else (status,)
        got_code = envelope_code(ex.body)
        ok = ex.status in wanted and (code is None or got_code == code)
        detail = f"want {wanted} {code or ''} got {ex.status} {got_code or ''}".strip()
        return self.check(ex.name, ok, detail)

    async def step(self, n: int) -> int:
        """Advance ``n`` ticks via /admin/step, recording each as ``admin_step_NN``."""
        for _ in range(n):
            self._steps += 1
            ex = await self.call(f"admin_step_{self._steps:02d}", "POST", "/admin/step")
            if ex.status != 200 or not isinstance(ex.body, dict):
                raise SmokeAbort(f"/admin/step returned {ex.status}")
            self.current_tick = int(ex.body["tick"])
        return self.current_tick if self.current_tick is not None else -1


async def section_preflight(ctx: SmokeContext) -> None:
    deadline = time.monotonic() + HEALTH_WAIT_S
    while True:
        try:
            if (await ctx.client.get("/v1/health")).status_code == 200:
                break
        except httpx.TransportError:
            pass
        if time.monotonic() > deadline:
            ctx.check("health_ready", False, f"no 200 from /v1/health within {HEALTH_WAIT_S:.0f} s")
            raise SmokeAbort("simulator not reachable")
        await asyncio.sleep(0.5)

    health = await ctx.call("health", "GET", "/v1/health")
    ctx.check(
        "health",
        health.status == 200 and isinstance(health.body, dict) and health.body.get("status") == "ok",
        f"got {health.status} {health.body}",
    )

    spec = await ctx.call("openapi", "GET", "/openapi.json")
    paths = spec.body.get("paths", {}) if isinstance(spec.body, dict) else {}
    has_admin = "/admin/events" in paths and "/admin/faults" in paths
    ctx.check("openapi", spec.status == 200 and has_admin, "admin schemas absent (plan Decision 3)")
    if not has_admin:
        raise SmokeAbort("openapi.json lacks admin paths — stop and ask")
    ctx.openapi = spec.body

    ctx.expect(await ctx.call("admin_faults_clear_start", "POST", "/admin/faults/clear"), 200)
    ctx.expect(await ctx.call("admin_reset", "POST", "/admin/reset"), 200)
    ctx.expect(await ctx.call("admin_pause", "POST", "/admin/pause"), 200)

    inst = await ctx.call("instance_initial", "GET", "/v1/instance")
    body = inst.body if isinstance(inst.body, dict) else {}
    ctx.check(
        "instance_initial",
        inst.status == 200 and body.get("tick") == 0 and body.get("status") == "PAUSED",
        f"got {inst.status} {body}",
    )
    ctx.current_tick = body.get("tick")
    ctx.manifest["instance"] = {
        k: body.get(k) for k in ("seed", "scenario_id", "scenario_version", "tick_minutes")
    }


Section = Callable[[SmokeContext], Awaitable[None]]
SECTIONS: list[tuple[str, Section]] = [
    ("preflight", section_preflight),
]


async def _cleanup(client: httpx.AsyncClient) -> None:
    """Never leave a fault active or the clock running, whatever happened."""
    for path in ("/admin/faults/clear", "/admin/pause"):
        try:
            await client.post(path)
        except httpx.HTTPError as exc:
            print(f"  WARN  cleanup {path} failed: {exc}", file=sys.stderr)


def _write_manifest(ctx: SmokeContext, out_dir: Path, meta: Mapping[str, Any]) -> dict[str, Any]:
    manifest = {
        "fixture_version": FIXTURE_VERSION,
        "provenance": PROVENANCE,
        **meta,
        **ctx.manifest,
        "fixtures": ctx.writer.index,
        "checks": ctx.checks,
        "all_passed": bool(ctx.checks) and all(c["ok"] for c in ctx.checks),
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return manifest


async def run_smoke(
    client: httpx.AsyncClient,
    out_dir: Path,
    only: set[str] | None,
    meta: Mapping[str, Any] | None = None,
) -> int:
    """Run the selected sections in order; returns the process exit code."""
    ctx = SmokeContext(client, FixtureWriter(out_dir))
    sections_run: list[str] = []
    try:
        for name, section in SECTIONS:
            if only and name not in only:
                continue
            ctx.section = name
            sections_run.append(name)
            print(f"[{name}]")
            try:
                await section(ctx)
            except Exception as exc:  # noqa: BLE001 - any failure aborts the run, cleanup still runs
                ctx.check(f"section_{name}_completed", False, f"{type(exc).__name__}: {exc}")
                break
    finally:
        await _cleanup(client)
        ctx.manifest["sections_run"] = sections_run
        manifest = _write_manifest(ctx, out_dir, meta or {})
    return 0 if manifest["all_passed"] else 1


def _run_quiet(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--section", action="append", dest="sections", metavar="NAME")
    args = parser.parse_args(argv)

    meta = {
        "image": IMAGE,
        "image_digest": _run_quiet(
            ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", IMAGE]
        ),
        "git_sha": _run_quiet(["git", "rev-parse", "HEAD"]),
        "base_url": args.base_url,
    }

    async def _run() -> int:
        timeout = httpx.Timeout(10.0, connect=2.0)
        async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
            return await run_smoke(client, args.out, set(args.sections or ()), meta)

    code = asyncio.run(_run())
    manifest = json.loads((args.out / "manifest.json").read_text())
    passed = sum(c["ok"] for c in manifest["checks"])
    print(f"CONTRACT SMOKE: {passed}/{len(manifest['checks'])} checks passed")
    return code


if __name__ == "__main__":
    sys.exit(main())
