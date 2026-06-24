# Coder layer — autonomous build loop

## Overview

The coder layer is what turns tickets into shipped pull requests. It runs entirely on the host (not inside a sandbox), is triggered by the `execute_ticket` MCP tool, and operates as a background daemon thread so the orchestrator's session can exit immediately.

## The build pipeline (per ticket)

```
execute_ticket(id)
    │
    ▼
_run_build (daemon thread, host-side)
    ├─ start heartbeat thread (UPDATE updated_at every 60s — liveness signal for the reaper)
    ├─ sync clone to main
    ├─ read context_files + source tree
    ├─ coder.py (LLM call, streamed)
    │   ├─ attempt 1: Qwen 3.6-27B, reasoning OFF (/no_think)
    │   ├─ attempt 2: Qwen 3.6-27B, reasoning ON
    │   ├─ attempt 3: Sonnet 4.6
    │   └─ attempt 4: Opus 4.8
    ├─ parse ===FILE:=== output → write files to clone
    ├─ git commit + push (force-with-lease, idempotent)
    ├─ gh pr create (or update existing PR — idempotent)
    └─ set ticket: status=review, pr_url=...
```

On any exception (including push failure): ticket reopens to `open` if `attempts < 6`, else `failed` (terminal).

## Escalation ladder

| Attempt | previous_failures | Model | Reasoning | Cost |
|---|---|---|---|---|
| 1 | 0 | Qwen 3.6-27B | OFF | Free |
| 2 | 1 | Qwen 3.6-27B | ON | Free |
| 3 | 2 | Sonnet 4.6 | — | ~$0.05–0.30 |
| 4 | 3 | Opus 4.8 | — | ~$0.20–1.00 |
| ≥5 | — | **stop** (`failed`) | — | No further burn |

At `attempts = 3` an escalation gate fires: n8n posts a Telegram alert, pauses the ticket, and waits for manual approval before proceeding to Sonnet. This prevents silent cloud spend escalation.

`model_hint` in the ticket payload overrides the ladder (use `'sonnet'` or `'opus'` to skip Qwen retries for tickets known to need cloud capability).

## Retry context (the reviewer feedback loop)

When a ticket is reopened after a failed PR review, the reviewer's feedback is stored in `last_error`. On the next build attempt:
- The coder receives a `RETRY_SYSTEM` prompt that includes the prior failure
- `[file.ext]` tags in `last_error` scope the coder to specific files (it treats untagged files as context-only)
- "No changes to commit" from git preserves the original `last_error` rather than overwriting it with the system message — the escalated rung still knows what to fix

## The idempotent PR

`open_pr` uses a deterministic branch name derived from the ticket. A retry force-pushes to the same branch, which updates the existing PR rather than creating a new one. This means a ticket that escalates from Qwen to Sonnet to Opus shows one PR with an evolving commit history, not three separate PRs.

## Coder context (what it receives)

Each ticket payload carries:
- `objective` — what to build
- `acceptance_criteria` — falsifiable success conditions
- `files` — what files to create or modify
- `context_files` — existing files it must read to understand the integration contract (ORM models, session classes, API contracts)
- `constraints` / `out_of_scope`
- `dependencies` — ticket IDs that must be `done` before this one is claimed

Without `context_files`, the model invents a parallel design. Thin tickets (missing integration context) are the primary cause of hallucinated code.

## Deployment note

`memory-mcp/server.py` imports `coder/*` and `build.py` at startup and Python caches the modules. **Editing any coder file requires `sudo systemctl restart memory-mcp-<ws>`** — the running process will not pick up edits until restarted. Verify with `ps -o lstart=` on the server.py PID vs the file mtime.

For the full process map, see `docs/PROCESS-MAP.md`.  
For escalation workflow detail, see [[layers/observability]].
