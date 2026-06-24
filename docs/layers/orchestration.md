# Orchestration layer — NemoClaw + OpenClaw

## What it is

Each workspace runs a sandboxed AI agent inside a Docker container managed by **NemoClaw** (NVIDIA's alpha agent runtime). Inside each sandbox, the agent is **OpenClaw** — an LLM-backed autonomous agent that has access to MCP tools and communicates with the outside world via a Telegram bot.

## The stack

| Component | Role |
|---|---|
| **NemoClaw v0.0.44** | Host-side daemon that manages sandbox lifecycle, egress policy, and the OpenShell gateway |
| **OpenShell gateway** | WebSocket-only gateway on `:8081` that brokers connections between the host and the sandboxes. Runs as uid 998 (sandbox user). |
| **OpenClaw** | The in-sandbox agent runtime. Holds SOUL.md as its operating identity, exposes tool calls via MCP, and drives LLM inference via the gateway. |
| **SOUL.md** | The agent's standing instruction set — 61 lines defining its identity, operational mode (PLAN vs EXECUTE), retry rules, and escalation behaviour. Lives in `/sandbox/.openclaw/workspace/SOUL.md`. |

## Sandbox isolation

Each workspace sandbox is a Docker container with:
- Its own writable filesystem layer
- Its own `openclaw.json` config (MCP registrations, egress presets)
- Its own provider-injected secrets (Tavily key, GitHub PAT — never in plaintext config)
- Its own Telegram bot identity
- Its own network egress policy via NemoClaw presets

Sandboxes cannot read each other's filesystems, credentials, or memory.

## The operating mode (PLAN vs EXECUTE)

The orchestrator operates in two distinct modes, controlled by the trigger message:

- **`[PLAN]`** — the orchestrator uses Claude Code (flat-rate subscription) for deep planning: reads the blueprint, merged PRs, and obsidian vault; decomposes the next work slice into tickets; seeds them via the MCP tools.
- **`[EXECUTE NEXT]`** — the orchestrator claims one ticket atomically, calls `execute_ticket(id)`, and exits immediately. It does not write code, wait, retry, or run diagnostics. Execution is handed off to the host-side build thread.

This thin-dispatcher pattern keeps the in-sandbox session short and cheap.

## How the agent is triggered

The n8n processor (every 5 min) sends `[EXECUTE NEXT]` via the workspace's Telegram bot. OpenClaw receives it, runs its dispatch, and exits. There is no always-on agent loop — sessions are spawned per-ticket.

## Key constraints (NemoClaw v0.0.44 alpha)

- **Pin at v0.0.44** — v0.0.45 has known regressions
- **MCP registration** is non-deterministic across a single gateway restart; tools may connect on the next heartbeat cycle (~30 min)
- **`nemoclaw rebuild`** = upgrade (destroys the sandbox and wipes provider config); never use it
- **`docker restart`** preserves the writable layer; `--recreate-sandbox` destroys it

For operational procedures, see `docs/guides/nemoclaw-guide.md`.
