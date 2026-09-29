# Claude Code Environment Setup — BUP Fuel Supply Operations Platform

_Prepared for Shila · 2026-09-29 · executed on your Mac (Remote Control session), project root `~/Desktop/bup-fuel-platform`._

The goal was a **small, high-quality, well-integrated** environment — not a pile of tools. What follows is
what I found, what I installed and why, what I deliberately did **not** install, and the workflows to run it with.

---

## 1. Audit — what was already there

| Item | State |
|---|---|
| Claude Code | **v2.1.126** |
| Node / npm | v22.17.1 / 10.9.2 |
| Python | 3.14.7 · **uv / uvx** present |
| Git | present · **`gh` NOT installed** · **Docker NOT installed** |
| `~/.claude/settings.json` | did not exist (none created globally; project-scoped settings added instead) |
| User skills (`~/.claude/skills`) | none |
| Installed plugins | none |
| MCP servers | **none configured** |
| Marketplaces | `claude-plugins-official` only (updated during setup) |

No existing config was overwritten. `~/.claude.json` is auto-backed-up by Claude Code (5 timestamped backups in
`~/.claude/backups/`); MCP additions were appends, not rewrites.

---

## 2. The five capabilities you named — verdicts

| Requested | What it actually is | Verdict |
|---|---|---|
| **superpowers** | Real plugin by *obra* (`obra/superpowers-marketplace`). Adds a senior-engineer workflow: brainstorming, writing/executing plans, TDD, systematic debugging, code-review subagents, verification-before-completion, parallel-agent dispatch. | **INSTALLED** (v6.4.2). This is the workflow spine you asked for. |
| **Context7** | Upstash MCP server for **up-to-date, version-specific library docs** pulled into context on demand. Hosted HTTP endpoint, works anonymously. | **INSTALLED** (HTTP, no key). Highest-value MCP here. |
| **context window / context management** | **Not a package** — a strategy. There is no legitimate single "context window" install. | Delivered as a **strategy** (§6) using CLAUDE.md hierarchy + Superpowers session context + Serena + Context7. |
| **omni route** (OmniRoute) | A third-party AI **gateway** that routes prompts to 350+ external model providers using your own API keys. | **NOT installed** — explained in §5. It routes your code to non-Anthropic providers, needs keys you haven't supplied, and adds real security/complexity. Your routing intent is met natively (§5, §8). |
| **caveman context** | A skill that makes Claude answer in ultra-terse "caveman" style to cut tokens ~75%. | **NOT installed** — explained in §5. It directly fights the detailed architecture explanations you asked for. |

---

## 3. Installed — MCP servers (user scope, all verified ✓ Connected)

| MCP | Transport | Credentials | Why |
|---|---|---|---|
| **context7** | HTTP `https://mcp.context7.com/mcp` | none (anonymous; optional `CONTEXT7_API_KEY` for higher limits) | Current FastAPI / Pydantic / OR-Tools / React / etc. docs on demand — stops API-signature guessing. |
| **playwright** | stdio `npx @playwright/mcp` | none | Drive/test/debug the operator dashboard; E2E. |
| **serena** | stdio `uvx … oraios/serena` | none | Semantic **codebase indexing & navigation** (LSP-based) for a large project — your "codebase indexing" requirement. Activates per-project. |

Verify anytime: `claude mcp list`.

## 4. Installed — plugin

- **superpowers@superpowers-marketplace** (user scope, enabled). Gives `/brainstorm`, `/write-plan`,
  `/execute-plan`, skills-search, and auto-activating engineering-discipline skills.

---

## 5. Deliberately NOT installed (and why)

| Item | Why not | Instead |
|---|---|---|
| **OmniRoute / omni route** | External multi-provider gateway; sends code to third-party providers, needs API keys you didn't supply, adds a failure point and security surface. | Native model selection in Claude Code + subagents for "cheap model does grunt work" (§8). Revisit only if you deliberately want non-Anthropic models. |
| **caveman context** | Ultra-terse output mode; degrades the explainable architecture/ML reasoning this project needs. | Keep normal output; use context strategy (§6) for token economy. |
| **GitHub MCP** | Needs `GITHUB_PERSONAL_ACCESS_TOKEN` (not supplied) and `gh` isn't installed; no repo attached yet. | **Ask me to add it once you have a token** — one command. |
| **PostgreSQL / Redis / Docker MCPs** | No DB/Redis running, Docker not installed, no creds. Installing now = dead config. | Add when the stack is up (Docker Compose first). |
| **MLflow / Prometheus / Grafana MCPs** | No mature, reputable, low-risk Claude Code MCPs worth it now; would add context bloat. | Use them as **services** (compose) + skills; instrument via code, not an MCP. |
| **Kubernetes / Kafka / Terraform / service mesh** | Overkill for a deterministic single-simulator hackathon system. | Modular monolith + Docker Compose (ADR-0001). |

