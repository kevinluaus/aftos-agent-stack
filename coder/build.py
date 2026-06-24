"""End-to-end ticket builder for the async `execute_ticket` dispatcher.

Reads the workspace's coder clone for REAL file context (the old `code()` path
passed relative paths that never resolved → Qwen hallucinated the files), runs
the coder in full-file output mode (Qwen×2 no-think → Qwen think → Sonnet → Opus by attempt),
parses the emitted files, and opens a PR. All the slow/heavy work lives here,
off the orchestrator's turn — so a coder call can never time out the
orchestrator again, and the coder side (not the orchestrator) owns the commit,
the PR, and the ticket status. (F89)
"""
import os
import re

from exceptions import CoderFormatError
from repo import ensure_clone, _git
from coder import run_coder
from pr import open_pr

FULLFILE_SYSTEM = (
    "You are a senior engineer implementing ONE ticket against an existing codebase. "
    "Output the COMPLETE final content of every file you create or change — never a "
    "diff, never a partial snippet, never commentary. For EACH file emit exactly:\n"
    "===FILE: <repo-relative-path>===\n"
    "<the full file content>\n"
    "===ENDFILE===\n"
    "Emit nothing outside these blocks. Do not wrap the content in markdown fences. "
    "Match the surrounding code's imports and style. If the ticket asks for tests, "
    "include them as their own ===FILE=== block. The current contents of any existing "
    "target files are provided to you — modify them, do not rewrite unrelated parts.\n"
    "CRITICAL: if the provided context contains ORM models (e.g. SQLAlchemy "
    "`Mapped`/`mapped_column`), any repository or service layer you build MUST operate "
    "on those EXISTING models through the database Session — NEVER define a parallel "
    "`@dataclass` or in-memory dict/store. Implement EXACTLY the behaviour the "
    "acceptance criteria state — no extra states, methods, or transitions."
)

# Used instead of FULLFILE_SYSTEM on retries (last_error set). Tells the model
# the implementation is largely done and it must only emit the files that change.
# This prevents Qwen re-emitting all N files unchanged when only one needs fixing.
RETRY_SYSTEM = (
    "You are doing a TARGETED FIX on an existing codebase. The implementation is already "
    "complete except for the specific issue described in the reviewer feedback.\n"
    "Output ===FILE=== blocks ONLY for the file(s) that must change to address that "
    "feedback. Do NOT re-emit files that are already correct — they are preserved from "
    "the previous commit automatically.\n"
    "For each file you DO emit, provide its COMPLETE final content — never a diff or "
    "partial snippet:\n"
    "===FILE: <repo-relative-path>===\n"
    "<full file content>\n"
    "===ENDFILE===\n"
    "Emit nothing outside these blocks. Do not wrap content in markdown fences. "
    "Match the surrounding code's imports and style.\n"
    "CRITICAL: if the provided context contains ORM models (e.g. SQLAlchemy "
    "`Mapped`/`mapped_column`), any repository or service layer you build MUST operate "
    "on those EXISTING models through the database Session — NEVER define a parallel "
    "`@dataclass` or in-memory dict/store."
)

_FILE_RE = re.compile(
    r"===FILE:\s*(?P<path>.+?)\s*===\r?\n(?P<body>.*?)(?:\r?\n)?===ENDFILE===", re.DOTALL
)
_FENCE_OPEN = re.compile(r"^```[A-Za-z0-9_+\-]*\r?\n")
_FENCE_CLOSE = re.compile(r"\r?\n```\s*$")

# Matches [filename.ext] or [path/to/file.ext] tags in reviewer feedback.
# Reviewers write these to explicitly scope a retry to specific files.
# Example: "[pyproject.toml] alembic>=1.13 missing from dependencies"
_FILE_TAG_RE = re.compile(r"\[([^\]\s][^\]]*\.[a-zA-Z][a-zA-Z0-9]*)\]")


def _extract_file_hints(last_error: str) -> list[str]:
    """Return filenames/paths named in [file.ext] tags within reviewer feedback."""
    return _FILE_TAG_RE.findall(last_error or "")


def _parse_files(output: str) -> list:
    """Extract [{path, content}] from the coder's ===FILE=== blocks."""
    files = []
    for m in _FILE_RE.finditer(output or ""):
        path = m.group("path").strip()
        # The model sometimes echoes the absolute context path it was shown
        # (===FILE: /home/.../coder-repo/app/...===). Reduce any such path back to
        # repo-relative so write_files lands it in the right place. (F89)
        if "coder-repo/" in path:
            path = path.split("coder-repo/", 1)[1]
        path = path.lstrip("/")
        body = m.group("body")
        # tolerate a single wrapping ```lang ... ``` fence if the model added one
        body = _FENCE_OPEN.sub("", body)
        body = _FENCE_CLOSE.sub("", body)
        if not body.endswith("\n"):
            body += "\n"
        files.append({"path": path, "content": body})
    return files


