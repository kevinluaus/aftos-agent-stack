-- One row per business workspace
CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    blueprint JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Tickets are units of work the agent acts on
CREATE TABLE tickets (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL CHECK (status IN ('open','running','blocked','review','done','failed','cancelled','escalation_pending')),
    priority INT DEFAULT 5,
    payload JSONB,
    pr_url TEXT,                          -- set when status='review' (PR open, awaiting human merge)
    parent_ticket_id BIGINT REFERENCES tickets(id),
    assigned_to TEXT,                    -- 'coder', 'research', 'scraper', 'orchestrator', etc.
    model_hint TEXT,                     -- 'local', 'sonnet', 'opus', or NULL
    attempts INT DEFAULT 0,
    last_error TEXT,
    error_history JSONB NOT NULL DEFAULT '[]',
    plan_session_id TEXT,                -- groups tickets from same planning session
    estimated_minutes INT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    deadline TIMESTAMPTZ,
    dependencies BIGINT[],               -- ticket IDs that must complete first
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_tickets_workspace_status ON tickets(workspace_id, status);
CREATE INDEX idx_tickets_open_priority ON tickets (workspace_id, status, priority DESC) WHERE status = 'open';
CREATE INDEX idx_tickets_running_started ON tickets (started_at) WHERE status = 'running';

-- Approvals queue for content drafts and external actions
CREATE TABLE approvals (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    ticket_id BIGINT REFERENCES tickets(id),
    action_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','expired')) DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by TEXT
);

CREATE INDEX idx_approvals_workspace_status ON approvals(workspace_id, status);

-- Capability gaps the agent reports when it can't proceed
CREATE TABLE capability_gaps (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    ticket_id BIGINT REFERENCES tickets(id),
    description TEXT NOT NULL,
    blocking BOOLEAN DEFAULT TRUE,
    resolved BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

-- Memory: episodic = conversation summaries, semantic = knowledge facts
-- 1024-dim matches BGE-M3 output. DO NOT migrate to 768; that was a stale nomic-era recommendation.
CREATE TABLE memory_episodic (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    embedding vector(1024),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_memory_episodic_workspace_session ON memory_episodic(workspace_id, session_id);
CREATE INDEX idx_memory_episodic_embedding ON memory_episodic
    USING hnsw (embedding vector_cosine_ops);

CREATE TABLE memory_semantic (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE CASCADE,
    source TEXT,
    content TEXT NOT NULL,
    embedding vector(1024),
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX uq_memory_semantic_ws_source ON memory_semantic(workspace_id, source);
CREATE INDEX idx_memory_semantic_workspace ON memory_semantic(workspace_id);
CREATE INDEX idx_memory_semantic_embedding ON memory_semantic
    USING hnsw (embedding vector_cosine_ops);

-- Structured logs (used from Stage 8 onward; created up-front so callers don't need a migration)
CREATE TABLE logs (
    id BIGSERIAL PRIMARY KEY,
    workspace_id TEXT,
    component TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('debug','info','warn','error','critical')),
    message TEXT NOT NULL,
    metadata JSONB,
    session_id TEXT,
    ticket_id BIGINT REFERENCES tickets(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_logs_workspace_created ON logs(workspace_id, created_at DESC);
CREATE INDEX idx_logs_component_created ON logs(component, created_at DESC);
CREATE INDEX idx_logs_level ON logs(level) WHERE level IN ('error','critical');

-- Coder specialist cost tracking (Stage 7.14)
-- Separate table from logs: coder needs structured cost columns, not JSONB metadata.
CREATE TABLE IF NOT EXISTS coder_calls (
    id                BIGSERIAL PRIMARY KEY,
    ts                TIMESTAMPTZ NOT NULL DEFAULT now(),
    workspace_id      TEXT NOT NULL,
    task_signature    TEXT,
    router_decision   TEXT,
    model_used        TEXT,
    prompt_tokens     INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    latency_ms        INTEGER,
    cost_usd          NUMERIC(10,6) DEFAULT 0,
    success           BOOLEAN DEFAULT TRUE,
    ticket_id         BIGINT REFERENCES tickets(id)
);

CREATE INDEX IF NOT EXISTS idx_coder_calls_ts
    ON coder_calls(ts DESC);
CREATE INDEX IF NOT EXISTS idx_coder_calls_workspace
    ON coder_calls(workspace_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_coder_calls_router
    ON coder_calls(router_decision);
