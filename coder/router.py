"""Deterministic coder router — same inputs, same output every time."""
import hashlib

HARD_KEYWORDS = (
    "race condition", "concurrency", "deadlock", "memory leak", "performance regression",
    "security vulnerability", "exploit", "crypto", "consensus", "distributed transaction",
    "migrate database schema", "refactor entire", "architectural",
)

MEDIUM_KEYWORDS = (
    "refactor", "design", "architecture", "trade-off", "review the",
    "why is", "explain why", "find the bug",
)

LOCAL_FRIENDLY_KEYWORDS = (
    "add a test", "write a test", "stub", "comment", "rename", "format",
    "lint fix", "type hint", "docstring", "import", "dependency bump",
)


def task_signature(task_text: str, files: list | None) -> str:
    h = hashlib.sha256()
    h.update(task_text.strip().lower().encode())
    for f in sorted(files or []):
        h.update(f.encode())
    return h.hexdigest()[:16]


def route(
    task_text: str,
    file_count: int = 0,
    total_loc: int = 0,
    model_hint: str | None = None,
    previous_failures: int = 0,
) -> str:
    """Return the LiteLLM coder alias. Deterministic — same inputs, same output.

    Escalation ladder by attempt (attempt = previous_failures + 1):
      1: local Qwen, reasoning OFF    2: local Qwen, reasoning OFF (2nd minor-fix try)
      3: local Qwen, reasoning ON     4: Sonnet    5+: Opus
    Rungs 1-3 all return 'local-coder'; the caller (build.py `disable_thinking`) controls
    reasoning off (pf < 2) vs on (pf == 2). An explicit `model_hint` always overrides the
    ladder — use 'sonnet'/'opus' for major failures to skip the local-Qwen retry queue.
    """
    if model_hint == "opus":
        return "opus-coder"
    if model_hint == "sonnet":
        return "sonnet-coder"
    if model_hint == "local":
        return "local-coder"

    if previous_failures <= 2:
        return "local-coder"
    if previous_failures == 3:
        return "sonnet-coder"
    return "opus-coder"


if __name__ == "__main__":
    import sys
    print(route(sys.argv[1] if len(sys.argv) > 1 else "add a test for foo()"))
