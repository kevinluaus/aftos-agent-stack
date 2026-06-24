# What is the aftos agent-stack?

The aftos agent-stack is a self-hosted AI agent infrastructure running on a single NVIDIA GB10 Grace Blackwell workstation. It runs multiple independent AI agents concurrently, each working autonomously on a different software product, using local language models for cost and latency and escalating to cloud models only when needed.

## The core idea

Three AI agents — each assigned to a distinct product — work through a software development backlog without human intervention. They write code, open pull requests, index knowledge, and learn from feedback. A human reviews PRs and approves escalations via Telegram. Everything else is autonomous.

## What it is not

- It is not a hosted or cloud-native system. Every service runs on one physical machine.
- It is not a general-purpose chatbot platform. It is purpose-built for autonomous software development across three concurrent workspaces.
- It is not a demo or prototype. It ships real code to real GitHub repositories with a real escalation and review loop.

## The workspaces

Each workspace represents one software product. In this build, three workspaces run concurrently across three separate product domains. Each has its own isolated agent, GitHub organisation, Telegram bot, secrets, and memory. They share the physical host, model serving layer, and observability stack.

| Item | Per workspace |
|---|---|
| Sandbox | Isolated NemoClaw/OpenClaw container |
| GitHub org | One dedicated org; agent only reaches its own repos |
| Telegram bot | One bot for review alerts and daily summaries |
| Memory MCP | Dedicated port; all DB queries auto-scoped by workspace ID |
| GitHub MCP | Dedicated container carrying only this org's PAT |

→ See [[workspaces/workspace-template]] for the full structure and how to add a new workspace.

## The principles it was built on

1. All external side-effects require human approval.
2. All agent execution is sandboxed.
3. All secrets are accessed via abstraction — never in plaintext on disk.
4. All workflows are traceable end-to-end.
5. All providers are swappable behind one interface.
6. All workspaces are isolated by default.
7. Every component must justify itself with a problem that already hurt — no speculative infrastructure.

→ See [[overview/architecture-map]] for how the layers connect.  
→ See [[design-decisions/local-vs-cloud]] for the cost and escalation strategy.
