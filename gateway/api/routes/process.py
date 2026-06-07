"""POST /process and POST /process/stream — single prompt submission.

Two paths depending on whether callback_url is set:

  Async (callback_url present):
    Enqueue → return {status: queued} immediately.
    Worker POSTs result to callback_url when done.

  Sync (no callback_url):
    Enqueue → poll DB every 100ms → return result when done (30s timeout).

Cache fast-path:
  If the prompt is semantically similar to a cached entry, skip the queue entirely
  and return immediately — no worker involved.
"""
import asyncio
import json
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from gateway.api.models import Priority, ProcessRequest, ProcessResponse
from gateway.cache import semantic as cache
from gateway.config import settings
from gateway.queue import priority_queue as pqueue
from gateway.queue.wal import append as wal_append
from gateway.storage.db import AsyncSessionLocal
from gateway.storage.models import Prompt, Response

router = APIRouter()

_POLL_INTERVAL = 0.1   # seconds between DB status checks
_SYNC_TIMEOUT = 30.0   # max wait for a synchronous result


@router.post("/process", response_model=ProcessResponse)
async def process_prompt(req: ProcessRequest) -> ProcessResponse:
    # 1. Back-pressure — refuse new work if the queue is full.
    depths = await pqueue.depth()
    if depths["total"] >= settings.max_queue_depth:
        raise HTTPException(status_code=503, detail="Queue full — retry later")

    # 2. Idempotency — same prompt_id was already submitted.
    async with AsyncSessionLocal() as session:
        existing = await session.get(Prompt, req.prompt_id)
        if existing:
            if existing.status == "completed":
                resp_row = await session.get(Response, req.prompt_id)
                return ProcessResponse(
                    user_id=req.user_id,
                    prompt_id=req.prompt_id,
                    status="completed",
                    cached=bool(resp_row and resp_row.cached_from_prompt_id),
                    response=resp_row.response_text if resp_row else None,
                )
            # Still in queue or processing — poll or return queued.
            if req.callback_url:
                return ProcessResponse(
                    user_id=req.user_id, prompt_id=req.prompt_id, status="queued"
                )
            return await _poll_for_result(req.user_id, req.prompt_id)

    # 3. Embed and check semantic cache before touching the queue.
    embedding = await cache.embed(req.text)
    cache_result = await cache.lookup(req.text)

    if cache_result.hit:
        # Cache hit — store a completed record and return immediately, no worker needed.
        async with AsyncSessionLocal() as session:
            wal_append(req.prompt_id, "none", "completed")
            session.add(
                Prompt(
                    prompt_id=req.prompt_id,
                    user_id=req.user_id,
                    text=req.text,
                    priority=req.priority.value,
                    status="completed",
                    embedding=embedding,
                    callback_url=req.callback_url,
                )
            )
            session.add(
                Response(
                    prompt_id=req.prompt_id,
                    response_text=cache_result.response,
                    model_tier="cached",
                    latency_ms=0,
                    cached_from_prompt_id=cache_result.matched_prompt_id,
                )
            )
            await session.commit()

        return ProcessResponse(
            user_id=req.user_id,
            prompt_id=req.prompt_id,
            status="completed",
            cached=True,
            response=cache_result.response,
            processing_time_ms=0,
        )

    # 4. Cache miss — store prompt and enqueue for a worker to handle.
    async with AsyncSessionLocal() as session:
        wal_append(req.prompt_id, "none", "pending")
        session.add(
            Prompt(
                prompt_id=req.prompt_id,
                user_id=req.user_id,
                text=req.text,
                priority=req.priority.value,
                status="pending",
                embedding=embedding,
                callback_url=req.callback_url,
            )
        )
        await session.commit()

    await pqueue.enqueue(req.prompt_id, req.priority)

    # 5. Async mode — caller will be notified via webhook.
    if req.callback_url:
        return ProcessResponse(
            user_id=req.user_id, prompt_id=req.prompt_id, status="queued"
        )

    # 6. Sync mode — wait here until the worker finishes.
    return await _poll_for_result(req.user_id, req.prompt_id)


@router.post("/process/stream")
async def process_stream(req: ProcessRequest) -> StreamingResponse:
    """Same as /process but streams SSE status events instead of blocking."""
    depths = await pqueue.depth()
    if depths["total"] >= settings.max_queue_depth:
        raise HTTPException(status_code=503, detail="Queue full — retry later")

    async def event_stream():
        # Embed + cache check (same as sync path).
        embedding = await cache.embed(req.text)
        cache_result = await cache.lookup(req.text)

        if cache_result.hit:
            yield _sse({"status": "completed", "prompt_id": req.prompt_id,
                        "response": cache_result.response, "cached": True})
            return

        # Persist and enqueue.
        async with AsyncSessionLocal() as session:
            wal_append(req.prompt_id, "none", "pending")
            session.add(Prompt(
                prompt_id=req.prompt_id, user_id=req.user_id, text=req.text,
                priority=req.priority.value, status="pending",
                embedding=embedding, callback_url=req.callback_url,
            ))
            await session.commit()

        await pqueue.enqueue(req.prompt_id, req.priority)
        yield _sse({"status": "queued", "prompt_id": req.prompt_id})

        # Stream status ticks until the worker finishes.
        deadline = time.monotonic() + _SYNC_TIMEOUT
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            async with AsyncSessionLocal() as session:
                row = await session.get(Prompt, req.prompt_id)
                if row and row.status == "completed":
                    resp = await session.get(Response, req.prompt_id)
                    yield _sse({
                        "status": "completed",
                        "prompt_id": req.prompt_id,
                        "response": resp.response_text if resp else None,
                        "cached": bool(resp and resp.cached_from_prompt_id),
                    })
                    return
                if row and row.status == "failed":
                    yield _sse({"status": "failed", "prompt_id": req.prompt_id,
                                "error": row.last_error})
                    return

        yield _sse({"status": "timeout", "prompt_id": req.prompt_id})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def _poll_for_result(user_id: str, prompt_id: str) -> ProcessResponse:
    """Poll DB every 100ms until the worker marks the prompt done."""
    deadline = time.monotonic() + _SYNC_TIMEOUT

    while time.monotonic() < deadline:
        async with AsyncSessionLocal() as session:
            row = await session.get(Prompt, prompt_id)
            if row and row.status == "completed":
                resp = await session.get(Response, prompt_id)
                return ProcessResponse(
                    user_id=user_id,
                    prompt_id=prompt_id,
                    status="completed",
                    cached=bool(resp and resp.cached_from_prompt_id),
                    response=resp.response_text if resp else None,
                    processing_time_ms=resp.latency_ms if resp else None,
                )
            if row and row.status == "failed":
                return ProcessResponse(
                    user_id=user_id,
                    prompt_id=prompt_id,
                    status="failed",
                    error=row.last_error,
                    retry_count=row.attempts,
                )
        await asyncio.sleep(_POLL_INTERVAL)

    return ProcessResponse(user_id=user_id, prompt_id=prompt_id, status="processing")
