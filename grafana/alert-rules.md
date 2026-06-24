# Grafana Alert Rules — Stage 8.5

Configure in Grafana → Alerting → Alert rules → + New alert rule.
Contact point for all rules: **aftos-ops-telegram**

---

## Alert 1 — Coder escalation (sonnet/opus coder fired)

**Name:** Coder escalation — cloud tier used  
**Folder:** agent-stack  
**Datasource:** langfuse-clickhouse  
**Evaluate every:** 5m | Pending period: 0s (fire immediately)

**Query A:**
```sql
SELECT count() AS escalations
FROM observations
WHERE is_deleted = 0
  AND metadata['model_group'] IN ('sonnet-coder', 'opus-coder')
  AND start_time >= now() - INTERVAL 10 MINUTE
```

**Condition:** `A` IS ABOVE `0`  
**Message:** `⚠️ Coder escalated ({{ $values.A }} cloud calls / 10min)`

---

## Alert 2 — Opus % exceeds budget

**Name:** Opus usage > 20%  
**Datasource:** langfuse-clickhouse  
**Evaluate every:** 15m | Pending period: 15m

**Query A:**
```sql
SELECT round(
  count() FILTER (WHERE metadata['model_group'] = 'opus-coder') * 100.0
  / nullIf(count(), 0), 1
) AS opus_pct
FROM observations
WHERE is_deleted = 0
  AND metadata['model_group'] IN ('local-coder', 'sonnet-coder', 'opus-coder')
  AND start_time >= now() - INTERVAL 24 HOUR
```

**Condition:** `A` IS ABOVE `20`  
**Message:** `🔴 Opus {{ $values.A }}% of coder decisions (target <10%)`

---

## Alert 3 — Cloud cost spike

**Name:** Cloud cost > $5 in 24h  
**Datasource:** langfuse-clickhouse  
**Evaluate every:** 30m | Pending period: 0s

**Query A:**
```sql
SELECT round(sum(total_cost), 2) AS cost_usd
FROM observations
WHERE is_deleted = 0
  AND total_cost > 0
  AND start_time >= now() - INTERVAL 24 HOUR
```

**Condition:** `A` IS ABOVE `5`  
**Message:** `💸 LLM spend ${{ $values.A }} / 24h`

---

## Alert 4 — Stuck tickets

**Name:** Stuck ticket (running > 60 min)  
**Datasource:** agentstack (Postgres)  
**Evaluate every:** 5m | Pending period: 5m

**Query A (raw SQL):**
```sql
SELECT COUNT(*) AS stuck
FROM tickets
WHERE status = 'running'
  AND started_at < now() - interval '60 minutes'
```

**Condition:** `A` IS ABOVE `0`  
**Message:** `🔴 {{ $values.A }} ticket(s) stuck >60min`

---

## Alert 5 — PRs ready for batched review (queue drained)

**Name:** Review-ready — drained queue with open PRs
**Folder:** agent-stack
**Datasource:** agentstack (Postgres)
**Evaluate every:** 5m | Pending period: 5m (avoid firing during brief transitions)

**Query A (raw SQL):**
```sql
SELECT count(*) AS review_ready
FROM tickets t
WHERE t.status = 'review'
  AND (SELECT count(*) FROM tickets WHERE status = 'running') = 0
  AND NOT EXISTS (
    SELECT 1 FROM tickets o
    WHERE o.workspace_id = t.workspace_id
      AND o.status = 'open' AND o.attempts < 3
      AND NOT EXISTS (
        SELECT 1 FROM unnest(o.dependencies) dep
        JOIN tickets dt ON dt.id = dep WHERE dt.status <> 'done'
      )
  )
```

**Condition:** `A` IS ABOVE `0`
**Message:** `🔎 {{ $values.A }} PR(s) ready — run /review-pending`

Counts only tickets in `review` whose workspace has **no runnable-open work** (deps merged) and nothing
running — i.e. the processor has gone quiet and is waiting on you. Re-notify cadence is controlled by
the notification policy (set a sane repeat interval, e.g. 4h, so it nudges without spamming). This
**replaces** the `review_ready_alert*.sh` cron — keep the script only as a manual CLI check.

---

---

## Alert 6 — MCP endpoint down

**Name:** MCP endpoint down  
**Group:** agent-stack-health  
**Folder:** agent-stack  
**Datasource:** agentstack (Postgres)  
**Evaluate every:** 5m | Pending period: 5m

**Query A (raw SQL):**
```sql
SELECT COUNT(*) AS down_count
FROM logs
WHERE component = 'mcp_health'
  AND level = 'error'
  AND created_at > now() - INTERVAL '10 minutes'
```

**Condition:** `A` IS ABOVE `0`  
**Message:** `🔴 MCP down ({{ $values.A }} errors / 10min)`

**Data source:** `logs` table, written every 2 min by `mcp-health-check.timer`.  
If `noData` fires it means the health-check timer itself is dead — check with `systemctl status mcp-health-check.timer`.

---

## Alert 7 — Coder infra failure

**Name:** Coder build infra failure  
**Group:** agent-stack-health  
**Folder:** agent-stack  
**Datasource:** agentstack (Postgres)  
**Evaluate every:** 5m | Pending period: 5m

**Query A (raw SQL):**
```sql
SELECT COUNT(*) AS infra_failures
FROM logs
WHERE component = 'coder'
  AND level = 'error'
  AND (
    message ILIKE '%Timeout%'
    OR message ILIKE '%APIError%'
    OR message ILIKE '%APIConnectionError%'
    OR message ILIKE '%InternalServerError%'
  )
  AND created_at > now() - INTERVAL '30 minutes'
```

**Condition:** `A` IS ABOVE `2`  
**Message:** `🔴 Coder infra: {{ $values.A }} failure(s) / 30min`

Threshold of 2 avoids noise from single transient errors. Normal escalation failures (wrong code → retry)
do NOT match — those don't produce `level='error'` coder logs.

---

## Notes

- Alert 5 uses the **agentstack** datasource (Postgres), like Alert 4.
- Alerts 1–3 use the **langfuse-clickhouse** datasource (Clickhouse).
- Alert 4 uses the **agentstack** datasource (Postgres) — redundant with the n8n stuck-ticket flow but fires faster (5 min vs 30 min).
- Alerts 6–7 use **agentstack** (Postgres) via the `logs` table written by mcp_health component / coder component.
- Grafana alerting against Clickhouse requires the `grafana-clickhouse-datasource` plugin (already installed).
- All alerts route to **aftos-ops-telegram** contact point.
