# Backend image: one image, two roles (api | worker). Non-root, pinned lockfile.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY fuelops ./fuelops

FROM python:3.12-slim
RUN groupadd -g 10001 app && useradd -u 10001 -g app -m app \
 && apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/fuelops /app/fuelops
COPY docker/entrypoint.sh /entrypoint.sh
RUN mkdir -p /app/data && chown -R app:app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
EXPOSE 8080 9101
HEALTHCHECK --interval=10s --timeout=3s --retries=6 CMD curl -fs http://localhost:8080/healthz || curl -fs http://localhost:9101/metrics >/dev/null || exit 1
ENTRYPOINT ["/entrypoint.sh"]
CMD ["api"]
