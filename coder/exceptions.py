"""Typed exceptions for the coder pipeline.

Caught by memory-mcp/server.py _run_build to decide how last_error is
updated before the next retry — preserving reviewer feedback through
infrastructure failures and appending targeted hints where useful. (F90)
"""


class CoderFormatError(RuntimeError):
    """The coder's output contained no parseable ===FILE=== blocks AND the
    completion token count was well below the max_tokens ceiling.

    Interpretation: the model dropped the ===ENDFILE=== terminator rather than
    being truncated. server.py appends a one-time format reminder to any
    existing reviewer feedback so the next attempt knows to close its blocks.

    RISK — refusal / garbage misclassification: a model refusal or incoherent
    output also produces low token counts, triggering this exception even though
    ===ENDFILE=== was never started. In practice refusals are extremely rare in
    the coder context (system prompt mandates FILE blocks), and the format
    reminder in last_error is harmless noise for Sonnet/Opus which never drop
    ===ENDFILE===. If spurious reminders appear on escalated attempts, check
    Langfuse for zero-content outputs rather than dropped terminators.

    RISK — LiteLLM token under-reporting: if the backend strips thinking tokens
    from completion_tokens (observed with some Ollama versions), a genuine
    truncation could fall below the 0.99 threshold and be misclassified here.
    Symptom: CoderFormatError fires on large-file tickets where output chars are
    near the ~16k token equivalent. If seen, raise CODER_MAX_TOKENS or patch the
    threshold; do not loosen the ===ENDFILE=== regex.
    """

    def __init__(self, completion_tokens: int, max_tokens: int):
        super().__init__(
            f"coder emitted no parseable ===FILE=== blocks; output was not near "
            f"token ceiling ({completion_tokens}/{max_tokens} tokens used) — "
            f"likely dropped ===ENDFILE=== terminator"
        )
        self.completion_tokens = completion_tokens
        self.max_tokens = max_tokens


class CoderPushError(RuntimeError):
    """Git push failed after successful code generation.

    The coder produced correct output and files were written to the clone, but
    the push to the remote branch failed. server.py catches this to neutralise
    the escalation-rung burn on the first occurrence (the code was fine; only
    the transport failed) and emits a 'push_failure' log event for the n8n
    health gate to detect.

    RISK — auth vs transient indistinguishable: a PAT expiry and a network
    blip both surface as CalledProcessError on push. The first failure is
    treated as transient (attempts-1 decrement, free retry). A second
    consecutive push failure stops the decrement and lets escalation proceed,
    which forces the n8n gate to trigger and alert ops. PAT rotation is the
    only manual recovery for expiry.

    RISK — attempts-1 negative: if attempts is somehow 0 at exception time
    (manual DB reset between claim and build), GREATEST(attempts-1, 0) keeps
    it at 0. The ticket re-enters the queue at pf=0 (Qwen no-think), which is
    correct — don't escalate for an infra failure.

    RISK — consecutive detection via last_error prefix: the "second consecutive
    push failure" path triggers when prev_error starts with "CoderPushError:".
    If a reviewer sets last_error to something starting with that string (which
    they should never do — reviewers write human-readable directives), the 2nd-
    consecutive path fires incorrectly. Mitigated by "CoderPushError:" being in
    _SYSEXC so stored push-error strings are never treated as human feedback.
    """

    def __init__(self, branch: str, stderr: str, return_code: int):
        super().__init__(
            f"git push failed for branch '{branch}' "
            f"(exit {return_code}): {stderr[:200] if stderr else 'no stderr'}"
        )
        self.branch = branch
        self.stderr = stderr
        self.return_code = return_code
