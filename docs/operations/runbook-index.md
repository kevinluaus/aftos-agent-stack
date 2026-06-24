# Runbook index — operational procedures

This note is a pointer map. The actual step-by-step procedures live in the build repo at `~/agent-stack/docs/`.

## MCP setup runbooks

| Runbook | What it covers |
|---|---|
| `docs/guides/filesystem-mcp-node-direct-PER-WORKSPACE-runbook.md` | Node-direct filesystem MCP installation (all 3 workspaces) |
| `docs/guides/tavily-PER-WORKSPACE-runbook.md` | Tavily MCP provider injection (all 3 workspaces) |
| `docs/guides/RUNBOOK-github-PAT-3-workspaces-CORRECTED-2026-06-12.md` | GitHub PAT cutover (the authoritative runbook; supersedes the earlier draft) |

## Recovery runbooks

| Runbook | What it covers |
|---|---|
| `docs/guides/reboot-recovery-runbook.md` | Recovery after host reboot (gateway re-spawn, MCP re-registration, restart-policy verification) |
| `docs/guides/nemoclaw-guide.md` | NemoClaw operator manual v0.0.44 — sandbox lifecycle, MCP registration, provider wiring |

## GitHub setup

| Doc | What it covers |
|---|---|
| `docs/guides/github-setup.md` | GitHub App creation, org setup, repo structure, isolation test (GH.1–GH.10) |

## Common operational tasks

**Restart a memory-mcp service after code edits:**
```bash
sudo systemctl restart memory-mcp-<ws>
```

**Reset a workspace agent session (clears stale context):**
```bash
# Rename the active session file, then cleanup
docker exec ws-<ws> sh -c 'mv /sandbox/.openclaw/sessions/main.jsonl /sandbox/.openclaw/sessions/main.jsonl.reset.$(date +%s)'
openclaw sessions cleanup --fix-missing
docker restart ws-<ws>
```

**Check n8n workflow status:**
```bash
docker exec n8n n8n workflow:list
```

**Verify LiteLLM is up:**
```bash
curl http://localhost:4000/health/readiness
```

**Force-run the vault indexer for a workspace:**
```bash
/home/klu/agent-stack/memory/run-index.sh ws-<ws> /home/klu/agent-stack/workspaces/ws-<ws>/obsidian-vault/
```

## Deployment checklist (after editing coder/build/server files)

1. `sudo systemctl restart memory-mcp-<ws>` — Python module cache invalidation
2. Verify the restarted process is newer than the edited file: `ps -o lstart= -p $(pgrep -f memory-mcp/server.py)`
3. If memory-mcp restarted, the gateway's MCP session goes stale → gateway restart may be needed
4. If SOUL.md edited: `docker cp` into sandbox + `chown 998` + `docker restart <sandbox>`
5. If n8n workflow JSON edited: `import:workflow` → `update:workflow --active=true` → `docker restart n8n`
