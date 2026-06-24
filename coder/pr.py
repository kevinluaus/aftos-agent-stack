"""Open a GitHub PR: write generated files into the workspace's coder clone,
commit them to a fresh branch off base, push, and open a PR with cost annotation.

The orchestrator passes the *final* file contents it wants committed (it is the
reviewer/decider — drafts discipline). This module does the git/gh mechanics.

PR creation uses the GitHub REST API directly (not the `gh` CLI) so that the
per-workspace GH_TOKEN injected by the memory-mcp service (via `op run`) is
used without relying on `gh auth` state, which is not reliably set up in the
service environment. Push still uses `gh auth git-credential` (via repo.py's
credential helper) since that credential path is known-good.
"""
import os
import re
import subprocess as _sp
import requests as _requests

from exceptions import CoderPushError
from repo import ensure_clone, write_files, _git, CODER_REPOS


def _gh_headers() -> dict:
    token = os.environ.get("GH_TOKEN", "")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _org_repo(ws: str) -> str:
    """Extract 'Owner/repo' from the workspace's CODER_REPOS entry."""
    url = CODER_REPOS.get(ws, "")
    m = re.search(r"github\.com[/:](.+?)(?:\.git)?$", url)
    if not m:
        raise ValueError(f"Cannot parse org/repo from CODER_REPOS[{ws!r}] = {url!r}")
    return m.group(1)  # e.g. "your-org/your-coder-repo"


def _find_open_pr(org_repo: str, branch: str) -> str | None:
    """Return the URL of an existing open PR for the branch, or None."""
    org = org_repo.split("/")[0]
    resp = _requests.get(
        f"https://api.github.com/repos/{org_repo}/pulls",
        params={"state": "open", "head": f"{org}:{branch}"},
        headers=_gh_headers(),
        timeout=30,
    )
    if resp.ok:
        prs = resp.json()
        if prs:
            return prs[0].get("html_url")
    return None


def _create_pr(org_repo: str, base: str, branch: str, title: str, body: str) -> str:
    """Create a PR via the GitHub REST API and return its URL."""
    resp = _requests.post(
        f"https://api.github.com/repos/{org_repo}/pulls",
        json={"title": title, "head": branch, "base": base, "body": body},
        headers=_gh_headers(),
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(
            f"GitHub API PR create failed HTTP {resp.status_code}: {resp.text[:400]}"
        )
    return resp.json()["html_url"]


def open_pr(
    branch: str,
    title: str,
    body: str,
    files: list | None = None,
    cost_usd: float = 0.0,
    model_used: str = "unknown",
    task_signature: str = "ad-hoc",
    base: str = "main",
    workspace_id: str | None = None,
) -> str:
    """Write `files`, commit to `branch` off `base`, push, open a PR. Returns the PR URL.

    files: list of {"path": "<repo-relative path>", "content": "<full file content>"}.
    """
    ws = workspace_id or os.environ.get("WORKSPACE_ID", "unknown")
    if not files:
        raise ValueError("open_pr requires a non-empty `files` list of {path, content}.")

    path = ensure_clone(ws, base=base)

    # If the branch already exists on remote (a retry), build the new commit on top
    # of it so the coder's partial output (e.g. only the changed file) sits on top of
    # the previous attempt's full implementation. The PR diff against `base` (main)
    # then shows the complete set of files, not just what the coder emitted this run.
    # For a fresh branch, fall back to starting from a clean base.
    ls = _git(path, "ls-remote", "--heads", "origin", branch, network=True)
    branch_exists = bool(ls.stdout.strip())
    if branch_exists:
        _git(path, "fetch", "origin", branch, network=True)
        _git(path, "checkout", branch)
        _git(path, "reset", "--hard", f"origin/{branch}")
    else:
        _git(path, "checkout", base)
        _git(path, "reset", "--hard", f"origin/{base}")
        _git(path, "checkout", "-B", branch)

    written = write_files(path, files)
    _git(path, "add", "-A")
    if not _git(path, "status", "--porcelain").stdout.strip():
        raise RuntimeError("No changes to commit — `files` did not modify the tree.")
    _git(path, "commit", "-m", title)
    # Existing branch: plain push (we fetched + added one commit on top — no force needed).
    # Fresh branch: force-with-lease since remote may have a stale branch from a prev run.
    push_flags = ["-u", "origin", branch] + ([] if branch_exists else ["--force-with-lease"])
    # Only the push is wrapped — fetch/checkout/reset above remain as bare CalledProcessError
    # since those failures (network at checkout, remote not found) are different failure modes
    # that don't warrant the attempts-decrement treatment. (F95)
    try:
        _git(path, "push", *push_flags, network=True)
    except _sp.CalledProcessError as e:
        raise CoderPushError(
            branch=branch,
            stderr=e.stderr or "",
            return_code=e.returncode,
        ) from e

    annotated_body = f"""{body}

---
**Coder run metadata**
- Model: `{model_used}`
- Cost: ${cost_usd:.4f}
- Task signature: `{task_signature}`
- Workspace: `{ws}`
- Files: {', '.join(written)}
"""
    org_repo = _org_repo(ws)

    # Idempotent: if a retry force-pushed to an existing branch that already has an
    # open PR, return that PR's URL instead of failing with "PR already exists".
    existing = _find_open_pr(org_repo, branch)
    if existing:
        return existing

    return _create_pr(org_repo, base, branch, title, annotated_body)
