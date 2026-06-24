# Design decision — Local vs cloud (cost strategy)

## The architecture principle

Local is the default. Cloud is the escalation. The system is designed so that the vast majority of token consumption — the orchestrator dispatch, the coder's first two attempts, all specialist work, all embeddings — costs nothing because it runs on local Ollama.

Cloud spend occurs in two scenarios only:
1. The coder's escalation ladder reaches rung 3 (Sonnet) or rung 4 (Opus) after local attempts fail
2. Planning sessions and PR review via the Claude Code flat-rate subscription (zero marginal API cost)

## The cost model (observed, 2026-06-22)

| Layer | Model | Weekly cost |
|---|---|---|
| Orchestrator dispatch | Llama 3.1:8b (local) | $0 |
| Coder rungs 1–3 | Qwen 3.6-27B (local) | $0 |
| Coder rung 4 (escalation) | Sonnet 4.6 | ~$0.35–0.60 per ticket |
| Planning + review | Claude Code subscription | Flat rate (no API cost) |
| Embeddings | BGE-M3 (local) | $0 |
| Specialists | Qwen 3.6-27B (local) | $0 |
| Estimated API saving vs all-cloud | — | ~$14.60/week |

Previous configuration (Sonnet as orchestrator): ~$9.18/week in orchestrator cost alone, plus coder costs. Switching to Llama 3.1:8b as orchestrator eliminated this.

## The joint workflow (cost-aware planning)

The most expensive cognitive work — decomposing blueprints into tickets, reviewing PRs, evaluating code quality — runs in **Claude Code** (flat-rate subscription) rather than the metered API. This is the practical answer to the D28 cost problem: move token-heavy reasoning off the API onto the subscription.

The API key is reserved for what only the autonomous agent can do: the coder's escalated cloud attempts, which happen without human supervision.

## The escalation gate

Before committing to Sonnet (rung 3 → rung 4), the system requires human approval via Telegram. This prevents a difficult ticket from silently escalating to Opus without operator awareness. Auto-approval kicks in after 24h if cost impact is below $5 — so the gate does not block progress on low-stakes tickets.

## The model flip design

The LiteLLM alias system makes the local→cloud swap a config edit:

```yaml
# litellm/config.yaml
- model_name: orchestrator-primary
  litellm_params:
    model: ollama_chat/llama3.1:8b   # ← change this line
```

`sudo systemctl restart litellm` — no sandbox rebuild, no MCP re-registration. The entire architecture was designed around this property: LiteLLM was added at Stage 3.5 specifically so this flip would always be a one-line change.

## What to watch

- **Opus usage %** — target <10% of coder invocations, alert at ≥20%
- **Cache hit rate** — `cache_read_input_tokens > 0` in Langfuse confirms prompt caching is working
- **Cloud spend vs wave size** — visible in the Grafana coder-cost dashboard

For Langfuse query detail, see [[layers/observability]].
