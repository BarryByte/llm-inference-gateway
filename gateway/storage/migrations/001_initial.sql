-- Initial schema — run once on a fresh Postgres instance.
-- pgvector extension must be available (included in the Docker image).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE prompts (
    prompt_id   TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    text        TEXT NOT NULL,
    priority    TEXT NOT NULL DEFAULT 'normal',
    status      TEXT NOT NULL DEFAULT 'pending',
    embedding   vector(384),
    attempts    INT  NOT NULL DEFAULT 0,
    last_error  TEXT,
    callback_url TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE responses (
    prompt_id              TEXT PRIMARY KEY REFERENCES prompts(prompt_id),
    response_text          TEXT,
    model_tier             TEXT,
    tokens_in              INT,
    tokens_out             INT,
    cached_from_prompt_id  TEXT REFERENCES prompts(prompt_id),
    latency_ms             INT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE cache_entries (
    id              TEXT PRIMARY KEY,
    embedding       vector(384),
    response_text   TEXT,
    hit_count       INT  NOT NULL DEFAULT 0,
    last_hit_at     TIMESTAMPTZ,
    ttl_expires_at  TIMESTAMPTZ
);

CREATE TABLE dlq (
    prompt_id    TEXT PRIMARY KEY REFERENCES prompts(prompt_id),
    reason_chain JSONB,
    moved_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Indexes (see PRD §7)
CREATE INDEX idx_prompts_queue
    ON prompts (status, priority, created_at);

CREATE INDEX idx_prompts_inflight
    ON prompts (status)
    WHERE status IN ('pending', 'processing');

CREATE INDEX idx_cache_embedding
    ON cache_entries USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
