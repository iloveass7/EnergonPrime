.PHONY: up down logs test lint smoke contract load e2e dev-api dev-worker dev-web

up:            ## full stack (simulator, redis, api, worker, web)
	docker compose up -d --build
	@echo "UI http://localhost:3000  API http://localhost:8080  Simulator http://localhost:8000/admin"

up-obs:        ## + Prometheus (9090) and Grafana (3001)
	docker compose --profile observability up -d --build

down:
	docker compose --profile observability down -v

logs:
	docker compose logs -f api worker

lint:
	uv run ruff check fuelops scripts tests && uv run ruff format --check fuelops scripts tests
	uv run mypy fuelops scripts && uv run lint-imports
	cd frontend && npx tsc -p tsconfig.json

test:
	uv run pytest tests -q

contract:      ## Phase 0 contract smoke against a clean simulator
	uv run python scripts/contract_smoke.py --out /tmp/p0-contract

smoke:         ## end-to-end smoke against the running stack
	uv run python scripts/e2e_smoke.py

load:          ## k6 normal profile against the API
	k6 run -e BASE_URL=$${BASE_URL:-http://localhost:8080} load/k6_api.js

dev-api:
	uv run uvicorn fuelops.api.main:app --port 8080 --reload
dev-worker:
	uv run python -m fuelops.worker.main
dev-web:
	cd frontend && npm run dev
