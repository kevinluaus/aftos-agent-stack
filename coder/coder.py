"""Coder specialist with five-rung routing (Qwen×2 no-think → Qwen think → Sonnet → Opus)."""
import os
import json
import time
from pathlib import Path

import httpx
from openai import OpenAI

LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "")
WORKSPACE_ID = os.environ.get("WORKSPACE_ID", "unknown")

# We STREAM the coder completion (see run_coder), so the relevant timeout is a
# per-read STALL, not a total-generation cap. Local Qwen produced correct code but
# the default 600s total timeout discarded it before the full response arrived
# (APITimeoutError despite success). With streaming, total output length / model
# speed are irrelevant — tokens just have to keep flowing within CODER_READ_TIMEOUT. (F89)
CODER_READ_TIMEOUT = float(os.environ.get("CODER_READ_TIMEOUT", "180"))
client = OpenAI(
    api_key=LITELLM_KEY,
    base_url=LITELLM_BASE,
    timeout=httpx.Timeout(connect=15.0, read=CODER_READ_TIMEOUT, write=30.0, pool=15.0),
    max_retries=0,
)


def run_coder(
    task: str,
    files: list | None = None,
    model: str | None = None,
    model_hint: str | None = None,
    previous_failures: int = 0,
    max_tokens: int = 4096,
    system_prompt: str | None = None,
    context_files: list | None = None,
    disable_thinking: bool = False,
    ticket_id: int | None = None,
) -> dict:
    """Run a coding task. Returns {output, model_used, latency_ms, tokens, task_signature, router_decision, cost_usd}."""
    from router import route, task_signature as _sig
    from cost_log import log_coder_call

    t0 = time.time()
    sig = _sig(task, files)

    if model is None:
        total_loc = 0
        for f in files or []:
            p = Path(f)
            if p.exists() and p.is_file():
                try:
                    total_loc += sum(1 for _ in p.open())
                except Exception:
                    pass
        model = route(
            task_text=task,
            file_count=len(files or []),
            total_loc=total_loc,
            model_hint=model_hint,
            previous_failures=previous_failures,
        )

    # Read target files (if they already exist) AND any context files, deduped.
    # Routing above uses only `files` so context never inflates the cost tier.
    file_context = ""
    chunks, seen = [], set()
    for f in (files or []) + (context_files or []):
        if f in seen:
            continue
        seen.add(f)
        p = Path(f)
        if p.exists() and p.is_file() and p.stat().st_size < 50_000:
            chunks.append(f"\n=== {f} ===\n{p.read_text()}\n")
    if chunks:
        file_context = (
            "\n\nExisting code (CONTEXT — match its style, imports, and models; only "
            "create/modify the files the task lists, do not rewrite these):\n" + "".join(chunks)
        )

    messages = [
        {
            "role": "system",
            "content": [
                {
                    "type": "text",
                    "text": system_prompt or (
                        "You are a senior engineer. Produce minimal, correct diffs. "
                        "Explain your plan in 2-3 sentences before any code. "
                        "Use unified diff format when modifying existing files."
                    ),
                    "cache_control": {"type": "ephemeral"},
                }
            ],
        },
        {
            "role": "user",
            "content": (
                ([{"type": "text", "text": file_context, "cache_control": {"type": "ephemeral"}}]
                 if file_context else [])
                # Qwen3 honours a `/no_think` soft switch in the prompt — used on the
                # first local rung so the whole token budget goes to code, not
                # chain-of-thought (which was truncating the last file). Ignored by
                # Sonnet/Opus, but disable_thinking is only set on the local rung. (F89)
                + [{"type": "text", "text": task + ("\n\n/no_think" if disable_thinking else "")}]
            ),
        },
    ]

    stream = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        stream=True,
        stream_options={"include_usage": True},
        extra_body={"metadata": {"workspace_id": WORKSPACE_ID, "specialist": "coder"}},
    )
    parts: list[str] = []
    usage = None
    model_used = model
    for chunk in stream:
        if getattr(chunk, "model", None):
            model_used = chunk.model
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if chunk.choices:
            delta = chunk.choices[0].delta
            if delta is not None and delta.content:
                parts.append(delta.content)
    output = "".join(parts)
    if not output.strip():
        raise RuntimeError(f"coder stream returned empty output from {model_used}")

    if usage is not None:
        usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage)
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
    else:
        usage_dict, in_tok, out_tok = {}, 0, 0

    cost = log_coder_call(
        workspace_id=WORKSPACE_ID,
        task_signature=sig,
        router_decision=model,
        model_used=model_used,
        usage=usage_dict,
        latency_ms=int((time.time() - t0) * 1000),
        success=True,
        ticket_id=ticket_id,
    )

    return {
        "output": output,
        "model_used": model_used,
        "latency_ms": int((time.time() - t0) * 1000),
        "tokens": {"input": in_tok, "output": out_tok},
        "task_signature": sig,
        "router_decision": model,
        "cost_usd": cost,
    }


if __name__ == "__main__":
    import sys
    task = sys.argv[1] if len(sys.argv) > 1 else "Write a Python function to reverse a string."
    result = run_coder(task)
    print(json.dumps(result, indent=2))
