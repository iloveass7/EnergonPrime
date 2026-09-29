"""FastAPI app (api role, xN). Reads come from Redis; commands go through the ACL."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from fuelops.api.deps import AppState, problem
from fuelops.api.routers import commands, reads, system, test_plane
from fuelops.config import get_settings
from fuelops.observability import metrics as m
from fuelops.observability.logging import configure_logging, log


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    fo = AppState(settings)
    try:
        await fo.db.init()
    except Exception as exc:  # noqa: BLE001 - reads still work from Redis
        log.warning("db_init_failed", error=str(exc))
    app.state.fo = fo
    log.info(
        "api_start",
        test_plane=settings.test_plane_enabled,
        auth=settings.effective_auth_mode,
    )
    yield
    await fo.client.aclose()
    if fo.admin is not None:
        await fo.admin.aclose()
    await fo.store.close()
    await fo.db.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="FuelOps API",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "x-api-key"],
    )

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        m.HTTP_INFLIGHT.inc()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:  # noqa: BLE001
            log.exception("unhandled", path=request.url.path, error=str(exc))
            return problem(500, "INTERNAL", "unexpected error")
        finally:
            m.HTTP_INFLIGHT.dec()
            route = request.scope.get("route")
            path = getattr(route, "path", "unmatched")
            elapsed = time.perf_counter() - started
            m.HTTP_REQUESTS.labels(path, request.method, str(status)).inc()
            m.HTTP_SECONDS.labels(path).observe(elapsed)
            if path not in ("/metrics", "/api/v1/stream"):
                request.app.state.fo.latencies.append((time.time(), elapsed, status >= 500))

    app.include_router(system.router)
    app.include_router(reads.router)
    app.include_router(commands.router)
    if settings.test_plane_enabled:
        app.include_router(test_plane.router)
    return app


app = create_app()
