# Tools layer — MCP servers

The agent's capabilities are entirely defined by its MCP tools. Each workspace sandbox has five MCP servers registered. All are host-side services; the sandbox connects to them over the Docker bridge.

## The five MCP servers (per workspace)

### 1. filesystem MCP
- **Package:** `@modelcontextprotocol/server-filesystem@2026.1.14`
- **Deployed:** node-direct from `/sandbox/.local/mcp-fs/` (not via `npx` — the gateway's 30s handshake timeout makes `npx` unreliable)
- **Scope:** `/sandbox/project` + `/sandbox/drafts` only — not the full sandbox root (which contains credentials)
- **Purpose:** lets the agent read and write its own project files and drafts

### 2. Tavily MCP (web search)
- **Provider:** generic OpenShell provider injects `${TAVILY_API_KEY}` into the MCP URL
- **URL:** `https://mcp.tavily.com/mcp/?tavilyApiKey=${TAVILY_API_KEY}`
- **Auth:** query param (not bearer)
- **Purpose:** real-time web search for research tasks

### 3. GitHub MCP
- **Image:** `ghcr.io/github/github-mcp-server:v1.1.2` (pinned — v1.1.2, newer has auth regressions)
- **Per-workspace containers:** one container per workspace, named `gh-mcp-<ws>`, each on a unique port starting at 8077
- **Auth:** 366-day org-scoped fine-grained PAT, provider-injected as `${GITHUB_TOKEN}` — never in the MCP config
- **Mode:** `--read-only --toolsets repos,issues,pull_requests`
- **Isolation:** each container holds only its org's PAT; a workspace agent can only reach its own org
- **Purpose:** list issues, read PRs, interact with the GitHub API

### 4. Obsidian MCP (vault read/write)
- **Package:** `@bitbonsai/mcpvault@0.11.0`
- **Bridge:** `supergateway` wraps the stdio-only mcpvault as a streamableHttp server
- **Ports:** unique port per workspace, starting at 8093
- **Vault:** `~/agent-stack/workspaces/ws-<ws>/obsidian-vault/` on the host
- **Purpose:** lets the orchestrator write PR review notes, decisions, and daily logs to the vault

### 5. Memory + tickets MCP
- **Server:** `memory-mcp/server.py` (FastMCP, streamable-http)
- **Ports:** unique port per workspace, starting at 8088
- **Purpose:** semantic memory search, episodic recall, full ticket state machine, structured logging, session reset, and the `execute_ticket` build dispatcher
- See [[layers/memory]] for full detail

## Secrets model for MCP tools

No MCP tool config ever contains a literal secret. Instead:
- Sensitive credentials are held in a **generic OpenShell provider** on the host
- The provider injects a placeholder (e.g. `${GITHUB_TOKEN}`) into the gateway environment at startup
- The L7 egress proxy substitutes the real credential on every outbound request
- The agent inside the sandbox only ever sees the placeholder string — it cannot exfiltrate the real token

For the security model behind this, see [[design-decisions/secrets-model]].

For operational runbooks, see:
- `docs/guides/filesystem-mcp-node-direct-PER-WORKSPACE-runbook.md`
- `docs/guides/tavily-PER-WORKSPACE-runbook.md`
- `docs/guides/RUNBOOK-github-PAT-3-workspaces-CORRECTED-2026-06-12.md`
