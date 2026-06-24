# Hardware — NVIDIA GB10 Grace Blackwell

## The machine

**Acer Veriton GN100 AI Mini Workstation**

| Spec | Value |
|---|---|
| Chip | NVIDIA GB10 Grace Blackwell Superchip |
| Architecture | ARM64 |
| OS | DGX OS (Ubuntu-based) |
| Memory | 128 GB unified (CPU + GPU share the same pool) |
| Memory bandwidth | **273 GB/s** — the real bottleneck, not memory size |
| Storage | ~3.7 TB |
| Form factor | Mini workstation (desktop, not a server rack) |

## Why unified memory matters

The GB10 has no discrete GPU memory — the CPU and GPU share the same 128 GB pool. This means:

- Large language models load entirely into memory visible to both compute units. A 27B parameter model quantised to Q4_K_M occupies ~17 GB and stays resident.
- The bottleneck for token generation is **memory bandwidth** (273 GB/s), not memory capacity. Throughput on a 27B Q4 model is ~9.9 tokens/second — bandwidth-bound.
- Multiple models can coexist: Qwen 3.6-27B (17 GB), Llama 3.1:8b (4.9 GB), and BGE-M3 (1.2 GB) all fit simultaneously with headroom for everything else.

## What runs on the host

Everything co-resides on this single machine:

- Three NemoClaw/OpenClaw agent sandboxes (Docker containers)
- Ollama model serving (Qwen, Llama, BGE-M3)
- LiteLLM proxy (routes to local and cloud models)
- Langfuse v3 (LLM observability — 6 containers)
- Grafana + Prometheus + exporters
- n8n (workflow automation)
- Postgres 16 + pgvector (all state: tickets, memory, logs)
- Three GitHub MCP servers (one per workspace org)
- Three memory-mcp servers + three obsidian-mcp servers

## The constraint this creates

One physical machine = one point of failure. The design accepts this for v0 on the basis that all data is in Postgres (backed up) and all configuration is in code. Recovery from a full machine loss is: re-provision the host, restore from backup, re-run the stage runbooks.

→ See [[layers/inference]] for how model serving is tuned for this hardware.
