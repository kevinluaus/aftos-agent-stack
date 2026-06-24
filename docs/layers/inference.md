# Inference layer — LiteLLM + Ollama

## Architecture

All LLM calls — local and cloud — route through a single **LiteLLM proxy** on `:4000`. Sandboxes reach it via the Docker bridge at `172.18.0.1:4000`. The master key stays on the host; sandboxes never see API credentials.

```
sandbox → 172.18.0.1:4000 (LiteLLM) → Ollama :11434 (local)
                                     → Anthropic API (cloud)
```

This single-interface design means flipping a workspace from local to cloud is a LiteLLM config edit and a `systemctl restart litellm` — no sandbox rebuild, no MCP re-bake.

## Model roster

| Role | Model | Cost | Notes |
|---|---|---|---|
| Orchestrator (primary) | **Llama 3.1:8b** (local) | Free | Fast dispatch (~8–10s first turn); always resident |
| Orchestrator (fallback) | **Qwen 3.6-27B** (local) | Free | Full reasoning capability |
| Cloud capable | **Sonnet 4.6** | $3/$15 per M | Escalation rung 4; planning sessions |
| Cloud heavy | **Opus 4.8** | $5/$25 per M | Escalation rung 5; last resort |
| Heartbeat / chat | **Haiku 4.5** | $1/$5 per M | Per-workspace product chat alias |
| Coder (local) | **Qwen 3.6-27B** | Free | Rungs 1–3 of escalation |
| Embeddings | **BGE-M3** (1024-dim) | Free | Nightly vault indexing, semantic search |

## LiteLLM aliases (the routing contract)

| Alias | Backend | Use |
|---|---|---|
| `orchestrator-primary` | `ollama_chat/llama3.1:8b` | Default orchestrator |
| `orchestrator-fallback` | `ollama_chat/qwen3.6:27b` | Fallback orchestrator |
| `orchestrator-sonnet` | `claude-sonnet-4-6` | Quick rollback target |
| `local-coder` | `ollama_chat/qwen3.6:27b` | Coder rungs 1–3 |
| `sonnet-coder` | `claude-sonnet-4-6` | Coder rung 4 |
| `opus-coder` | `claude-opus-4-8` | Coder rung 5 |
| `bge-m3` | `ollama/bge-m3` | Embeddings |
| `haiku` | `claude-haiku-4-5-20251001` | Budget/heartbeat |

## Ollama tuning (GB10-specific)

The Ollama systemd override (`/etc/systemd/system/ollama.service.d/override.conf`) sets:
- `OLLAMA_NUM_PARALLEL=4` — four concurrent inference slots (for specialist parallelism)
- `OLLAMA_CTX_SIZE=16384` — explicit context cap; without this the default 262K ctx allocates ~42 GB KV cache per model
- `OLLAMA_FLASH_ATTN=1` — flash attention on Blackwell
- `OLLAMA_KEEP_ALIVE=-1` — models stay resident indefinitely (prewarm service ensures Qwen + Llama always loaded)

Result: Qwen 3.6-27B occupies ~23 GB (down from ~42 GB at default ctx), leaving room for Llama (4.9 GB) and BGE-M3 (1.2 GB) simultaneously.

## Prompt caching

Anthropic prompt caching is applied client-side in `coder.py` via `cache_control: {"type": "ephemeral"}` on system and file-context message blocks. OpenClaw applies caching at the `<!-- OPENCLAW_CACHE_BOUNDARY -->` marker in SOUL.md. LiteLLM itself is not configured for caching (that would conflict with OpenClaw's client-side approach).

## Cost observability

Every call flows through LiteLLM → Langfuse (ClickHouse). Grafana dashboards show per-workspace spend, cloud vs local split, and cache hit rates via `usage_details['cache_read_input_tokens']`.

For configuration detail, see `~/agent-stack/litellm/config.yaml`.  
For cost strategy, see [[design-decisions/local-vs-cloud]].
