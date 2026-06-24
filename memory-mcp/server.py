import os, sys, json, threading, datetime, requests, psycopg2, psycopg2.extras
from contextlib import contextmanager
from typing import Optional
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, os.path.expanduser("~/agent-stack/specialists"))

PG_PASSWORD = os.environ["PG_PASSWORD"]
WORKSPACE_ID = os.environ["WORKSPACE_ID"]
EMBED_URL = os.environ.get("EMBED_URL", "http://localhost:11434/api/embeddings")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "bge-m3:latest")
MCP_HOST = os.environ.get("MCP_HOST", "172.18.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "8088"))

mcp = FastMCP("memory", host=MCP_HOST, port=MCP_PORT)

def embed(text: str) -> list:
    r = requests.post(EMBED_URL, json={"model": EMBED_MODEL, "prompt": text})
    r.raise_for_status()
    return r.json()["embedding"]

def conn():
    return psycopg2.connect(
        dbname="agentstack", user="agentstack",
        password=PG_PASSWORD, host="localhost",
    )


@contextmanager
def db(commit: bool = False, cursor_factory=None):
    """Fresh connection + cursor that is ALWAYS closed, and rolled back on error.

    Replaces the bare `c=conn(); cur=c.cursor(); ...; c.close()` pattern, which
    leaked a Postgres connection on any mid-call exception. commit=True commits on
    a clean exit; reads need no commit. On exception we roll back, then re-raise so
    the caller still sees the real error (no silent null). (F88)
    """
    c = conn()
    try:
        with c.cursor(cursor_factory=cursor_factory) as cur:
            yield cur
            if commit:
                c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

@mcp.tool()
def memory_search(query: str, k: int = 5) -> list:
    """Search semantic memory for facts relevant to a query."""
    qvec = embed(query)
    with db() as cur:
        cur.execute(
            "SELECT source, content, 1 - (embedding <=> %s::vector) AS sim "
            "FROM memory_semantic WHERE workspace_id = %s "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (qvec, WORKSPACE_ID, qvec, k),
        )
        rows = cur.fetchall()
    return [{"source": s, "content": cnt, "similarity": float(sim)} for s, cnt, sim in rows]

@mcp.tool()
def memory_store_episodic(session_id: str, summary: str) -> dict:
    """Store a session summary for future recall."""
    vec = embed(summary)
    with db(commit=True) as cur:
        cur.execute(
            "INSERT INTO memory_episodic (workspace_id, session_id, summary, embedding) "
            "VALUES (%s, %s, %s, %s::vector) RETURNING id",
            (WORKSPACE_ID, session_id, summary, vec),
        )
        row_id = cur.fetchone()[0]
    return {"id": row_id, "stored": True}

@mcp.tool()
def memory_recall_recent(session_id: Optional[str] = None, k: int = 5) -> list:
    """Recall the k most recent episodic memories."""
    with db() as cur:
        if session_id:
            cur.execute(
                "SELECT session_id, summary, created_at FROM memory_episodic "
                "WHERE workspace_id = %s AND session_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (WORKSPACE_ID, session_id, k),
            )
        else:
            cur.execute(
                "SELECT session_id, summary, created_at FROM memory_episodic "
                "WHERE workspace_id = %s ORDER BY created_at DESC LIMIT %s",
                (WORKSPACE_ID, k),
            )
        rows = cur.fetchall()
    return [{"session_id": s, "summary": sm, "created_at": str(ca)} for s, sm, ca in rows]

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")


@mcp.tool()
def research(topic: str, depth: int = 1) -> dict:
    """Research a topic using local Qwen + Tavily web search. Returns condensed findings.

    depth: 1=quick (3 sources), 2=standard (5 sources), 3=deep (8 sources).
    Use this instead of doing search-and-summarise yourself — the specialist runs on a local
    model and returns ~500 tokens of distilled summary plus sources.
    """
    os.environ.setdefault("TAVILY_API_KEY", TAVILY_API_KEY)
    from research import make_specialist as _mk  # noqa: PLC0415
    spec = _mk()
    if depth == 1:
        spec.max_iterations = 6
    elif depth == 2:
        spec.max_iterations = 10
    elif depth == 3:
        spec.max_iterations = 14
    return spec.run(topic, workspace_id=WORKSPACE_ID)


@mcp.tool()
def scrape(url: str, extraction_prompt: str) -> dict:
    """Extract structured data from a webpage using local Qwen.

    url: the page to scrape.
    extraction_prompt: what to extract, e.g. 'the price, name, and rating'.
    """
    from scraper import make_specialist as _mk  # noqa: PLC0415
    spec = _mk()
    task = f"From {url}, extract: {extraction_prompt}. Return only JSON."
    return spec.run(task, workspace_id=WORKSPACE_ID)


import sys as _sys
_sys.path.insert(0, os.path.expanduser("~/agent-stack/coder"))


@mcp.tool()
def code(
    task: str,
    files: list | None = None,
    model_hint: str | None = None,
    previous_failures: int = 0,
) -> dict:
    """Implement a coding task. Routes between local Qwen, Sonnet, and Opus automatically.

    task: what to implement, e.g. 'add a unit test for parse_date in utils.py'
    files: optional list of relevant file paths (informs routing and provides context)
    model_hint: optional override — 'local', 'sonnet', or 'opus'. Only use 'opus' for hard tasks.
    previous_failures: number of prior failed attempts on this ticket. Pass (attempt - 1) from
        claim_next_ticket(). 0 = first try (Qwen no-think). 1 = second try (Qwen no-think). 2 = third
        try (Qwen with reasoning). 3 = Sonnet. 4+ = Opus.
    """
    from coder import run_coder  # noqa: PLC0415
    return run_coder(task=task, files=files, model_hint=model_hint, previous_failures=previous_failures)


@mcp.tool()
def review_code(diff: str, context: str = "") -> dict:
    """Review a code change using local Qwen 3.6-27B with think=true.

    diff: unified diff of the change to review
    context: optional additional context (PR description, ticket, etc.)
    """
    from coder import run_coder  # noqa: PLC0415
    task = (
        "Review this diff for bugs, style issues, and missed edge cases. "
        "Be terse but specific. List issues by line number where possible.\n\n"
        f"Context: {context}\n\nDiff:\n{diff}"
    )
    return run_coder(task=task, model_hint="local")


@mcp.tool()
def open_pr(
    branch: str,
    title: str,
    body: str,
    files: list | None = None,
    cost_usd: float = 0.0,
    model_used: str = "unknown",
    task_signature: str = "ad-hoc",
) -> str:
    """Write files into this workspace's coder repo, commit to a branch, push, open a PR.

    branch: feature branch to create off main and open the PR from
    title: PR title (keep under 70 chars; also used as the commit message)
    body: PR description
    files: list of {"path": "<repo-relative path>", "content": "<full file content>"}
        — the final code to commit. Assemble these from the `code` tool's output.
    cost_usd: coder call cost in USD (from the code tool response)
    model_used: model that produced the change (from the code tool response)
    task_signature: task hash (from the code tool response)
    """
    from pr import open_pr as _open  # noqa: PLC0415
    return _open(
        branch, title, body,
        files=files, cost_usd=cost_usd,
        model_used=model_used, task_signature=task_signature,
    )


@mcp.tool()
def create_ticket(
    title: str,
    payload: dict,
    priority: int = 5,
    dependencies: list | None = None,
    model_hint: str | None = None,
    estimated_minutes: int = 15,
) -> dict:
    """Create a ticket in this workspace's queue.

    priority: 1 (urgent) to 10 (idle).
    dependencies: list of ticket IDs that must be done first.
    model_hint: 'local', 'sonnet', or 'opus' — omit to let the router decide.
    """
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute(
                """INSERT INTO tickets (workspace_id, title, status, priority,
                                       dependencies, model_hint, estimated_minutes, payload)
                   VALUES (%s, %s, 'open', %s, %s, %s, %s, %s)
                   RETURNING id, created_at""",
                (WORKSPACE_ID, title, priority, dependencies or [],
                 model_hint, estimated_minutes, json.dumps(payload)),
            )
            row = cur.fetchone()
    finally:
        c.close()
    return {"id": row[0], "created_at": row[1].isoformat(), "status": "open"}


@mcp.tool()
def list_tickets(status: str | None = None, limit: int = 50) -> list:
    """List tickets for this workspace. Valid statuses: open, running, blocked, review, done, failed, cancelled."""
    c = conn()
    try:
        with c, c.cursor() as cur:
            q = ("SELECT id, title, status, priority, attempts, last_error, created_at "
                 "FROM tickets WHERE workspace_id=%s")
            params: list = [WORKSPACE_ID]
            if status:
                q += " AND status=%s"
                params.append(status)
            q += " ORDER BY priority ASC, created_at ASC LIMIT %s"
            params.append(limit)
            cur.execute(q, params)
            rows = cur.fetchall()
    finally:
        c.close()
    return [{"id": r[0], "title": r[1], "status": r[2], "priority": r[3],
             "attempts": r[4], "last_error": r[5], "created_at": r[6].isoformat()}
            for r in rows]


@mcp.tool()
def get_ticket(ticket_id: int) -> dict:
    """Fetch full ticket details including payload."""
    c = conn()
    try:
        with c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tickets WHERE id=%s", (ticket_id,))
            row = cur.fetchone()
    finally:
        c.close()
    if not row:
        return {"error": "not found"}
    r = dict(row)
    r["id"] = int(r["id"])
    for f in ("created_at", "updated_at", "started_at", "completed_at", "deadline"):
        if r.get(f):
            r[f] = r[f].isoformat()
    return r


@mcp.tool()
def update_ticket_status(
    ticket_id: int,
    status: str,
    last_error: str | None = None,
    pr_url: str | None = None,
) -> dict:
    """Update ticket status. Valid: open, running, blocked, review, done, failed, cancelled.

    review: code executed and a PR is open, awaiting human review/merge. Set this (with
        pr_url) after a successful open_pr — do NOT self-mark 'done'. A human merge sets 'done',
        which unblocks dependent tickets.
    pr_url: the PR URL from open_pr (stored; kept if omitted on later updates).
    """
    valid = {"open", "running", "blocked", "review", "done", "failed", "cancelled", "escalation_pending"}
    if status not in valid:
        return {"error": f"must be one of {valid}"}
    history_entry = (
        json.dumps([{
            "error_type": "reviewer_feedback",
            "error": last_error[:480],
            "ts": datetime.datetime.utcnow().isoformat() + "Z",
        }])
        if last_error else None
    )
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute(
                "UPDATE tickets SET status=%s, "
                "last_error = CASE WHEN %s IS NOT NULL THEN %s ELSE last_error END, "
                "pr_url=COALESCE(%s, pr_url), "
                "error_history = CASE WHEN %s IS NOT NULL "
                "  THEN error_history || %s::jsonb ELSE error_history END, "
                "updated_at=now() WHERE id=%s",
                (status, last_error, last_error, pr_url, history_entry, history_entry, ticket_id),
            )
    finally:
        c.close()
    return {"id": ticket_id, "status": status, "pr_url": pr_url}


@mcp.tool()
def claim_next_ticket() -> dict | None:
    """Atomically claim the next runnable ticket (open, dependencies done, attempts < 6).

    Uses FOR UPDATE SKIP LOCKED to prevent double-claiming across workspaces.
    Returns None if no runnable ticket exists.
    """
    c = conn()
    try:
        with c, c.cursor() as cur:
            cur.execute(
                """SELECT id, title, payload, model_hint, estimated_minutes, attempts
                   FROM tickets
                   WHERE workspace_id=%s
                     AND status='open'
                     AND attempts < 6
                     AND NOT EXISTS (
                       SELECT 1 FROM unnest(dependencies) dep
                       JOIN tickets dep_t ON dep_t.id = dep
                       WHERE dep_t.status != 'done'
                     )
                   ORDER BY priority ASC, created_at ASC
                   LIMIT 1
                   FOR UPDATE SKIP LOCKED""",
                (WORKSPACE_ID,),
            )
            r = cur.fetchone()
            if not r:
                return None
            cur.execute(
                """UPDATE tickets SET status='running', attempts=attempts+1,
                   started_at=now(), updated_at=now() WHERE id=%s""",
                (r[0],),
            )
        c.commit()
    finally:
        c.close()
    return {"id": r[0], "title": r[1], "payload": r[2], "model_hint": r[3],
            "estimated_minutes": r[4], "attempt": r[5] + 1}


@mcp.tool()
def log_event(
    component: str,
    level: str,
    message: str,
    ticket_id: int | None = None,
    session_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Write a structured log entry visible in the Grafana system overview dashboard.

    component: who is logging — 'orchestrator', 'coder', 'research', 'scraper', 'system'
    level: 'debug' | 'info' | 'warn' | 'error' | 'critical'
    message: what happened, in one sentence
    ticket_id: ticket being worked on (from claim_next_ticket response)
    session_id: optional — group log entries across a session
    metadata: optional dict of extra context (e.g. {'attempt': 2, 'model': 'sonnet-coder'})
    """
    valid = {'debug', 'info', 'warn', 'error', 'critical'}
    if level not in valid:
        return {"error": f"level must be one of {valid}"}
    row = _insert_log(component, level, message, ticket_id, session_id, metadata)
    return {"id": row[0], "created_at": row[1].isoformat()}


def _insert_log(component, level, message, ticket_id=None, session_id=None, metadata=None):
    """Insert one logs row. Shared by the log_event tool and the background builder."""
    with db(commit=True) as cur:
        cur.execute(
            """INSERT INTO logs
               (workspace_id, component, level, message, ticket_id, session_id, metadata)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               RETURNING id, created_at""",
            (WORKSPACE_ID, component, level, message, ticket_id, session_id,
             json.dumps(metadata) if metadata else None),
        )
        return cur.fetchone()


def _heartbeat_loop(ticket_id: int, stop: "threading.Event") -> None:
    """Bump updated_at every 60s while a build runs. Lets the n8n reaper tell a
    live-but-slow Qwen build (fresh heartbeat) from a dead one (stale heartbeat),
    so it never requeues work that's still in flight. (F89)"""
    while not stop.wait(60):
        try:
            with db(commit=True) as cur:
                cur.execute(
                    "UPDATE tickets SET updated_at=now() WHERE id=%s AND status='running'",
                    (ticket_id,),
                )
        except Exception:
            pass


def _run_build(ticket_id: int) -> None:
    """Background worker: build a claimed code ticket end-to-end and set its final
    status. Runs in a daemon thread so the slow coder call never blocks the MCP
    request thread (which previously crashed the whole memory MCP) or the
    orchestrator's turn. Owns commit + PR + status — the orchestrator does not.
    A heartbeat thread keeps updated_at fresh so the reaper won't double-fire. (F89)
    """
    _hb_stop = threading.Event()
    threading.Thread(target=_heartbeat_loop, args=(ticket_id, _hb_stop), daemon=True).start()
    row = None  # initialise so the except block can safely read row["last_error"]
    try:
        with db(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, title, payload, attempts, last_error, model_hint FROM tickets "
                "WHERE id=%s AND workspace_id=%s",
                (ticket_id, WORKSPACE_ID),
            )
            row = cur.fetchone()
        if not row:
            return

        # Pre-flight escalation gate (F113 postmortem): if this run would route to
        # Sonnet or Opus (attempts >= 4 → previous_failures >= 3), require explicit
        # approval via /approve before making the cloud API call.
        #
        # Without this gate, phantom attempts from llama's exec()-vs-execute_ticket()
        # confusion (or any other bug that burns attempts without a real build) can
        # silently promote a ticket to Sonnet. The n8n escalation probe only fires
        # AFTER the failure; this gate intercepts BEFORE the API call.
        #
        # Bypass: a non-null model_hint ('sonnet'/'opus') is an explicit operator
        # override and skips the gate. escalation_approved log entries (written by
        # n8n /approve and auto-approve) also satisfy the gate.
        if row["attempts"] >= 4 and not row.get("model_hint"):
            with db(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT 1 FROM logs WHERE component='escalation_approved' "
                    "AND ticket_id=%s LIMIT 1",
                    (ticket_id,),
                )
                approved = cur.fetchone()
            if not approved:
                with db(commit=True) as cur:
                    cur.execute(
                        "UPDATE tickets SET status='escalation_pending', updated_at=now() "
                        "WHERE id=%s AND status='running'",
                        (ticket_id,),
                    )
                _insert_log(
                    "escalation_pending", "warn",
                    f"pre-flight gate: ticket #{ticket_id} would route to Sonnet/Opus "
                    f"(attempts={row['attempts']}) — awaiting /approve",
                    ticket_id,
                )
                return  # finally block stops the heartbeat

        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        from build import build_ticket  # noqa: PLC0415
        res = build_ticket(ticket_id, row["title"], payload, row["attempts"], WORKSPACE_ID,
                           last_error=row.get("last_error"))
        with db(commit=True) as cur:
            cur.execute(
                "UPDATE tickets SET status='review', pr_url=%s, last_error=NULL, updated_at=now() WHERE id=%s",
                (res["pr_url"], ticket_id),
            )
        _insert_log(
            "coder", "info",
            f"ticket #{ticket_id} built -> review ({res.get('model_used')}): {res['pr_url']}",
            ticket_id, None,
            {"model": res.get("model_used"), "files": res.get("files"), "cost_usd": res.get("cost_usd")},
        )
    except Exception as e:
        import subprocess as _sp
        msg = f"{type(e).__name__}: {e}"
        if isinstance(e, _sp.CalledProcessError) and e.stderr:
            msg = f"{type(e).__name__} (stderr: {e.stderr[:300]}): {e}"
        try:
            # Reopen for an escalated retry (Qwen×2→Qwen-think→Sonnet→Opus) on a build
            # error; only terminal once attempts are exhausted. A timeout/transient fault
            # must NOT bury the ticket in 'failed' (the processor never re-fires it). (F89)
            #
            # Preserve reviewer feedback in last_error through infrastructure failures.
            # last_error serves two roles: (a) reviewer-written fix instructions for the coder,
            # (b) system error strings for debugging. Infrastructure exceptions (git push
            # failures, timeouts, no-diff, subprocess errors) must NOT overwrite role-(a)
            # content, or the next attempt receives garbage as its "reviewer feedback."
            #
            # Detection: system errors always start with a Python exception class name
            # ("CalledProcessError:", "RuntimeError:", "ValueError:", etc.). Human reviewer
            # feedback (e.g. "[pyproject.toml] alembic missing") never does. So: if prev_error
            # is human-written, preserve it regardless of what the current exception is.
            prev_error = row.get("last_error") if row else None
            # Both typed exceptions must be listed so stored system-error strings
            # are never mistaken for human reviewer feedback on a subsequent attempt.
            # CoderFormatError (F90), CoderPushError (F95).
            _SYSEXC = ("CalledProcessError:", "RuntimeError:", "ValueError:", "TypeError:",
                       "AttributeError:", "KeyError:", "OSError:", "IOError:",
                       "CoderFormatError:", "CoderPushError:")
            prev_is_human = prev_error and not any(prev_error.startswith(p) for p in _SYSEXC)

            from exceptions import CoderFormatError as _CFE, CoderPushError as _CPE  # noqa: PLC0415

            # Determine (new_last_error, attempts_delta) for the unified UPDATE below.
            #
            # new_last_error=None  → CASE preserves existing last_error column value
            # new_last_error=str   → CASE overwrites last_error with the new value
            # attempts_delta=-1    → GREATEST(attempts-1, 0): neutralise rung burn
            # attempts_delta=0     → attempts unchanged (escalation proceeds normally)
            #
            # Four branches (F90 / F95):
            #  P1. CoderPushError + human reviewer feedback
            #      Code was fine; push failed. Preserve reviewer directive, neutralise rung.
            #  P2. CoderPushError + prev_error already a push failure (2nd consecutive)
            #      Stop neutralising — let escalation proceed and force the n8n gate to
            #      trigger. Store the new error so the operator sees the latest stderr.
            #  P3. CoderPushError + no prior human feedback (first push failure)
            #      Store the error for debugging, neutralise rung.
            #  A.  CoderFormatError + human feedback + reminder not yet present
            #      Append one-time format reminder to reviewer directive. (F90)
            #  B.  Any other exception + human feedback
            #      Preserve reviewer directive unchanged.
            #  C.  No human feedback → overwrite with system error string.

            _FORMAT_REMINDER = (
                "\n[FORMAT: previous attempt omitted ===ENDFILE=== — "
                "every ===FILE: path=== block MUST be closed with ===ENDFILE=== "
                "on its own line]"
            )

            if isinstance(e, _CPE):
                # Emit a distinct log event so the n8n push-failure gate can query it.
                _insert_log("push_failure", "error",
                            f"ticket #{ticket_id} push failed — branch '{e.branch}', "
                            f"exit {e.return_code}: {e.stderr[:200] if e.stderr else 'no stderr'}",
                            ticket_id,
                            metadata={"branch": e.branch, "return_code": e.return_code})
                if prev_is_human:
                    # P1: preserve reviewer feedback, free retry (push failure, not code failure)
                    new_last_error = None
                    attempts_delta = -1
                    error_action = "reviewer feedback preserved (push failure, retry neutral)"
                elif prev_error and prev_error.startswith("CoderPushError:"):
                    # P2: 2nd consecutive push failure — stop neutralising, alert will fire
                    new_last_error = msg[:480]
                    attempts_delta = 0
                    error_action = "push failure 2nd consecutive — escalating, n8n gate will alert"
                else:
                    # P3: first push failure, no human feedback — free retry
                    new_last_error = msg[:480]
                    attempts_delta = -1
                    error_action = "push failure 1st — retry without escalation"
            elif isinstance(e, _CFE) and prev_is_human and "===ENDFILE===" not in prev_error:
                # A: append format reminder once; dedup prevents stacking (F90)
                new_last_error = prev_error + _FORMAT_REMINDER
                attempts_delta = 0
                error_action = "format reminder appended to reviewer feedback"
            elif prev_is_human:
                # B: any other infra failure — preserve reviewer directive
                new_last_error = None
                attempts_delta = 0
                error_action = "reviewer feedback preserved"
            else:
                # C: no human feedback — overwrite with system error
                new_last_error = msg[:480]
                attempts_delta = 0
                error_action = "updated"

            # Unified UPDATE: single SQL handles all cases.
            # GREATEST guard prevents attempts going negative on a manual-reset ticket. (F95)
            # CASE for last_error: NULL param → preserve existing value (no column change).
            # error_history always appended — every build failure is recorded. (F101)
            build_history_entry = json.dumps([{
                "attempt": row["attempts"] if row else 0,
                "error_type": type(e).__name__,
                "error": msg[:480],
                "ts": datetime.datetime.utcnow().isoformat() + "Z",
            }])
            with db(commit=True) as cur:
                cur.execute(
                    "UPDATE tickets SET "
                    "  status        = CASE WHEN attempts >= 6 THEN 'failed' ELSE 'open' END, "
                    "  attempts      = GREATEST(attempts + %s, 0), "
                    "  last_error    = CASE WHEN %s IS NOT NULL THEN %s ELSE last_error END, "
                    "  error_history = error_history || %s::jsonb, "
                    "  updated_at    = now() "
                    "WHERE id = %s",
                    (attempts_delta, new_last_error, new_last_error, build_history_entry, ticket_id),
                )
            _insert_log("coder", "error",
                        f"ticket #{ticket_id} build failed (will retry if attempts<6; {error_action}): {msg[:200]}",
                        ticket_id)
        except Exception:
            pass
    finally:
        _hb_stop.set()


@mcp.tool()
def execute_ticket(ticket_id: int) -> dict:
    """Build a CLAIMED code ticket end-to-end in the BACKGROUND and ship a PR.

    Reads the workspace's coder repo for real context, runs the coder (local Qwen
    -> Sonnet -> Opus by attempt), opens a PR, and moves the ticket to 'review' (or
    'failed' on error) ITSELF. Returns IMMEDIATELY — do NOT wait, poll, retry, or
    update the ticket yourself. Call this exactly once for a claimed code ticket,
    then end your turn. The background job owns the commit, the PR, and the status.
    """
    # Server-side claim log — reliable even when the orchestrator skips log_event.
    # This ensures every dispatch appears in Grafana/logs regardless of model
    # instruction-following quality (llama 3.1 skips log_event intermittently).
    try:
        with db(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT title FROM tickets WHERE id=%s AND workspace_id=%s",
                (ticket_id, WORKSPACE_ID),
            )
            t = cur.fetchone()
        if t:
            _insert_log("orchestrator", "info",
                        f"claimed ticket #{ticket_id}: {t['title']}", ticket_id)
    except Exception:
        pass  # logging failure must never block dispatch
    threading.Thread(target=_run_build, args=(ticket_id,), daemon=True).start()
    return {
        "status": "dispatched",
        "ticket_id": ticket_id,
        "note": ("Coder is building in the background. The ticket will move to 'review' "
                 "(PR opened) on success or 'failed' on error. Do NOT wait, retry, or run "
                 "diagnostics — end your turn now."),
    }


@mcp.tool()
def dispatch_next_ticket() -> dict:
    """Claim the next eligible ticket and dispatch it — one atomic call.

    Call this exactly once when you receive [EXECUTE NEXT]. Do NOT call
    claim_next_ticket or execute_ticket separately — this replaces both.

    Returns one of:
      {"status": "no_work"}
          Queue is empty or no runnable ticket. End your turn.
      {"status": "dispatched", "ticket_id": N, "title": "..."}
          Code ticket fired in background. End your turn — do NOT wait, poll, or retry.
      {"status": "research_needed", "ticket": {...}}
          Non-code ticket (no files list). Handle it yourself: memory_search,
          do the research work, update_ticket_status(id, 'done'), log_event, memory_store_episodic.
    """
    ticket = claim_next_ticket()
    if ticket is None:
        return {"status": "no_work"}
    payload = ticket.get("payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if payload.get("files"):
        execute_ticket(ticket["id"])
        return {"status": "dispatched", "ticket_id": ticket["id"], "title": ticket["title"]}
    return {"status": "research_needed", "ticket": ticket}


def _admin_server() -> None:
    """Background HTTP server for operational commands (session reset, etc.).

    Listens on MCP_PORT+100 (MCP_PORT+100 per workspace — e.g. 8188, 8189, 8190).
    Endpoints:
      POST /admin/reset-session  — archive the agent:main:main session file and
                                   replace it with a minimal header-only stub.
                                   Preserves the session UUID + registration so
                                   the gateway never re-enables heartbeat.
                                   Telegram sessions are untouched.
    """
    import http.server as _http
    import subprocess as _sp

    admin_port = MCP_PORT + 100

    class _Handler(_http.BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/admin/reset-session":
                self._send(404, {"error": "not found"})
                return
            ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S.000Z")
            try:
                r = _sp.run(
                    ["docker", "ps", "--filter", f"name={WORKSPACE_ID}",
                     "--format", "{{.Names}}"],
                    capture_output=True, text=True, timeout=10,
                )
                container = r.stdout.strip()
                if not container:
                    raise RuntimeError(f"no running container found for {WORKSPACE_ID}")
                # Read sessions.json to find the agent:main:main session UUID.
                # We target ONLY that entry so Telegram sessions are never disturbed
                # and the session registration stays intact (prevents heartbeat re-enable).
                SESSIONS_JSON = "/sandbox/.openclaw/agents/main/sessions/sessions.json"
                r2 = _sp.run(
                    ["docker", "exec", container, "cat", SESSIONS_JSON],
                    capture_output=True, text=True, timeout=10,
                )
                sdata = json.loads(r2.stdout) if r2.stdout.strip() else {}
                entry = sdata.get("agent:main:main", {})
                session_id = entry.get("sessionId", "")
                if not session_id:
                    raise RuntimeError("agent:main:main session not found in sessions.json")
                SESSIONS_DIR = "/sandbox/.openclaw/agents/main/sessions"
                session_file = f"{SESSIONS_DIR}/{session_id}.jsonl"
                archive_file = f"{SESSIONS_DIR}/{session_id}.jsonl.reset.{ts}"
                # Archive the current (bloated) session file
                _sp.run(["docker", "exec", container, "mv", session_file, archive_file],
                        check=True, timeout=10)
                # Warm stub: 2-scenario in-context examples for dispatch_next_ticket.
                # Scenario A: empty queue → no_work → end turn.
                # Scenario B: code ticket → dispatched → end turn.
                # One tool call per scenario (was 2 — claim + execute — which caused llama
                # to confuse execute_ticket with the shell exec tool).
                # content: [] (not null) on tool-call assistant lines — the gateway
                # iterates over content and crashes with "not iterable" if null.
                now_iso = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
                seed_ts = "2026-06-23T00:00:00.000Z"
                sid8 = session_id[:8]
                warm_lines = [
                    # session header
                    json.dumps({"type": "session", "version": 3, "id": session_id,
                                "timestamp": now_iso, "cwd": "/sandbox/.openclaw/workspace"}),
                    # Scenario A — empty queue
                    json.dumps({"type": "message", "id": f"sa-u-{sid8}",
                                "parentId": f"sa-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "user",
                                            "content": [{"type": "text", "text": "[EXECUTE NEXT]"}],
                                            "timestamp": seed_ts}}),
                    json.dumps({"type": "message", "id": f"sa-a1-{sid8}",
                                "parentId": f"sa-u-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "assistant", "content": [],
                                            "tool_calls": [{"function": {
                                                "name": "memory__dispatch_next_ticket",
                                                "arguments": "{}"}, "id": f"sa-tc1-{sid8}",
                                                "type": "function"}]}}),
                    json.dumps({"type": "message", "id": f"sa-tr1-{sid8}",
                                "parentId": f"sa-a1-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "tool",
                                            "content": [{"type": "text",
                                                         "text": "{\"status\": \"no_work\"}"}],
                                            "tool_call_id": f"sa-tc1-{sid8}"}}),
                    json.dumps({"type": "message", "id": f"sa-a2-{sid8}",
                                "parentId": f"sa-tr1-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "assistant",
                                            "content": [{"type": "text",
                                                         "text": "no work available"}]}}),
                    # Scenario B — code ticket dispatch via dispatch_next_ticket (single call)
                    json.dumps({"type": "message", "id": f"sb-u-{sid8}",
                                "parentId": f"sb-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "user",
                                            "content": [{"type": "text", "text": "[EXECUTE NEXT]"}],
                                            "timestamp": seed_ts}}),
                    json.dumps({"type": "message", "id": f"sb-a1-{sid8}",
                                "parentId": f"sb-u-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "assistant", "content": [],
                                            "tool_calls": [{"function": {
                                                "name": "memory__dispatch_next_ticket",
                                                "arguments": "{}"}, "id": f"sb-tc1-{sid8}",
                                                "type": "function"}]}}),
                    json.dumps({"type": "message", "id": f"sb-tr1-{sid8}",
                                "parentId": f"sb-a1-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "tool",
                                            "content": [{"type": "text",
                                                         "text": "{\"status\": \"dispatched\", \"ticket_id\": 999, \"title\": \"example task\"}"}],
                                            "tool_call_id": f"sb-tc1-{sid8}"}}),
                    json.dumps({"type": "message", "id": f"sb-a2-{sid8}",
                                "parentId": f"sb-tr1-{sid8}", "timestamp": seed_ts,
                                "message": {"role": "assistant",
                                            "content": [{"type": "text",
                                                         "text": "dispatched ticket #999"}]}}),
                ]
                stub_content = "\\n".join(warm_lines) + "\\n"
                # Write as sandbox user (uid 998) so the gateway can append turns.
                # docker exec without --user runs as root → root-owned file →
                # gateway (uid 998) can't write → all turns fail silently.
                _sp.run(
                    ["docker", "exec", "--user", "998", container,
                     "bash", "-c",
                     f"printf '%s' {json.dumps(stub_content)} > {session_file}"],
                    check=True, timeout=10,
                )
                _insert_log("system", "info",
                            f"session reset: archived {session_id} → fresh header written (ts={ts})")
                self._send(200, {"status": "ok", "workspace": WORKSPACE_ID,
                                 "session_id": session_id, "ts": ts})
            except Exception as e:
                self._send(500, {"status": "error", "message": str(e)})

        def _send(self, code: int, data: dict) -> None:
            body = json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass  # suppress default access log noise

    _http.HTTPServer((MCP_HOST, admin_port), _Handler).serve_forever()


if __name__ == "__main__":
    threading.Thread(target=_admin_server, daemon=True).start()
    mcp.run(transport="streamable-http")
