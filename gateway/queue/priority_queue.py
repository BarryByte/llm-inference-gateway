"""
Priority queue backed by two Redis sorted sets.

  gateway:queue   - waiting prompts, score = priority_offset + timestamp
  gateway:claimed 0 in-flight prompts, score = deadline (now + visibility_timeout)

Score offsets keep three lanes in one sorted set without overlap:
  high   ->  0 + ts   
  normal -> 1B + ts   
  low -> 2B + ts
"""
import time

import redis.asyncio as aioredis

from gateway.api.models import Priority
from gateway.config import settings

QUEUE_KEY   = "gateway:queue"
CLAIMED_KEY = "gateway:claimed"

_OFFSETS = {
    Priority.high:   0,
    Priority.normal: 1_000_000_000,
    Priority.low:    2_000_000_000,
}

_redis: aioredis.Redis | None = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


async def enqueue(prompt_id: str, priority: Priority) -> None:
    score = _OFFSETS[priority] + time.time()
    await _get_redis().zadd(QUEUE_KEY, {prompt_id: score})


async def dequeue() -> str | None:
    r = _get_redis()
    result = await r.zpopmin(QUEUE_KEY, count=1)
    if not result:
        return None
    prompt_id, _ = result[0]
    deadline = time.time() + settings.visibility_timeout_seconds
    await r.zadd(CLAIMED_KEY, {prompt_id: deadline})
    return prompt_id


async def ack(prompt_id: str) -> None:
    await _get_redis().zrem(CLAIMED_KEY, prompt_id)


async def requeue_timed_out() -> int:
    """Move crashed/hung claimed prompts back to the queue at normal priority."""
    r = _get_redis()
    timed_out = await r.zrangebyscore(CLAIMED_KEY, "-inf", time.time())
    if not timed_out:
        return 0
    pipe = r.pipeline()
    for prompt_id in timed_out:
        pipe.zrem(CLAIMED_KEY, prompt_id)
        pipe.zadd(QUEUE_KEY, {prompt_id: _OFFSETS[Priority.normal] + time.time()})
    await pipe.execute()
    return len(timed_out)


async def promote_starved() -> int:
    """Bump low-priority prompts waiting longer than starvation_max_wait_seconds to normal."""
    r = _get_redis()
    cutoff   = time.time() - settings.starvation_max_wait_seconds
    max_score = _OFFSETS[Priority.low] + cutoff
    starved  = await r.zrangebyscore(QUEUE_KEY, _OFFSETS[Priority.low], max_score)
    if not starved:
        return 0
    pipe = r.pipeline()
    for prompt_id in starved:
        pipe.zrem(QUEUE_KEY, prompt_id)
        pipe.zadd(QUEUE_KEY, {prompt_id: _OFFSETS[Priority.normal] + time.time()})
    await pipe.execute()
    return len(starved)


async def depth() -> dict:
    r = _get_redis()
    high   = await r.zcount(QUEUE_KEY, _OFFSETS[Priority.high],   _OFFSETS[Priority.normal] - 1)
    normal = await r.zcount(QUEUE_KEY, _OFFSETS[Priority.normal], _OFFSETS[Priority.low] - 1)
    low    = await r.zcount(QUEUE_KEY, _OFFSETS[Priority.low],    "+inf")
    claimed = await r.zcard(CLAIMED_KEY)
    return {"high": high, "normal": normal, "low": low, "claimed": claimed, "total": high + normal + low}
