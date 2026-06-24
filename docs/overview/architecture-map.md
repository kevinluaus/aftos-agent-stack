# Architecture map

All layers and how they connect. Read this alongside [[overview/what-is-this]].

## Layer diagram

```
┌─────────────────────────────────────────────────────────────┐
│  HUMAN INTERFACE                                            │
│  Telegram (review approvals, escalation alerts, status)     │
│  Claude Code CLI (planning, PR review — flat-rate sub)      │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│  ORCHESTRATION LAYER (per workspace)                        │
│  NemoClaw sandbox → OpenClaw agent (SOUL.md)                │
│  N isolated Docker containers (one per workspace/product).  │
│  Each has its own SOUL, Telegram bot, and GitHub org.       │
└─────────────────────────┬───────────────────────────────────┘
                          │ MCP tools
┌─────────────────────────▼───────────────────────────────────┐
│  TOOLS LAYER (MCP servers — host-side)                      │
│  filesystem  │ tavily  │ github  │ obsidian  │ tickets/memory│
│  (per ws)    │ (search)│ (PRs)   │ (vault)   │ (state + RAG) │
└──────────────┬──────────────────────────────────────────────┘
               │ code() tool → execute_ticket()
┌──────────────▼──────────────────────────────────────────────┐
│  BUILD LAYER (host — memory-mcp background thread)          │
│  Coder: context → LLM generation → write files → git push   │
│  Escalation ladder: Qwen×2 → Qwen+think → Sonnet → Opus    │
└──────────────┬──────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│  INFERENCE LAYER                                            │
│  LiteLLM proxy :4000 — single routing interface             │
│  Local (Ollama):  Qwen 3.6-27B, Llama 3.1:8b, BGE-M3      │
│  Cloud (Anthropic): Sonnet 4.6, Opus 4.8, Haiku 4.5        │
└──────────────┬──────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│  STATE + MEMORY LAYER                                       │
│  Postgres 16 + pgvector: tickets, logs, coder_calls,        │
│  memory_episodic, memory_semantic (1024-dim BGE-M3 vecs)    │
└──────────────┬──────────────────────────────────────────────┘
               │
┌──────────────▼──────────────────────────────────────────────┐
│  OBSERVABILITY LAYER                                        │
│  Langfuse v3 (LLM traces + cost, ClickHouse-backed)         │
│  Grafana :3001 (dashboards from Postgres + ClickHouse)      │
│  Prometheus + node_exporter + postgres_exporter             │
│  n8n :5678 (processor loop + escalation workflows)          │
└─────────────────────────────────────────────────────────────┘
```

## Key data flows

**Ticket execution flow (the core loop):**
1. n8n fires every 5 min → checks for runnable tickets → sends `[EXECUTE NEXT]` via Telegram to the workspace bot
2. Orchestrator (in-sandbox) claims a ticket (`claim_next_ticket`) → calls `execute_ticket(id)` → exits immediately
3. `execute_ticket` spawns a background build thread on the host: reads context → calls the coder → writes files → pushes to GitHub → opens PR → sets ticket to `review`
4. Grafana alert fires → Telegram ping → human runs `/review-pending` in Claude Code → merge or reject with feedback
5. Merge sets ticket `done`, unblocking any dependent tickets → next wave fires

**Memory flow:**
- Obsidian vault (per workspace, on host) contains PR review notes, decisions, daily logs written by the orchestrator
- BGE-M3 nightly cron (04:00) embeds all vault `.md` files → stored in `memory_semantic` (pgvector)
- `memory_search` tool makes this retrievable semantically during planning and execution

**Cost flow:**
- Every LLM call flows through LiteLLM → traces land in Langfuse (ClickHouse)
- Grafana dashboards surface per-workspace spend, model distribution, cache hit rates
- Escalation gate: `attempts >= 6` = terminal `failed`, stops token burn

## Port map (host)

| Service | Port | Notes |
|---|---|---|
| Gateway (WebSocket) | 8081 | OpenShell, per-workspace sandboxes |
| LiteLLM | 4000 | `0.0.0.0` — sandboxes reach via `172.18.0.1:4000` |
| Langfuse web | 3000 | `127.0.0.1` only, SSH tunnel |
| Grafana | 3001 | Dashboards |
| n8n | 5678 | Workflow automation |
| Postgres | 5432 | System DB (agent state) |
| Ollama | 11434 | Local model serving |
| GitHub MCP (per ws) | 8077+ (one per workspace) | Per-workspace GitHub API server |
| memory-mcp (per ws) | 8088+ (one per workspace) | Tickets + RAG tools |
| obsidian-mcp (per ws) | 8093+ (one per workspace) | Vault read/write |
| memory-mcp admin | MCP port + 100 | Session reset endpoint |

→ See [[layers/orchestration]] for sandbox detail.  
→ See [[layers/tools]] for MCP server detail.  
→ See [[layers/coder]] for the build loop detail.
