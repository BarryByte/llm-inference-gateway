"""Shared pytest fixtures.

Strategy:
  - Redis → fakeredis (no real Redis needed, full API compatibility).
  - DB    → mock the session where needed (pgvector doesn't work with SQLite).
  - LLM   → MockLLM is used by default (PROVIDER=mock).
  - HTTP  → httpx.AsyncClient with the FastAPI app, worker pool disabled.
"""
import pytest
import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, patch


@pytest.fixture
async def fake_redis():
    """In-memory Redis with full sorted-set support — no real Redis required."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest.fixture
async def fake_redis_bytes():
    """decode_responses=False variant — the rate limiter stores raw bytes."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


@pytest.fixture
async def client():
    """FastAPI test client. Worker pool is disabled so tests drive processing manually."""
    from gateway.main import app

    with patch("gateway.worker.pool.start_pool", new_callable=AsyncMock):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            yield ac
