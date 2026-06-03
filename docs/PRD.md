# LLM Inference Gateway — Product Requirements

## 1. What this is

A self-hosted gateway that sits between client apps and one or more LLM providers.

It exists because direct, synchronous calls to LLM APIs break down under real load:
requests pile up, providers throttle me, identical prompts get billed twice, and a
single crash loses in-flight work. This gateway is the layer that absorbs those problems.

Think of it as a smaller, transparent version of what tools like LiteLLM, Portkey, or
OpenRouter do — but built from primitives so every layer is understandable.

## 2. Goals

1. Accept prompt requests over HTTP and process them reliably, even under bursty load.
2. Never exceed an upstream provider's rate limit (default: 300 calls / 60s).
3. Skip the LLM entirely when a semantically similar prompt has already been answered.
4. Survive crashes — restart and resume in-flight work without losing or duplicating it.
5. Route prompts to the cheapest model that can handle them.
6. Expose enough metrics that I can actually watch it work.

## 3. Non-goals

- Multi-region replication.
- A web UI for end users (an ops dashboard via Grafana is fine).
- Fine-tuning, RAG pipelines, or agent orchestration. This is a gateway, not a platform.
- Real LLM provider integration in v1 — a mock provider stands in.

## 4. Architecture overview

```
 Client
   │
   ▼
 HTTP API (FastAPI)
   │
   ▼
 Smart Router ──► Complexity Classifier (local, Ollama)
   │
   ├─► Semantic Cache (vector similarity)
   │
   ▼
 Priority Queue (Redis sorted sets, custom)
   │
   ▼
 Worker Pool ──► Rate Limiter ──► Mock LLM Provider
   │
   ▼
 Result Store (Postgres)  +  WAL (append-only log)
                          +  Dead-letter Queue
```

Every box is something I can open, read, and reason about. No black-box workflow
engine.

## 5. Core features

### 5.1 HTTP API

**`POST /process`** — submit a prompt.

```json
{
  "user_id": "u1",
  "prompt_id": "p1",
  "text": "Explain quantum computing simply",
  "priority": "high",
  "callback_url": "https://app.example.com/hooks/llm"
}
```

- `priority` is one of `high | normal | low`. Default `normal`.
- `callback_url` is optional. If set, the response is POSTed back when ready
  and the original call returns immediately with `status: "queued"`.
- Same `prompt_id` + same `text` is idempotent — returns the existing result.

**`POST /process/stream`** — same payload, but the response body streams tokens
back over Server-Sent Events. Useful for chat-style clients.

**`POST /batch`** — submit up to 100 prompts in one call. Returns one result array.

**`GET /jobs/{prompt_id}`** — fetch the current status and result.

**`GET /health`** — liveness for load balancers (db, queue, cache, workers).

**`GET /metrics`** — Prometheus-format metrics (see §5.7).

### 5.2 Semantic cache

- Every incoming prompt gets embedded.
- Cosine similarity against existing embeddings; threshold defaults to 0.92.
- Cache hit returns the stored response and increments `hit_count`.
- Eviction is hybrid: LRU first, then importance-weighted (entries with high
  `hit_count` are protected). TTL is configurable per entry.
- Threshold and TTL are tunable at runtime via env vars — no redeploy.

Embeddings come from `sentence-transformers` (default `all-MiniLM-L6-v2`).
A deterministic mock embedder is available for tests.

### 5.3 Smart router (cost-based routing)

Before queuing, the router decides which model tier should handle the prompt:

| Tier   | Used for                                    | Cost weight |
|--------|---------------------------------------------|-------------|
| small  | factual lookups, short rewrites, classify    | 1×          |
| medium | summaries, structured extraction             | 4×          |
| large  | reasoning, long generation, code            | 15×         |

Complexity is scored by a tiny local model (Ollama, e.g. `qwen2.5:0.5b`) plus
heuristics (token count, presence of code blocks, keywords like "step by step").
The router's decision is logged with every request so I can audit it later.

### 5.4 Priority queue (custom)

Built on Redis sorted sets, not Celery or a black-box library.

- Three priority bands: `high`, `normal`, `low`.
- Within a band, FIFO via timestamp tiebreaker.
- Starvation guard: low-priority jobs get bumped up if they've waited longer
  than `MAX_WAIT_SECONDS` (default 60s).
- Task states: `pending → claimed → processing → done | failed | retrying`.
- Visibility timeout: if a worker claims a task and doesn't ack within N seconds,
  it returns to `pending`. This is how crash recovery actually works.

### 5.5 Rate limiting

Two layers:

1. **Global provider limit** — sliding window over Redis, default 300/min.
   Enforced by the worker before it calls the LLM.
2. **Per-user limit** — optional, configurable. Defaults to off.

When the global limit is saturated, workers pause rather than failing. Clients see
queue depth grow but no errors.

### 5.6 Crash recovery (WAL)

