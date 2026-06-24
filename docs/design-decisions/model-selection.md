# Design decision — Model selection

## The principle

Every model choice answers one question: what is the cheapest model that reliably handles this task? Cloud is not the default — it is the escalation.

## The model picks (verified 2026-06-03; reassess 2026-08-05)

### Qwen 3.6-27B — the local workhorse

**Role:** coder (rungs 1–3), orchestrator fallback, all specialist tasks (research, scraper, documentation, code review)  
**Why:** Dense 27B model (April 2026), Q4_K_M quantisation at ~17 GB, GPU-resident on the GB10. Performs at approximately Opus 3 class on coding and reasoning benchmarks. Cost = $0 per call. First-turn latency ~45s (bandwidth-bound, not fixable).  
**Why not a larger model:** The bottleneck is memory bandwidth (273 GB/s), not capacity. A larger model increases weight size but not bandwidth — throughput drops. The 27B Q4 is the sweet spot.

### Llama 3.1:8b — the orchestrator dispatcher

**Role:** `orchestrator-primary` (the model that decides what to do next and calls `execute_ticket`)  
**Why:** The orchestrator's job is dispatch, not deep reasoning — claim a ticket and call one tool. An 8b model does this in ~8–10s first turn vs ~45s for Qwen. Cost = $0. Saves ~$9.18/week vs the previous Sonnet orchestrator.  
**Rollback:** swap `orchestrator-primary` back to `orchestrator-sonnet` in `litellm/config.yaml` + restart litellm.

### BGE-M3 — embeddings

**Role:** vault indexing and semantic memory retrieval (1024-dim vectors in pgvector)  
**Why:** Outperforms nomic-embed (~15 retrieval points on MTEB). 1024-dim is the native schema (`vector(1024)`). Runs at ~239ms/embedding via Ollama. Cost = $0.

### Sonnet 4.6 — cloud capable

**Role:** coder rung 4; planning sessions (via Claude Code subscription, flat-rate); PR review  
**Cost:** $3/$15 per million tokens  
**Why:** The flat-rate Claude Code subscription handles all planning and review work at zero marginal cost. The API key is only hit when the autonomous coder escalates to rung 4.

### Opus 4.8 — cloud last resort

**Role:** coder rung 5 (after 3 prior failures)  
**Cost:** $5/$25 per million tokens  
**Target:** <10% of all coder invocations. Alert threshold: ≥20%.  
**Why not more often:** The escalation gate at attempts=3 requires human approval before Opus is invoked, preventing accidental runaway spend.

### Haiku 4.5 — budget cloud

**Role:** Per-workspace product chat APIs (`<ws>-chat` LiteLLM alias); heartbeat bridging  
**Cost:** $1/$5 per million tokens  
**Why:** Customer-facing chat needs cloud quality but not Sonnet depth. Haiku provides adequate response quality at 3× lower cost.

## What was rejected and why

- **Qwen 2.5 14B + Coder 32B split** — retired in favour of the unified 3.6-27B. One model, one tag, one KV cache slot.
- **Qwen3.6-35B-A3B MoE** — candidate for August 2026 reassessment (may address the ~45s latency bottleneck). Not adopted yet.
- **Separate specialist models** — every specialist runs on the same Qwen 3.6-27B. Separate models for research vs code review vs documentation would fragment memory bandwidth with no demonstrated quality improvement.
