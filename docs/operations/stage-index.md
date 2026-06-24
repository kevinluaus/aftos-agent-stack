# Stage index — build history and reference

The system was built across nine stages. This note maps each stage to its canonical document and summarises what was built. Drift logs are replication archive — open them only when re-executing that stage.

## Stage map

| Stage | Status | What was built | Canonical doc |
|---|---|---|---|
| **0** | FINAL | Accounts (Anthropic, Tavily, Telegram), 3 blueprints gap-checked, GitHub App + 3 orgs + broker (GH.1–9) | `docs/stages/stage-0.md` |
| **1** | CLOSED | Host setup, Postgres 16 + pgvector, schema, daily backups via `op run` | `docs/stages/stage-1.md` |
| **2** | CLOSED | NemoClaw v0.0.44 install, first workspace onboard, operator surfaces | `docs/stages/stage-2.md` |
| **3** | CLOSED | Heartbeat cost-cut, persistence net, egress gate, SOUL.md | `docs/stages/stage-3.md` |
| **3.5** | DONE | LiteLLM proxy, Langfuse v3, Path C inference (sandbox→host), Wave 1 bake (all 3 workspaces) | `docs/stage-detail/stage-3_5.md` |
| **4** | CLOSED | Three workspaces replicated and isolated | `docs/stages/stage-4.md` |
| **5** | DONE | filesystem MCP, Tavily MCP, GitHub MCP, Obsidian MCP, BLUEPRINT.md — all 3 workspaces | `docs/stages/stage-5.md` + `docs/stage-detail/stage-5-drift-log.md` |
| **6** | DONE | BGE-M3 embeddings, Ollama concurrency tuning (NUM_PARALLEL=4, CTX=16384), speed baseline | `docs/stages/stage-6.md` |
| **7** | DONE | Memory layer, specialist framework, tiered coder, ticket system — all 3 workspaces | `docs/stages/stage-7.md` |
| **8** | DONE | Grafana, Prometheus, exporters, 9 alert rules, n8n, logger MCP, session reset | `docs/stages/stage-8.md` |
| **9** | LIVE | Autonomous coder loop, escalation ladder, review gate — first workspace active | `docs/stages/stage-9.md` |

## Where to look for what

| Question | Look here |
|---|---|
| Current status of everything | `docs/constants/MASTER_INDEX.md` §1 |
| Cross-stage operational truths (the scar tissue) | `docs/constants/LEARNINGS.md` |
| What the last session did, what's next | `docs/constants/MEMORY.md` (carry-forward block) |
| The autonomous coder loop (end-to-end) | `docs/PROCESS-MAP.md` |
| GitHub PAT migration (authoritative) | `docs/guides/RUNBOOK-github-PAT-3-workspaces-CORRECTED-2026-06-12.md` |

## Key design decisions captured in LEARNINGS.md

- **F12** — command context split (config commands vs gateway-routed commands)
- **F25** — filesystem MCP must be node-direct (npx fails the 30s handshake)
- **F32** — GitHub PAT vs App token (why PATs retired the rotation problem)
- **F47** — memory-mcp must be host-side HTTP (not in-sandbox stdio)
- **F76** — Sonnet token cost is 3–4× Qwen; caching strategy is mandatory before production load
- **F79** — obsidian MCP via supergateway (stdio→HTTP bridge); vault on host, not in sandbox
- **F89** — autonomous coder execution loop: thin dispatcher + daemon build thread + two liveness layers
- **F105** — Llama 3.1:8b as orchestrator saves ~$9.18/week vs Sonnet

Full ledger: `docs/constants/LEARNINGS.md` (F1–F109 as of 2026-06-22)
