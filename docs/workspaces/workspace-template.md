# Workspace template

This file shows the structure of a workspace record in the aftos agent-stack. Each workspace
represents one software product being developed autonomously by an AI agent.

---

## What a workspace contains

| Item | Description |
|---|---|
| **SOUL.md** | The agent's identity, constraints, and operating context — loaded at startup |
| **BLUEPRINT.md** | Product strategy, roadmap, and success metrics — the agent's north star |
| **coder-repo/** | Local clone of the product codebase — where built code lands before being pushed |
| **obsidian-vault/** | Knowledge base — PR review notes, decisions, daily logs written by the orchestrator |

## Workspace identity (per workspace in config)

| Item | Value |
|---|---|
| Workspace ID | `ws-<name>` — used to scope all DB queries, MCP endpoints, and log entries |
| GitHub org | A dedicated org scoped to this workspace; the agent can only reach this org's repos |
| Telegram bot | One bot per workspace — review alerts and daily summaries route here |
| Memory MCP port | Unique port on `172.18.0.1` for this workspace's ticket + memory server |
| GitHub MCP port | Unique port on `172.18.0.1` for this workspace's GitHub API server |
| Obsidian MCP port | Unique port on `172.18.0.1` for this workspace's vault server |

## Adding a new workspace

The full procedure for standing up a new workspace is in `docs/stages/stage-4.md` (replication)
and `docs/stages/stage-5.md` (MCP tooling). Key steps:

1. `nemoclaw ws create <name>` — create the sandbox
2. Write `SOUL.md` and `BLUEPRINT.md` — define the agent's identity and product context
3. Register five MCP servers: filesystem, Tavily, GitHub, Obsidian, memory-mcp
4. Create GitHub org + PAT, inject via OpenShell provider
5. Create Telegram bot + route alerts via n8n processor
6. Add workspace row to Grafana dashboards

Each workspace is isolated by default: separate sandbox, separate DB scope, separate credential set.
No workspace can access another workspace's repos, files, or tokens.

## Why one workspace per product

Isolation is the design principle. A bug in one workspace's agent cannot corrupt another
workspace's codebase, secrets, or state. The shared layers (LiteLLM, Langfuse, Grafana, n8n)
are read-write for observability but not for execution — an agent cannot affect another agent's
tickets, memory, or build queue.
