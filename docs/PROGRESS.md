# Progress

Phases from `docs/pipeline.md` §14. Each phase is ticked only when its "done when" is proven, with evidence.

| | Phase | Done when | Status | Evidence |
|---|---|---|---|---|
| [ ] | **0 Contract recon** | contract smoke passes from one script | verified; awaiting code review | `evidence/phase-0/contract-smoke-20260929T052805Z.json`, `…T052823Z.json` (determinism), `docs/contract-findings.md` |
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
```

## Open questions

- Admin event/fault `parameters` keys (e.g. which routes a `route_disruption` targets) are not documented in `docs/`; `{}` affects no entity. Needed before Phase 6 scenarios. See `docs/contract-findings.md`.
- `CLAUDE.md` names `docs/simulator-guide.md` as the top source of truth; that file does not exist.
