"""Single-task processor — runs the full pipeline for one claimed prompt.

Pipeline per prompt:
  1. Load from DB, mark as processing, log to WAL.
  2. Embed the prompt text.
  3. Check semantic cache — if hit, skip LLM entirely.
  4. If miss: wait for a rate-limit slot, classify complexity, call LLM.
  5. Store the response in DB and in cache (for future hits).
  6. If the request had a callback_url, POST the result back.
  7. Ack the item in the queue (remove from claimed set).

On failure: retry up to max_retries, then move to DLQ.
"""
import time

import httpx

from gateway.cache import semantic as cache
from gateway.config import settings
from gateway.limiter.rate_limiter import acquire
from gateway.observability.metrics import (
    cache_hits,
    cache_misses,
    cost_per_minute,
    request_latency,
)
from gateway.provider.factory import get_provider
from gateway.queue import priority_queue as pqueue
from gateway.queue.wal import append as wal_append
from gateway.router.router import route
from gateway.storage.db import AsyncSessionLocal
from gateway.storage.models import DeadLetterEntry, Prompt, Response
from gateway.api.models import Priority

# Cost units per tier — used for the cost_per_minute metric.
_COST_WEIGHTS = {"small": 1, "medium": 4, "large": 15}

# Single shared provider instance — loading the client once is cheaper.
_provider = None


def _get_provider():
    global _provider
    if _provider is None:
        _provider = get_provider()
    return _provider


async def process(prompt_id: str) -> None:
    """Entry point called by the worker pool for each claimed prompt."""
    # Load prompt and capture all fields before the session closes.
    async with AsyncSessionLocal() as session:
        row = await session.get(Prompt, prompt_id)
        if row is None:
            await pqueue.ack(prompt_id)
            return

        # Idempotent delivery: already finished, just ack and move on.
        if row.status in ("completed", "failed"):
            await pqueue.ack(prompt_id)
            return

        text = row.text
        user_id = row.user_id
        priority = row.priority
        callback_url = row.callback_url
        attempts = row.attempts + 1

        wal_append(prompt_id, row.status, "processing")
        row.status = "processing"
        row.attempts = attempts
        await session.commit()

    t0 = time.monotonic()
    try:
        await _run_pipeline(prompt_id, text, user_id, callback_url, t0)
    except Exception as exc:
        await _handle_failure(prompt_id, str(exc), priority, attempts)


async def _run_pipeline(
    prompt_id: str,
    text: str,
    user_id: str,
    callback_url: str | None,
    t0: float,
) -> None:
    # Embed once — reused for both cache lookup and cache store.
    embedding = await cache.embed(text)
    cache_result = await cache.lookup(text)

    if cache_result.hit:
        cache_hits.inc()
        response_text = cache_result.response
        model_tier = "cached"
        is_cached = True
    else:
        cache_misses.inc()

        # Block until the provider rate-limit allows another call.
        await acquire(user_id=user_id)

        tier = await route(text)
        response_text = await _get_provider().complete(text)
        model_tier = tier.value
        is_cached = False

        # Store for future semantic matches.
        await cache.store(prompt_id, text, embedding, response_text)
        cost_per_minute.labels(model_tier=model_tier).inc(
            _COST_WEIGHTS.get(model_tier, 1)
        )

    latency_ms = int((time.monotonic() - t0) * 1000)
    request_latency.labels(
        model_tier=model_tier, cached=str(is_cached).lower()
    ).observe(latency_ms / 1000)

    async with AsyncSessionLocal() as session:
        session.add(
            Response(
                prompt_id=prompt_id,
                response_text=response_text,
                model_tier=model_tier,
                latency_ms=latency_ms,
                cached_from_prompt_id=cache_result.matched_prompt_id if is_cached else None,
            )
        )
        prompt = await session.get(Prompt, prompt_id)
        wal_append(prompt_id, "processing", "completed")
        prompt.status = "completed"
        await session.commit()

    if callback_url:
        await _fire_callback(callback_url, prompt_id, response_text)

    await pqueue.ack(prompt_id)


async def _handle_failure(
    prompt_id: str, error: str, priority: str, attempts: int
) -> None:
    """Retry or move to DLQ depending on attempt count."""
    async with AsyncSessionLocal() as session:
        row = await session.get(Prompt, prompt_id)
        if row is None:
            return

        row.last_error = error

        if attempts >= settings.max_retries:
            wal_append(prompt_id, "processing", "failed")
            row.status = "failed"
            session.add(
                DeadLetterEntry(
                    prompt_id=prompt_id,
                    reason_chain={"errors": [error], "attempts": attempts},
                )
            )
            await pqueue.ack(prompt_id)
        else:
            # Put back in queue — the WAL lets us recover from a crash here too.
            wal_append(prompt_id, "processing", "pending")
            row.status = "pending"
            await pqueue.enqueue(prompt_id, Priority[priority])
            await pqueue.ack(prompt_id)

        await session.commit()


async def _fire_callback(url: str, prompt_id: str, response: str) -> None:
    """POST result to the caller's webhook. Best-effort — never fails the job."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={"prompt_id": prompt_id, "response": response})
    except Exception:
        pass
