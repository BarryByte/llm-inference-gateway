"""Semantic cache tests — hit, near-hit, near-miss, threshold boundary.

The cache uses cosine similarity to find semantically similar prompts.
These tests control the embedding vectors directly so we can place them
exactly on each side of the threshold without a real sentence-transformer model.

Key insight: cosine distance = 1 - cosine_similarity.
Threshold is 0.92 similarity → 0.08 distance cutoff.
"""
import math
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.cache.semantic import CacheResult, lookup, store


def _unit_vec(dim: int, value: float = 1.0) -> list[float]:
    """Return a unit vector in the first dimension — cosine similarity = 1.0 with itself."""
    vec = [0.0] * dim
    vec[0] = value
    return vec


def _orthogonal_vec(dim: int) -> list[float]:
    """A vector orthogonal to _unit_vec — cosine similarity = 0.0."""
    vec = [0.0] * dim
    vec[1] = 1.0
    return vec


def _angled_vec(dim: int, similarity: float) -> list[float]:
    """A vector with a controlled cosine similarity to _unit_vec(dim)."""
    # cos(θ) = similarity → x = similarity, y = sqrt(1 - similarity²)
    vec = [0.0] * dim
    vec[0] = similarity
    vec[1] = math.sqrt(1 - similarity ** 2)
    return vec


@pytest.fixture
def mock_db_session():
    """Yields a mock session that returns no existing cache entries (empty DB)."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


async def test_cache_miss_when_empty():
    """lookup() returns a miss when there are no entries in the DB."""
    query_vec = _unit_vec(384)

    with patch("gateway.cache.semantic.embed", AsyncMock(return_value=query_vec)), \
         patch("gateway.cache.semantic.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        # Simulate no rows returned.
        mock_session.execute = AsyncMock(
            return_value=MagicMock(first=MagicMock(return_value=None))
        )
        mock_session_cls.return_value = mock_session

        result = await lookup("anything")
        assert result.hit is False


async def test_cache_hit_above_threshold():
    """A cached entry with similarity >= 0.92 should be returned as a hit."""
    from gateway.config import settings
    query_vec = _angled_vec(384, settings.similarity_threshold + 0.01)

    # Simulate a DB row with cosine distance just inside the threshold.
    distance = 1.0 - (settings.similarity_threshold + 0.01)

    fake_entry = MagicMock()
    fake_entry.id = "cached-id"
    fake_entry.response_text = "cached response"
    fake_entry.ttl_expires_at = datetime.now() + timedelta(hours=1)

    fake_row = MagicMock()
    fake_row.dist = distance
    fake_row.CacheEntry = fake_entry

    with patch("gateway.cache.semantic.embed", AsyncMock(return_value=query_vec)), \
         patch("gateway.cache.semantic.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(first=MagicMock(return_value=fake_row))
        )
        mock_session_cls.return_value = mock_session

        result = await lookup("similar prompt")
        assert result.hit is True
        assert result.response == "cached response"


async def test_cache_miss_below_threshold():
    """A cached entry with similarity < 0.92 should NOT be a hit."""
    from gateway.config import settings
    distance = 1.0 - (settings.similarity_threshold - 0.05)  # too far away

    fake_entry = MagicMock()
    fake_row = MagicMock()
    fake_row.dist = distance
    fake_row.CacheEntry = fake_entry

    with patch("gateway.cache.semantic.embed", AsyncMock(return_value=_unit_vec(384))), \
         patch("gateway.cache.semantic.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(first=MagicMock(return_value=fake_row))
        )
        mock_session_cls.return_value = mock_session

        result = await lookup("different enough prompt")
        assert result.hit is False


async def test_store_adds_entry_to_db():
    """store() should create a CacheEntry row with the right fields."""
    added_entries = []

    with patch("gateway.cache.semantic.AsyncSessionLocal") as mock_session_cls:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.add = MagicMock(side_effect=added_entries.append)
        mock_session.commit = AsyncMock()
        mock_session_cls.return_value = mock_session

        await store("p1", "hello", _unit_vec(384), "hello back")

    assert len(added_entries) == 1
    entry = added_entries[0]
    assert entry.id == "p1"
    assert entry.response_text == "hello back"