Nothing fabricated: every installed item was verified against its official source and its live connection/enable state.

---

## 6. Context strategy (prevents losing decisions/APIs/history)

A 6-layer hierarchy, each in files so nothing depends on chat scrollback:

```
GLOBAL        Claude Code global rules + installed skills/MCPs
  ↓
PROJECT       CLAUDE.md  (goal, stack, guardrails, workflow) — always loaded
  ↓
ARCHITECTURE  docs/ARCHITECTURE.md + docs/adr/*  (decisions & constraints, append-only)
  ↓
TASK          docs/plans/*  (plan written before non-trivial work)
  ↓
CODE          the files in play — navigate via Serena (symbols), don't re-read whole trees
  ↓
LIBRARY DOCS  pulled on demand via Context7 (never frozen/guessed)
```

Rule against pollution: long-lived facts live in layers 2–4; transient detail stays out of them.
**Retrieve, don't memorize.** Superpowers injects project context at session start; Serena keeps code navigation
cheap; Context7 keeps docs fresh without bloating the base prompt.

---

## 7. Project skills created (`.claude/skills/`, auto-discovered in this project)

Eleven project-specific skills — rules, not tutorials:
`simulator-integration`, `backend-architecture`, `ml-forecasting`, `decision-optimization`,
`resilience-engineering`, `observability`, `testing`, `load-testing`, `deployment`,
`operator-dashboard`, `architecture-documentation`.

Example (`simulator-integration`): REST is truth / SSE advisory · refetch after events · never assume an
allocation succeeded · handle 409 & 503(fault) explicitly · idempotency keys · SSE reconnect+resync ·
validate route↔depot↔station.

Also seeded: `docs/ARCHITECTURE.md`, `docs/adr/0001-modular-monolith-and-stack.md`, and `CLAUDE.md`.

---

## 8–13. Recommended workflows

**Claude workflow (per change):** understand → inspect code → pull docs (Context7) → plan + risks → implement
→ test → self-review → integration test → verify → update docs → coherent commit. Never "ask → 500 lines."
Architecture change → plan first (`/brainstorm`, `/write-plan`). Bug → reproduce first.

**Architecture:** confirm against `ARCHITECTURE.md`; significant decision → write an ADR; keep the modular
monolith until profiling proves otherwise; explain trade-offs before adding complexity.

**ML:** `data → baseline → experiment → metric → comparison → decision`. Time-ordered splits (no leakage),
rolling CV, prediction intervals + calibration, graceful fallback to baseline, versioned models.

**Optimization:** `objective → constraints → baseline → optimize (OR-Tools) → evaluate`. Uncertainty-aware,
multi-objective weights explicit, always feasible, explainable + audited, baseline lift measured.

**Testing:** unit · contract · integration · fault-injection (drive `/admin/faults`) · deterministic replay
· property-based (Hypothesis) · E2E (Playwright). Bug fix = failing test first.

**Model routing (your "omni route" intent, done natively):** strong model for architecture/ML reasoning;
delegate implementation/grunt transforms to subagents (Superpowers dispatches parallel agents). No external gateway.

**Observability:** instrument as you build — structured logs w/ correlation IDs, Prometheus `/metrics`
(latency, error rate, simulator availability, model latency/confidence, forecast error, fallback activation,
decision frequency, allocation failures), OTel traces, `/health` live+ready, durable decision audit in Postgres.

---

## Credentials / env — what you'd supply later (nothing required now)
- `CONTEXT7_API_KEY` — *optional*, only for higher Context7 rate limits.
- `GITHUB_PERSONAL_ACCESS_TOKEN` — needed only if/when you want the GitHub MCP (+ `brew install gh`).
- Docker Desktop — needed for the compose stack, Postgres/Redis, and load-test targets.

## Config files touched
- `~/.claude.json` — appended 3 MCP servers (user scope). Auto-backed-up.
- `~/.claude/plugins/config` — Superpowers marketplace + plugin registered (user scope).
- **New**, under `~/Desktop/bup-fuel-platform/`: `CLAUDE.md`, `.claude/settings.json`, 11 `.claude/skills/*`,
  `docs/ARCHITECTURE.md`, `docs/adr/0001-*`, this report. No unrelated files modified.