def _has_inline_format_error(content: str) -> bool:
    """Return True if a file body contains embedded ===FILE: delimiters.

    Occurs when the model emits one outer ===FILE: block but uses ===FILE:
    lines as separators inside it instead of closing with ===ENDFILE=== and
    opening a new block. The reviewer detects this and calls
    _recover_inline_files before scoring the PR.
    """
    return bool(re.search(r'^===FILE:\s*.+===\s*$', content, re.MULTILINE))


def _recover_inline_files(outer_path: str, outer_body: str) -> list[dict]:
    """Split a concatenated file body back into individual files.

    outer_path: repo-relative path of the file that received all content
    outer_body: its full text (the concatenated blob)
    Returns [{path, content}] for all recovered files.
    """
    body = re.sub(r'\n?===ENDFILE===\s*$', '', outer_body)
    parts = re.split(r'\n===FILE:\s*(.+?)\s*===\n', body)

    def _clean(path: str, text: str) -> dict:
        if "coder-repo/" in path:
            path = path.split("coder-repo/", 1)[1]
        path = path.lstrip("/")
        if not text.endswith("\n"):
            text += "\n"
        return {"path": path, "content": text}

    files = [_clean(outer_path, parts[0])]
    for i in range(1, len(parts) - 1, 2):
        files.append(_clean(parts[i], parts[i + 1]))
    return files


def _compose_task(title: str, payload: dict, last_error: str | None = None,
                  files_override: list | None = None) -> str:
    parts = [f"# Ticket\n{title}"]
    if last_error:
        parts.append(
            f"# Reviewer feedback from previous attempt — you MUST address this\n{last_error}"
        )
    obj = payload.get("objective") or payload.get("task")
    if obj:
        parts.append(f"# Objective\n{obj}")
    if payload.get("acceptance_criteria"):
        parts.append(
            "# Acceptance criteria\n"
            + "\n".join(f"- {c}" for c in payload["acceptance_criteria"])
        )
    if payload.get("constraints"):
        v = payload["constraints"]
        items = [v] if isinstance(v, str) else v
        parts.append("# Constraints\n" + "\n".join(f"- {c}" for c in items))
    if payload.get("out_of_scope"):
        v = payload["out_of_scope"]
        items = [v] if isinstance(v, str) else v
        parts.append("# Out of scope\n" + "\n".join(f"- {c}" for c in items))
    files = files_override if files_override is not None else (payload.get("files") or [])
    if files:
        parts.append("# Files to create or modify\n" + "\n".join(f"- {f}" for f in files))
    if last_error:
        parts.append(
            "Emit ===FILE=== blocks ONLY for the file(s) that address the reviewer feedback above. "
            "Skip files that need no changes."
        )
    else:
        parts.append(
            "Implement the ticket now. Emit every file in full using the "
            "===FILE: <path>=== / ===ENDFILE=== format and nothing else."
        )
    return "\n\n".join(parts)


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:40] or "change"


# Source globs used as default coder context when a ticket doesn't specify
# `context_files`. Small project → giving the whole tree is cheap and stops the
# coder hallucinating models/imports it can't see.
_CONTEXT_GLOBS = ("app/**/*.py", "tests/**/*.py", "pyproject.toml")


def _default_context(clone) -> list:
    rels = []
    for pat in _CONTEXT_GLOBS:
        for p in sorted(clone.glob(pat)):
            if "__pycache__" in p.parts or p.name.endswith(".pyc"):
                continue
            rels.append(str(p.relative_to(clone)))
    return rels


