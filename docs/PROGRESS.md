# Progress

Phases from `docs/pipeline.md` §14. Each phase is ticked only when its "done when" is proven, with evidence.

| | Phase | Done when | Status | Evidence |
|---|---|---|---|---|
| [x] | **0 Contract recon** | contract smoke passes from one script | done 2026-09-29 (101/101; review findings fixed) | `evidence/phase-0/contract-smoke-20260929T054056Z.json`, `…T054113Z.json` (determinism), `docs/contract-findings.md` |
| [x] | 1 Backend slice | dashboard reflects live state with `meta` and age | done 2026-09-29 | `/api/v1/dashboard` meta.age_s; `docs/contract-findings.md` |
| [x] | 2 Operator shell | Playwright smoke green | done 2026-09-29 (2/2, 3 runs) | `frontend/e2e/smoke.spec.ts` |
| [~] | 3 Sync & lifecycle | scenario I + reset test pass | reset verified (e2e `reset_detected`, epoch++); SSE reconnect + resync implemented, scenario I (forced stream drop) not yet scripted — `stream_disconnect` leaves an open stream connected | `fuelops/worker/main.py` |
| [~] | 4 Intelligence | property tests + evaluation artifact (WAPE, coverage) | profile WAPE 5.1% live; coverage + rolling-origin artifact pending | `/api/v1/intelligence` |
| [x] | 5 Decide & act | risk → approve → ARRIVED with one allocation; duplicate test passes | done 2026-09-29 (12/12) | `scripts/e2e_smoke.py` |
| [~] | 6 Crisis & resilience | scenarios B–K pass | stale/unavailable/reset/SSE verified live; scripted B–K suite pending | test-plane scenarios in UI |
| [~] | 7 Platform | clean-machine `make up` + `ci-success` green | `make up` healthy locally (5/5 containers); CI written, not yet run on GitHub | `docker-compose.yml`, `.github/workflows/ci.yml` |
| [~] | 8 Performance & security | load and security reports with actuals | load done (normal + stress); spike/soak + security scan pending | `evidence/phase-8/README.md` |
| [ ] | 9 Competitive extras | each beats or matches the baseline, else disabled | not started | |
| [ ] | 10 Demo freeze | two consecutive clean timed runs | not started | |

## Phase 0 — how to reproduce

```bash
docker compose down -v && docker compose up -d
uv run python scripts/contract_smoke.py
uv run pytest tests/unit tests/contract -q
# determinism: restart clean again, then re-run into a scratch dir and diff
docker compose down -v && docker compose up -d
uv run python scripts/contract_smoke.py --out /tmp/p0-rerun --compare-to tests/fixtures/contract/sim-1.0.0
```

## Resolved

- Admin event/fault `parameters` keys: now documented in `docs/simulator-guide.md` §7.8/§7.10 and exercised by the smoke. The image contradicts the guide on empty filters (they affect nothing), so crisis injection must name explicit ids; see `docs/contract-findings.md`.
- `docs/simulator-guide.md` and `docs/hackathon-brief.md` added; CLAUDE.md precedence updated.
- Phase 0 code review (high, 2026-09-29): the six correctness findings and the dead-state cleanup are fixed with regression tests (`tests/unit/test_contract_smoke_robustness.py`, CLI guards). Deferred as minor: incremental SSE parsing, and `probe_stream` building its own `Exchange`.

## Next

- Phase 4: rolling-origin evaluation artifact (WAPE by horizon, interval coverage) + property tests.
- Phase 6: scripted scenario suite B–K (`scripts/run_scenarios.py`) with evidence per scenario.
- Phase 7: push to GitHub and get `ci-success` green; set `DATABASE_URL` to Neon.
- Phase 8: k6 spike/soak, fault-under-load, security scan (pip-audit, npm audit).
