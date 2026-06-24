# Design decision — Secrets model

## The rule (non-negotiable)

No secret ever appears in plaintext in a config file, environment variable, or process argument visible to an agent.

## How it works

All secrets live in **1Password vault `aftos`**. At runtime, they are resolved via the `op run` pattern:

```bash
export OP_SERVICE_ACCOUNT_TOKEN="$(cat /etc/agent-stack/op-service-account-token)"
op run --env-file=/home/klu/agent-stack/memory/.env.memory -- python3 server.py
```

The `.env.memory` file contains only `op://` references — bare paths that `op run` resolves at process start. The resolved values land in the child process's environment, never in any file on disk.

## MCP secrets — provider injection

For in-sandbox MCP tools (Tavily, GitHub), the secret cannot go through `op run` because the sandbox process is managed by NemoClaw, not directly launched by the operator. Instead:

1. An **OpenShell generic provider** is created on the host: `openshell provider create --name <name> --type generic --credential <KEY> --value op://YOUR_VAULT/<item>/credential`
2. The provider is attached to the workspace: `openshell sandbox provider attach <ws> <name>`
3. At gateway startup, the provider injects a placeholder (`openshell:resolve:env:v<id>_<KEY>`) into the gateway environment
4. MCP configs reference the secret as `${KEY}` (in the URL or headers)
5. The L7 egress proxy substitutes the real value on every outbound request

The agent inside the sandbox sees only `${KEY}` — the raw token is never in `openclaw.json`, never in the agent's environment, and cannot be exfiltrated.

**This was verified:** the GitHub PAT posture test confirmed the sandbox env shows `GITHUB_TOKEN=<unset>` while the proxy correctly injects it on egress.

## Why short-lived tokens weren't the answer

The initial GitHub MCP design used App installation tokens (~1h expiry) on the assumption that short lifetime was the exfil control. This turned out to be wrong: the real exfil control is **injection** (the agent can't read the token at all). A 366-day PAT through the injection model is equally secure to a 1h token — and eliminates all rotation complexity.

Security tradeoff accepted: 1h→366d blast radius, because injection — not lifetime — is the load-bearing security property.

## Secrets map

| Secret | 1Password item | Consumer |
|---|---|---|
| Anthropic Sonnet key | `sonnet-api` | LiteLLM |
| Anthropic Opus key | `opus-budget` | LiteLLM |
| Anthropic Haiku key | `haiku-budget` | LiteLLM (heartbeat) |
| Per-ws Haiku keys | `<ws>-haiku` | LiteLLM (product chat) |
| LiteLLM master key | `litellm-master-key` | All LiteLLM callers |
| Postgres password | `postgres-agentstack` | LiteLLM, memory-mcp, exporters |
| Tavily API key | `tavily-api-key` | Provider injection → Tavily MCP |
| GitHub PATs (×3) | `<ws>-github-pat` | Provider injection → GitHub MCP + coder push |
| Telegram tokens (×4) | `agent-<ws>-telegram-token`, `ops-telegram-token` | n8n, alerts |
| Langfuse secrets (×8) | `langfuse-*` | Langfuse compose |
| Service account token | `/etc/agent-stack/op-service-account-token` | All `op run` callers |

For operational instructions, see `docs/guides/nemoclaw-guide.md` §Providers.
