# Observability layer — Langfuse, Grafana, n8n

## Components

| Service | Port | Purpose |
|---|---|---|
| Langfuse v3 | 3000 (127.0.0.1) | LLM traces, cost per call, cache hit tracking |
| Grafana | 3001 | Cross-workspace dashboards |
| Prometheus | 9092 | Metrics scraping |
| node_exporter | 9100 | Host metrics (CPU, memory, disk, GPU) |
| postgres_exporter | 9187 | DB metrics |
| n8n | 5678 | Workflow automation (processor, reaper, escalation, session reset) |

## Langfuse v3

Langfuse captures every LLM call that passes through LiteLLM. Observations land in **ClickHouse** (not Postgres — that's a v2 behaviour). Key columns: `provided_model_name`, `total_cost`, `usage_details` (Map with `input`, `output`, `cache_read_input_tokens`), `start_time`.

Cost filter: `provided_model_name LIKE 'claude%'` catches all cloud-billable calls. Local Ollama calls have `total_cost = 0`.

## Grafana dashboards (4 dashboards)

- **System overview** — ticket counts by status, MCP health, workspace heartbeats
- **Ticket workflow** — PRs waiting for review, coder build outcomes, live build heartbeat, reaper activity
- **Coder cost** — per-model spend over time, build success/failure timeline
- **Workspace overview** — per-workspace summary (rolled out incrementally as each workspace is activated)

## Grafana alerts (9 active rules)

| Alert | Trigger | Action |
|---|---|---|
| MCP down | `component='mcp_health'` errors in last 10 min | Telegram |
| Coder failure | Infrastructure errors >2 in 30 min | Telegram |
| Review ready | Tickets in `review` status | Telegram (triggers `/review-pending`) |
| Escalation pending | `escalation_pending` log row present | Telegram |
| Heartbeat OK | `HEARTBEAT_OK` in ClickHouse observations | Status check |
| + 4 others | Various ticket/health conditions | Telegram |

## n8n workflows (5 active)

| Workflow | Cadence | Purpose |
|---|---|---|
| `workflow-processor` | Every 5 min | Reaper → per-ws gate → fire `[EXECUTE NEXT]` |
| `workflow-session-reset` | Every 30 min | Reset memory-mcp session when wave complete |
| `workflow-escalation-trigger` | Every 60 sec (Telegram poll) | Monitor for `/requeue` and `/approve` commands |
| `workflow-escalation` | Triggered by alert | Pause ticket at attempts=3, Telegram alert, 24h auto-approve |
| `workflow-error-handler` | Triggered by n8n errors | Digest failed/stuck workflows via Telegram |

## The reaper

The processor's first action each cycle is the **reaper** — it recovers work that got stuck without a heartbeat:

```sql
UPDATE tickets SET status = CASE
  WHEN attempts >= 6 THEN 'failed'
  ELSE 'open'
END
WHERE (status = 'running' AND updated_at < now() - interval '5 minutes')
   OR (status = 'failed' AND attempts < 6)
```

The reaper runs BEFORE the gate check, so a dead build is recovered before the processor decides whether to fire a new one.

## The escalation gate

At `attempts = 3`, before escalating to Sonnet, the system:
1. Logs an `escalation_pending` row
2. Grafana Alert 8 fires → Telegram notification
3. n8n pauses the ticket and waits for `/approve` or `/requeue` from the operator
4. After 24h with no response and cost < $5, auto-approves

This prevents silent cloud spend escalation on difficult tickets.

## MCP health monitoring

A systemd timer runs every 10 min: it attempts to connect to each MCP endpoint and logs the result as `component='mcp_health'` in the `logs` table. Grafana Alert 1 fires if any errors appear in the last 10 min.

For alert rule detail, see `grafana/alert-rules.md`.  
For n8n workflow JSONs, see `n8n/`.
