# Progress

Phases from `docs/pipeline.md` §14. Each phase is ticked only when its "done when" is proven, with evidence.

| | Phase | Done when | Status | Evidence |
|---|---|---|---|---|
| [x] | **0 Contract recon** | contract smoke passes from one script | done 2026-09-29 (101/101; review findings fixed) | `evidence/phase-0/contract-smoke-20260929T054056Z.json`, `…T054113Z.json` (determinism), `docs/contract-findings.md` |
| [ ] | 1 Backend slice | dashboard reflects live state with `meta` and age | not started | |
| [ ] | 2 Operator shell | Playwright smoke green | not started | |
| [ ] | 3 Sync & lifecycle | scenario I + reset test pass | not started | |
| [ ] | 4 Intelligence | property tests + evaluation artifact (WAPE, coverage) | not started | |
| [ ] | 5 Decide & act | risk → approve → ARRIVED with one allocation; duplicate test passes | not started | |
| [ ] | 6 Crisis & resilience | scenarios B–K pass | not started | |
| [ ] | 7 Platform | clean-machine `make up` + `ci-success` green | not started | |
| [ ] | 8 Performance & security | load and security reports with actuals | not started | |
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

Phase 1 (backend slice): config, ACL built on the typed errors and envelopes recorded in `tests/fixtures/contract/sim-1.0.0/`, worker hydration, Redis, `/api/v1/dashboard`, health, JSON logs, metrics.
