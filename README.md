# aftos agent-stack

![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Platform: NVIDIA GB10](https://img.shields.io/badge/Platform-NVIDIA%20GB10-76b900)
![Models: Ollama + Anthropic](https://img.shields.io/badge/Models-Ollama%20%2B%20Anthropic-blue)

Self-hosted AI agent infrastructure: multiple autonomous agents running concurrently on local hardware, writing production code and opening pull requests with a 30 min/day human review loop.

---

## What I built

I built a self-hosted AI agent infrastructure that autonomously helps me bring ideas to life on a machine sitting on my desk, while I go about my day.

Multiple agents run concurrently on a tiny AI computer, the kind of hardware that didn't exist for individuals a year ago. Each agent is assigned to a different software product, works through a development backlog without supervision, writes production code, opens pull requests on GitHub, and learns from reviewer feedback. When it hits a problem it can't solve, it escalates. My involvement is roughly thirty minutes a day: read the Telegram ping, open a PR, approve or reject with a note. The agents handle the rest.

What makes this genuinely hard to build is not the hardware or the models — it's the engineering beneath them: credential protection, sandboxed execution, LLM switchability, observability, and remote control. The LLM routing layer sits behind a single interface so switching a workspace from local models to a cloud API is a one-line edit, not a rebuild. The observability stack captures every LLM call: costs, token breakdown, cache hit rate, model used — all in a self-hosted Langfuse and Grafana instance. Every password is held in vault and provided when needed but no credential ever becomes visible to an agent. When errors occur and intervention is required, a Telegram alert fires and waits for feedback.

The agents write code I then review — not scaffolding needing additional work. The hard creative and strategic work stays with me. The implementation work runs continuously.

**Built on:** NVIDIA GB10 Grace Blackwell · NemoClaw · OpenClaw · Ollama · LiteLLM · Langfuse · Grafana · n8n · Postgres + pgvector · Anthropic Claude · 1Password · GitHub

---

## Architecture

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
               │ execute_ticket()
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

---

## What's in this repo

| Folder | Contents |
|---|---|
| `docs/` | Full architecture documentation — layers, design decisions, operations |
| `sql/` | Postgres schema (tickets, memory, logs, coder calls) |
| `memory-mcp/` | FastMCP server — semantic memory, episodic recall, ticket state machine, build dispatcher |
| `coder/` | Autonomous build loop — context assembly, LLM escalation, file writing, git push |
| `templates/` | LiteLLM config, SOUL template, env var template |
| `n8n/` | Workflow JSONs — processor loop, session reset, escalation gate, error handler |
| `grafana/` | Dashboard exports + alert rule definitions |

Start with `docs/overview/` for the plain-English system description and architecture map.

---

## Prerequisites

This is a reference architecture, not a one-click installer. Adapting it to your host requires real engineering. Here's what you need:

| Dependency | Notes |
|---|---|
| **NemoClaw + OpenClaw** | NVIDIA alpha agent runtime — the only hard dependency that's not publicly available. Requires an NVIDIA Build account; pinned at v0.0.44. Without it, there is no sandbox isolation and no agent runtime. |
| **NVIDIA GPU with Ollama support** | Runs on GB10 (128 GB unified memory). Any CUDA-capable machine with ≥32 GB VRAM works for the full model set. RTX 4090 (24 GB) works with smaller quantisations. |
| **Docker** | All sandboxes and most services run in Docker |
| **Postgres 16 + pgvector** | System DB — must be on the host for memory-mcp |
| **Anthropic API key** | Cloud escalation (Sonnet + Opus); the local tier runs free |
| **1Password** | The secrets model assumes 1Password + a service account token. Adaptable to any secret manager — the pattern (never a raw secret in config) is portable; the tool is swappable. |
| **Telegram** | One bot per workspace + one ops bot — the only supported trigger channel |
| **GitHub orgs** | One org per workspace — free GitHub accounts work |

### Three realistic paths

**Path A — read the docs** (no prerequisites)  
The design decisions, architecture patterns, and rationale are the most transferable part. No install needed.

**Path B — run the infrastructure** (Docker + GPU)  
Postgres, LiteLLM, Langfuse, Grafana, n8n, memory-mcp, and the coder loop are all portable. Compose these with your own agent runtime.

**Path C — replicate the full stack** (NemoClaw account required)  
The complete architecture as built. High setup complexity — see `docs/operations/` for the stage-by-stage build sequence.

---

## Key design decisions

- **Secrets never at rest** — every credential is injected via `op run` at process start; no token ever appears in config, env files, or agent context
- **Escalation ladder** — Qwen 3.6:27b (no-think) → Qwen (think) → Sonnet → Opus; cloud spend only when local models genuinely can't solve it
- **Idempotent builds** — every code generation attempt derives its branch name from the ticket ID; retries force-push to the same branch, updating the same PR
- **Workspace isolation** — each agent has its own sandbox, DB scope, GitHub org, and credential set; cross-workspace contamination is architecturally impossible
- **BGE-M3 semantic memory** — nightly vault indexing via pgvector; agents retrieve relevant past decisions at planning time, not just recent episodic memory

See `docs/design-decisions/` for full rationale on each.

---

## License

MIT — see [LICENSE](LICENSE).
