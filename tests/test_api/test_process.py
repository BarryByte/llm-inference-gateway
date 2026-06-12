"""POST /process tests — happy path, cache hit, idempotency, back-pressure.

The worker pool is disabled in tests (see conftest.py).
The process route is tested in isolation: we mock the DB, cache, and queue
so tests are fast and don't need any running infrastructure.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.api.models import ProcessResponse


def _make_process_req(prompt_id: str = "p1", text: str = "hello") -> dict:
    return {"user_id": "u1", "prompt_id": prompt_id, "text": text, "priority": "normal"}


async def test_back_pressure_returns_503(client):
    """When the queue is full, POST /process should return 503."""
    # Simulate queue at max depth.
    with patch(
        "gateway.api.routes.process.pqueue.depth",
        AsyncMock(return_value={"total": 99999, "high": 0, "normal": 99999, "low": 0, "claimed": 0}),
    ):
        resp = await client.post("/process", json=_make_process_req())

    assert resp.status_code == 503


async def test_cache_hit_returns_immediately(client):
    """A cache hit should return a completed response without touching the queue."""
    from gateway.cache.semantic import CacheResult

    cache_hit = CacheResult(hit=True, response="cached!", matched_prompt_id="old-p")

    with patch("gateway.api.routes.process.pqueue.depth",
               AsyncMock(return_value={"total": 0, "high": 0, "normal": 0, "low": 0, "claimed": 0})), \
         patch("gateway.api.routes.process.cache.embed",
               AsyncMock(return_value=[0.0] * 384)), \
         patch("gateway.api.routes.process.cache.lookup",
               AsyncMock(return_value=cache_hit)), \
         patch("gateway.api.routes.process.AsyncSessionLocal") as mock_session_cls, \
         patch("gateway.api.routes.process.wal_append"):

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=None)  # No existing prompt.
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value = mock_session

        resp = await client.post("/process", json=_make_process_req())

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["cached"] is True
    assert data["response"] == "cached!"


async def test_idempotency_completed_prompt(client):
    """Submitting the same prompt_id that already completed returns the stored result."""
    from gateway.storage.models import Prompt, Response as DBResponse

    existing_prompt = MagicMock(spec=Prompt)
    existing_prompt.status = "completed"

    existing_response = MagicMock(spec=DBResponse)
    existing_response.response_text = "already done"
    existing_response.cached_from_prompt_id = None

    with patch("gateway.api.routes.process.pqueue.depth",
               AsyncMock(return_value={"total": 0, "high": 0, "normal": 0, "low": 0, "claimed": 0})), \
         patch("gateway.api.routes.process.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # First get() → existing prompt, second get() → existing response.
        mock_session.get = AsyncMock(side_effect=[existing_prompt, existing_response])
        mock_session_cls.return_value = mock_session

        resp = await client.post("/process", json=_make_process_req())

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["response"] == "already done"


async def test_async_mode_returns_queued_when_callback_url_set(client):
    """When callback_url is set, /process should enqueue and return status=queued."""
    from gateway.cache.semantic import CacheResult

    cache_miss = CacheResult(hit=False)

    with patch("gateway.api.routes.process.pqueue.depth",
               AsyncMock(return_value={"total": 0, "high": 0, "normal": 0, "low": 0, "claimed": 0})), \
         patch("gateway.api.routes.process.cache.embed",
               AsyncMock(return_value=[0.0] * 384)), \
         patch("gateway.api.routes.process.cache.lookup",
               AsyncMock(return_value=cache_miss)), \
         patch("gateway.api.routes.process.pqueue.enqueue", AsyncMock()), \
         patch("gateway.api.routes.process.AsyncSessionLocal") as mock_session_cls, \
         patch("gateway.api.routes.process.wal_append"):

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.get = AsyncMock(return_value=None)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value = mock_session

        req = _make_process_req()
        req["callback_url"] = "http://example.com/hook"
        resp = await client.post("/process", json=req)

    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
