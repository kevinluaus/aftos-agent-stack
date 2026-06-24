# Memory layer — pgvector + BGE-M3 + memory-mcp

## Two types of memory

| Type | Storage | How it's written | How it's read |
|---|---|---|---|
| **Episodic** | `memory_episodic` (Postgres) | Agent calls `memory_store_episodic` after each ticket | `memory_recall_recent` retrieves the last N entries |
| **Semantic** | `memory_semantic` (pgvector, 1024-dim) | Nightly BGE-M3 indexer embeds the Obsidian vault | `memory_search` does cosine-similarity retrieval |

## Obsidian vaults (the knowledge base)

Each workspace has an Obsidian vault on the host at `~/agent-stack/workspaces/ws-<ws>/obsidian-vault/`. The orchestrator writes to it after each PR review: decisions, review notes, daily logs. The vault is the durable, human-readable record of what the agent has learned and decided.

Vault structure (per workspace):
```
obsidian-vault/
├── README.md
├── context/          ← PR review notes written by orchestrator
├── decisions/        ← architectural and product decisions
├── drafts/           ← work-in-progress
└── features/         ← feature-level notes
```

## BGE-M3 nightly indexer

A cron job runs at 04:00 daily for each workspace:
```
memory/run-index.sh ws-<ws> ~/agent-stack/workspaces/ws-<ws>/obsidian-vault/
```

It reads every `.md` file in the vault, splits on blank lines, embeds each chunk via BGE-M3 (Ollama at `localhost:11434`), and upserts into `memory_semantic`. Chunks that haven't changed since the last run are skipped (content-hash check), so only new or modified content incurs an embed call.

Logs: `~/agent-stack/logs/index-<ws>.log`

## memory-mcp (the MCP server)

One systemd-managed FastMCP service per workspace exposes all memory + ticket tools to its sandbox. Services are named `memory-mcp-<ws>` and assigned unique ports starting at 8088 (one per workspace).

Bound to `172.18.0.1` (host-side only). Each service carries a `WORKSPACE_ID` env var so all DB queries are automatically scoped.

**Tools exposed:**
- `memory_search(query)` — semantic search via pgvector cosine similarity
- `memory_store_episodic(content)` — write an episodic memory entry
- `memory_recall_recent(n)` — retrieve the last N episodic entries
- `create_ticket`, `list_tickets`, `get_ticket`, `update_ticket_status`, `claim_next_ticket` — ticket state machine
- `execute_ticket(id)` — spawns the host-side build thread (the core dispatch action)
- `log_event(level, message, metadata)` — structured logging to the `logs` table

## Admin endpoint (session reset)

Each memory-mcp service also runs an HTTP admin server on port `MCP_PORT + 100`. `POST /admin/reset-session` renames active session `.jsonl` files and runs cleanup — used by the n8n session-reset workflow (fires every 30 min when no build is active) to prevent context accumulation.

For operational procedures, see `docs/stages/stage-7.md` §Memory.
