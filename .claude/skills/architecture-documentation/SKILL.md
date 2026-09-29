---
name: architecture-documentation
description: How to capture and maintain architecture knowledge — ADRs, architecture overview, and diagrams — so decisions and constraints survive across sessions. Use before/after significant design decisions.
---

# Architecture Documentation

Prevents context loss: decisions, constraints, and APIs live in files, not in chat history.

## Artifacts
- `docs/ARCHITECTURE.md` — living overview: components, data flow, boundaries, the diagram, key constraints.
  Update it whenever structure changes.
- `docs/adr/NNNN-title.md` — **one ADR per significant decision**. Format:
  `Context → Decision → Status → Consequences → Alternatives considered`. ADRs are append-only; supersede, don't delete.
- `docs/plans/` — task plans written before non-trivial work (Superpowers `writing-plans`).
- Diagrams: Mermaid in Markdown (renders in GitHub), kept next to the prose they explain.

## When to write an ADR
Choosing a datastore, a resilience pattern, the optimization approach (MILP vs heuristic), the ML model class,
sync vs async boundaries, monolith vs separate worker — anything that constrains future work.

## Rules
- Keep docs terse and current; a stale doc is worse than none. Prune obsolete detail.
- Before implementing an architectural change, confirm it against `ARCHITECTURE.md`; after, update it + add the ADR.
- Reference exact library/API facts from **Context7** rather than freezing possibly-outdated snippets into docs.
