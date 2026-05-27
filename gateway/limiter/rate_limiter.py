"""
Rate limiter ~ controls how many LLM calls go out per minute.

Supports two limits:
  1. Global ~ shared across all workers (default: 300 calls/min).
  2. Per-user ~ optional, disabled by default.

When the limit is full, acquire() waits and retries every 100ms
instead of crashing. Workers slow down gracefully; nothing breaks.

How it tracks calls:
  Redis keeps a list of timestamps, one per call made in the last 60s.
  Before each new call: drop timestamps older than 60s, count what's left.
  Under the limit --> record this call and proceed.
  At the limit --> wait 100ms, then try again.
"""
import asyncio
import time
import uuid

import redis.asyncio as aioredis

from gateway.config import settings

GLOBAL_KEY = "gateway:ratelimit:global"

_redis: aioredis.Redis | None = None

def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=False)
    return _redis

# redis commands are individually atomic, but we need 3 commands in sequence
# 1. remove old 
# 2. count
# 3. insert 
# between our count and insert, another worker can sneak in, read the same count
# and both get a slot
# But lua runs all three as one unbreakable unit 
# no race condition between workers

_SLIDING_WINDOW_SCRIPT = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
local count = redis.call('ZCARD', key)

if count < limit then
    redis.call('ZADD', key, now, member)
    redis.call('EXPIRE', key, math.ceil(window) + 1)
    return 1
end
return 0
"""

async def acquire(user_id: str | None = None) -> None:
    """Block until a provider call slot is available."""
    await _wait_for_slot(GLOBAL_KEY, settings.provider_rate_limit)

    if user_id and settings.per_user_rate_limit > 0:
        user_key = f"gateway:ratelimit:user:{user_id}"
        await _wait_for_slot(user_key, settings.per_user_rate_limit)

async def _wait_for_slot(key: str, limit: int) -> None:
    while not await _try_acquire(key, limit):
        await asyncio.sleep(0.1)

async def _try_acquire(key: str, limit: int) -> bool:
    """Sliding window via Redis sorted set. Returns True if slot granted."""
    r = _get_redis()
    now = time.time()
    result = await r.eval(
        _SLIDING_WINDOW_SCRIPT,
        1,           # number of keys
        key,         # KEYS[1]
        now,         # ARGV[1]
        settings.rate_limit_window_seconds,  # ARGV[2]
        limit,       # ARGV[3]
        uuid.uuid4().hex,  # ARGV[4] ~ unique member name per call
    )
    return bool(result)