Every state transition (`claimed`, `processing`, `done`, `failed`) is appended
to a write-ahead log on disk before the in-memory state changes.

On startup:

1. Read the WAL.
2. Replay transitions to rebuild current state.
3. Any task left in `claimed` or `processing` is marked `pending` and re-queued.
4. After three failures, a task moves to the dead-letter queue with the reason
   chain.

The WAL is rotated daily and old segments are compacted. Postgres holds the
canonical result store; the WAL only covers the in-flight window.

### 5.7 Observability

- Structured JSON logs with `prompt_id`, `user_id`, `route_decision`, `cache_hit`,
  `attempt`, and `latency_ms` on every record.
- Prometheus metrics: queue depth per band, cache hit rate, worker saturation,
  rate-limiter wait time, p50/p95/p99 latency, DLQ size, cost-per-minute by tier.
- Prebuilt Grafana dashboard ships in the repo. `docker-compose up` brings it up
  alongside the gateway.
- Prometheus + Grafana ships in Docker Compose. OpenTelemetry is the upgrade path
  for production — same instrumentation points, swap the exporter.

### 5.8 Back-pressure

If queue depth exceeds `MAX_QUEUE_DEPTH`, the API returns `503` with a
`Retry-After` header instead of accepting more work. Better to push the load
back to the caller than to drop it silently or run out of memory.

## 6. Mock LLM provider

A `MockLLM` class with realistic constraints:

- 200–500ms randomized latency.
- 5% random failure rate (raises `ProviderError`).
- Internal rate limit (300/min) — independent of the gateway's limiter, so we
  can verify the gateway actually respects it.
- Deterministic responses for a fixed set of prompts so cache tests are stable.

No real API keys, ever.

## 7. Data model (Postgres)

```
prompts(prompt_id PK, user_id, text, priority, status, created_at, updated_at,
        embedding vector(384), attempts, last_error)

responses(prompt_id FK, response_text, model_tier, tokens_in, tokens_out,
          cached_from_prompt_id NULL, latency_ms, created_at)

cache_entries(id PK, embedding vector(384), response_text, hit_count,
              last_hit_at, ttl_expires_at)

dlq(prompt_id PK, reason_chain JSONB, moved_at)
```

`pgvector` powers similarity search. Indexes:

- `prompts(status, priority, created_at)` for queue scans.
- `cache_entries USING ivfflat (embedding vector_cosine_ops)` for similarity.
- Partial index on `prompts(status) WHERE status IN ('pending', 'processing')`
  keeps the hot path small even after millions of completed rows.

## 8. Stack

| Layer        | Choice                           | Why                                            |
|--------------|----------------------------------|------------------------------------------------|
| Language     | Python 3.11                      | Async story is good enough; ecosystem fits.   |
| API          | FastAPI + Uvicorn                | Async-native, fast, clean dependency model.   |
| Queue        | Redis sorted sets (custom)       | We want to understand the queue, not import it.|
| DB           | Postgres 16 + pgvector           | Single store for state, results, and vectors. |
| Embeddings   | sentence-transformers            | Local, fast, no API dependency.               |
| Classifier   | Ollama (small model)             | Local cost classifier with no API cost.       |
| Metrics      | Prometheus + Grafana             | Standard, scriptable, demoable.               |
| Packaging    | Docker Compose                   | One command to run everything.                |

## 9. Quality bar

This is what "done" looks like, not a checklist someone else grades:

- Clean module boundaries — API, router, queue, workers, cache, storage are
  independently testable.
- Crash recovery is *demonstrated*, not claimed: a `kill -9` on a worker
  mid-job leaves no orphaned state and no duplicated results.
- Idempotency is real: replaying the same request 100× concurrently produces
  one row and one response.
- Tests catch bugs that matter: rate-limit boundary, cache near-miss/near-hit,
  WAL replay correctness, queue fairness under priority mix.
- Logs and metrics are useful enough that I can answer "why was this prompt
  slow?" without attaching a debugger.

## 10. Stretch ideas (post-v1)

- Multi-provider routing with automatic failover (OpenAI → Anthropic → local).
- Token-budget guardrails per user / per day.
- Speculative execution: kick off the LLM call in parallel with the cache
  lookup, cancel whichever loses.
- Response signing so downstream clients can verify a result came from this
  gateway.
- Replay tool: pipe an old log file through the gateway to reproduce behavior.

## 11. Out of scope explicitly

- Authn/authz beyond a static API key header. Real auth belongs upstream.
- GDPR-style data deletion workflows. The schema supports them but the tooling
  isn't built.
- Cross-region deployment, sharding, or HA. Single-node is enough to prove the
  ideas.

## 12. Running it

```
docker compose up --build
```

That brings up the gateway, Postgres, Redis, Ollama, Prometheus, and Grafana.
No migrations to run by hand. No "first set this env var." If `up` doesn't
work on a clean checkout, that's a bug.