def build_ticket(ticket_id: int, title: str, payload: dict, attempt: int, workspace_id: str,
                 last_error: str | None = None) -> dict:
    """Build a ticket end-to-end and return {pr_url, model_used, files, cost_usd}.

    Raises on any failure; the caller (memory-mcp `_run_build`) marks the ticket
    `failed` so the reaper / attempt-counter can escalate or stop.
    """
    clone = ensure_clone(workspace_id, base="main")

    # Compute branch name early — needed for the context checkout below.
    branch = f"feat/t{ticket_id}-{_slug(title)}"

    if last_error:
        # Reviewer feedback means the previous attempt's branch has the flawed
        # implementation. Checkout that branch so the coder sees the existing code
        # and makes a targeted fix rather than rewriting from scratch.
        # open_pr always builds on the existing branch, so the PR diff against main
        # shows the complete set of files regardless of what the coder emits this run.
        ls = _git(clone, "ls-remote", "--heads", "origin", branch, network=True)
        if ls.stdout.strip():
            _git(clone, "fetch", "origin", branch, network=True)
            _git(clone, "checkout", branch)
            _git(clone, "reset", "--hard", f"origin/{branch}")
        else:
            _git(clone, "checkout", "main")
            _git(clone, "reset", "--hard", "origin/main")
    else:
        # Fresh attempt — always start from a clean main.
        _git(clone, "checkout", "main")
        _git(clone, "reset", "--hard", "origin/main")

    target = [str(f).lstrip("/") for f in (payload.get("files") or [])]

    # On retry: if the reviewer tagged specific files with [file.ext] in last_error,
    # scope the coder task to ONLY those files. The other original target files are
    # demoted to context_files so the coder can see them but won't re-emit them.
    # Without this, Qwen re-emits all N files unchanged when only one needs fixing,
    # producing a zero-diff commit that the "no changes to commit" guard rejects.
    if last_error:
        hints = _extract_file_hints(last_error)
        if hints:
            hint_set = set(hints)
            scoped = [
                t for t in target
                if t in hint_set or any(t.endswith("/" + h) for h in hint_set)
            ]
            if scoped:  # only restrict if hints matched at least one known target file
                target = scoped

    # Absolute paths so run_coder actually reads existing contents (the old bug:
    # relative paths resolved against the wrong CWD and were silently skipped).
    abs_paths = [str(clone / rel) for rel in target]

    # CONTEXT: a ticket's `files` are what it CREATES — not the existing code it
    # depends on. Without the surrounding source the coder hallucinates (#13 PR#4
    # invented a dataclass instead of using the real SQLAlchemy model). Feed it the
    # existing source tree: explicit `context_files` if planning set them, else the
    # whole small source tree, capped by a byte budget. (F89)
    # On a scoped retry the non-targeted original files also land here as context.
    ctx_rel = payload.get("context_files") or _default_context(clone)
    abs_set = set(abs_paths)
    context_abs, used, budget = [], 0, 60_000
    for rel in ctx_rel:
        p = clone / str(rel).lstrip("/")
        if not (p.exists() and p.is_file()) or str(p) in abs_set:
            continue
        sz = p.stat().st_size
        if used + sz > budget:
            continue
        used += sz
        context_abs.append(str(p))

    # Escalation ladder (attempt = previous_failures + 1):
    #   1,2: local Qwen reasoning OFF  3: local Qwen reasoning ON  4: Sonnet  5: Opus
    pf = max(0, attempt - 1)
    system = RETRY_SYSTEM if last_error else FULLFILE_SYSTEM
    task = _compose_task(f"#{ticket_id}: {title}", payload, last_error,
                         files_override=target if last_error else None)
    # Extracted so the no-FILE-blocks check can compare against the same value. (F90)
    max_tokens = int(os.environ.get("CODER_MAX_TOKENS", "16384"))
    coder = run_coder(
        task=task,
        files=abs_paths,
        context_files=context_abs,
        system_prompt=system,
        previous_failures=pf,
        disable_thinking=(pf < 2),
        # Qwen 3.6 reasons before answering and the chain-of-thought counts against
        # this budget; 8192 truncated the last (tests) file mid-stream. Streaming
        # removed the timeout risk, so give full-file generation real headroom. (F89)
        max_tokens=max_tokens,
        ticket_id=ticket_id,
    )

    files = _parse_files(coder["output"])
    if not files:
        out_tok = coder.get("tokens", {}).get("output", 0)
        if out_tok < max_tokens * 0.99:
            # Output well below ceiling: model dropped ===ENDFILE=== rather than
            # being truncated. CoderFormatError triggers a one-time format reminder
            # appended to last_error in server.py so the next attempt closes its
            # blocks. See exceptions.py for misclassification risks. (F90)
            raise CoderFormatError(out_tok, max_tokens)
        # At or near ceiling: output was truncated mid-stream. Plain RuntimeError —
        # the fix is more token budget, not a closing-marker reminder. (F90)
        raise RuntimeError(
            f"coder ({coder.get('model_used')}) output likely truncated at token "
            f"ceiling ({out_tok}/{max_tokens} tokens) — raise CODER_MAX_TOKENS"
        )

    # Strip .github/workflows/ files before committing.  GitHub requires the
    # `Actions:write` PAT scope (fine-grained) / `workflow` scope (classic PAT)
    # to push workflow files, which is separate from `contents:write`. Any
    # workflow files the coder generated are noted in the PR body so a human
    # can add them manually via the GitHub UI (which uses their own token).
    workflow_files = [f for f in files if f["path"].startswith(".github/workflows/")]
    files = [f for f in files if not f["path"].startswith(".github/workflows/")]
    if not files:
        raise RuntimeError(
            "All generated files were .github/workflows/ entries (PAT cannot push them). "
            "Re-scope the ticket to exclude CI/CD files."
        )

    pr_title = f"#{ticket_id}: {title}"[:68]
    body = payload.get("objective") or f"Implements ticket #{ticket_id}."
    if workflow_files:
        body += (
            "\n\n> **Note:** the following workflow files were generated but not pushed "
            "(requires `Actions:write` PAT scope — add them manually via the GitHub UI):\n"
            + "".join(f"> - `{f['path']}`\n" for f in workflow_files)
        )
    pr_url = open_pr(
        branch=branch,
        title=pr_title,
        body=body,
        files=files,
        cost_usd=coder.get("cost_usd", 0.0),
        model_used=coder.get("model_used", "unknown"),
        task_signature=coder.get("task_signature", f"ticket-{ticket_id}"),
        workspace_id=workspace_id,
    )
    return {
        "pr_url": pr_url,
        "model_used": coder.get("model_used"),
        "files": [f["path"] for f in files],
        "cost_usd": coder.get("cost_usd", 0.0),
    }
