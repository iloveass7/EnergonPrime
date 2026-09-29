---
name: deployment
description: Containerization, CI/CD, config, and experiment/model versioning rules for the fuel platform. Use for Docker, docker-compose, GitHub Actions, environment config, or model registry work.
---

# Deployment

Keep it simple: **Docker + docker-compose + GitHub Actions**. No Kubernetes/Terraform unless a real need appears.

## Containers
- Multi-stage Dockerfiles; slim runtime images; run as non-root; pin base image digests.
- `docker-compose.yml` brings up: backend, Postgres, Redis, (optional) simulator, Prometheus+Grafana.
- **Health checks** in compose; **graceful shutdown** (handle SIGTERM, drain in-flight, close pools).

## Config & secrets
- 12-factor: all config via env / `BaseSettings`. `.env` gitignored; commit `.env.example`.
- Never bake secrets into images or commit them. CI secrets via GitHub Actions secrets.

## CI/CD (GitHub Actions)
- Pipeline: lint (ruff) → type-check → unit+integration tests → build image → (optional) push.
- Cache deps; fail the build on test/lint failure. Keep it fast.
- Provide a rollback path: tagged images so you can redeploy the previous tag.

## Experiment / model versioning
- Track experiments with **MLflow** (or a lightweight local run-log if MLflow is overkill for the timeline):
  params, metrics, data-snapshot hash, artifact. Register promoted models with a version + metrics.
- Reproducible training: pinned deps, fixed seed, recorded data snapshot. Inference loads a specific model version.

## Definition of done
`docker compose up` yields a healthy stack; CI is green; models are versioned and reloadable.
