"""Rate limiter tests — boundary conditions at the sliding window edge.

We test _try_acquire directly (the atomic Redis Lua slot check) rather than
the full acquire() so we don't need asyncio.sleep in tests.

The Lua script is the critical path — it's what prevents race conditions between
concurrent workers. These tests verify it counts correctly.
"""
import time

import pytest
from unittest.mock import patch, AsyncMock


async def test_allows_calls_up_to_limit(fake_redis_bytes):
    """Each call below the limit should succeed."""
    with patch("gateway.limiter.rate_limiter._get_redis", return_value=fake_redis_bytes):
        from gateway.limiter.rate_limiter import _try_acquire

        LIMIT = 5
        for _ in range(LIMIT):
            granted = await _try_acquire("test:key", LIMIT)
            assert granted is True


async def test_blocks_at_limit(fake_redis_bytes):
    """The (limit+1)th call should be rejected."""
    with patch("gateway.limiter.rate_limiter._get_redis", return_value=fake_redis_bytes):
        from gateway.limiter.rate_limiter import _try_acquire

        LIMIT = 3
        for _ in range(LIMIT):
            await _try_acquire("test:block", LIMIT)

        # One more should be denied.
        granted = await _try_acquire("test:block", LIMIT)
        assert granted is False


async def test_window_slides_correctly(fake_redis_bytes):
    """Calls older than the window should not count against the current limit."""
    with patch("gateway.limiter.rate_limiter._get_redis", return_value=fake_redis_bytes):
        from gateway.limiter import rate_limiter as rl

        LIMIT = 2
        KEY = "test:slide"

        # Fill the window.
        await rl._try_acquire(KEY, LIMIT)
        await rl._try_acquire(KEY, LIMIT)
        assert await rl._try_acquire(KEY, LIMIT) is False

        # Manually age out the existing entries (push them into the past).
        old_time = time.time() - rl.settings.rate_limit_window_seconds - 1
        members = await fake_redis_bytes.zrange(KEY, 0, -1)
        if members:
            mapping = {m: old_time for m in members}
            await fake_redis_bytes.zadd(KEY, mapping)

        # Now we should have a fresh slot.
        assert await rl._try_acquire(KEY, LIMIT) is True


async def test_per_user_limit_is_independent(fake_redis_bytes):
    """Global and per-user limits are separate keys — one doesn't block the other."""
    with patch("gateway.limiter.rate_limiter._get_redis", return_value=fake_redis_bytes):
        from gateway.limiter.rate_limiter import _try_acquire

        # Fill the global key.
        for _ in range(3):
            await _try_acquire("global", 3)
        assert await _try_acquire("global", 3) is False

        # A different user key should still be open.
        assert await _try_acquire("user:alice", 3) is True


async def test_acquire_does_not_block_when_slot_available():
    """acquire() should return immediately when under the limit."""
    mock_try = AsyncMock(return_value=True)
    with patch("gateway.limiter.rate_limiter._try_acquire", mock_try):
        from gateway.limiter.rate_limiter import acquire
        await acquire()  # Should not raise or hang.
        assert mock_try.called
