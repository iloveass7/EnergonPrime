---
name: decision-optimization
description: Decision intelligence and allocation optimization rules — turning forecasts + constraints into explainable recommendations. Use for allocation, scheduling, routing, or any optimize/policy work.
---

# Decision Optimization

The core value: `prediction → constraints → optimization → recommendation`. ML predicts; **optimization decides**.
Do not let a model output allocations directly.

## Mandatory workflow
`objective → constraints → baseline → optimize → evaluate`.

## Formulation
- Start with a clear **objective** (e.g. minimize expected unmet demand + transport cost + shortage risk penalty).
- Enumerate **hard constraints**: depot capacity, supply arrivals, route/vehicle limits, station demand,
  conservation of fuel, non-negativity, time windows.
- Model as **LP/MILP with OR-Tools** (or a documented heuristic if MILP is too slow for the tick budget).
  Use a greedy/proportional baseline to measure the optimizer's lift.
- **Uncertainty-aware**: feed forecast intervals in — optimize expected value with a shortage-risk penalty,
  or a small robust/scenario set. Don't optimize against point forecasts alone.
- **Multi-objective**: make weights explicit and configurable; expose the trade-off, don't hardcode.

## Explainability (required)
Every recommendation returns: inputs used, binding constraints, chosen allocation, objective value,
and a plain-language "why" + the runner-up alternative. Log all of it to the decision audit (see `observability`).

## Safety
- Validate the optimizer's output against constraints before proposing it; never emit an infeasible plan.
- If the solver fails/times out → fall back to the baseline policy and flag degraded decision quality.
- Support **counterfactual replay**: given a past state, recompute what the decision would have been.
