# Design decision — GitHub strategy

## The structure

One GitHub App owned by the personal account, installed into separate GitHub organisations — one per workspace:

- Each workspace gets its own GitHub org (e.g. `your-org-alpha`, `your-org-beta`)
- Each org contains two repos: an orchestrator repo (SOUL.md, blueprints, strategy) and a coder repo (shipped product code)
- Org isolation is the load-bearing property: a token minted for one org cannot reach another org's repos

**Two repos per workspace**: the orchestrator repo holds SOUL.md, blueprints, and strategy; the coder repo holds all shipped product code.

## Authentication — 366-day org-scoped PATs

The original design used GitHub App installation tokens (~1h expiry), which required a rotation proxy. This was retired in favour of **366-day org-scoped fine-grained PATs** (one per organisation, stored in `op://YOUR_VAULT/<ws>-github-pat/credential`).

Each PAT is:
- Scoped to its org only (cannot access another org's repos)
- Injected via the OpenShell provider mechanism (the sandbox agent cannot read it)
- Used for both the GitHub MCP (read operations) and the coder push path (write operations)
- Set to expire 366 days from creation — annual rotation is the only maintenance task

## Workspace isolation — the load-bearing test

The isolation test verifies that a token minted for org-A returns 404 on any org-B repo. This is the security property the whole GitHub design rests on. It must be verified end-to-end before any real workspace is wired.

## The MCP server (per workspace)

Each workspace runs its own GitHub MCP container:
- Image: `ghcr.io/github/github-mcp-server:v1.1.2` (pinned — newer versions have App auth regressions)
- Runs in read-only mode with toolsets: `repos,issues,pull_requests`
- Published at `172.18.0.1:<8077|8078|8079>`, bound to that address only
- Carries `--restart unless-stopped` (a reboot that drops the restart policy silently kills GitHub access for that workspace — F37)

## The coder push path

Code is written to a local clone at `~/agent-stack/workspaces/ws-<ws>/coder-repo/` and pushed via the `gh` CLI using the same org PAT as the MCP. This means one PAT covers both reading issues (via MCP) and pushing code (via coder) — no additional credential.

Push is idempotent: the branch name is derived from the ticket ID; a retry force-pushes to the same branch, updating the existing PR.

For the runbook, see `docs/guides/RUNBOOK-github-PAT-per-workspace.md`.
