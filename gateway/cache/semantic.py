"""
Semantic cache ~ embed prompts with sentence-transformers, find near-matches in pgvector.

Hit:  cosine similarity >= threshold --> return stored response, bump hit_count.
Miss: embed --> LLM call (caller's job) --> store result.

Cosine distance : 0 = identical, 1 = orthogonal.
distance threshold = 1 - similarity_threshold
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update

from gateway.config import settings
from gateway.storage.db import AsyncSessionLocal
from gateway.storage.models import CacheEntry

_model = None  # lazy - loading takes ~2s, don't pay it at import time


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(settings.embedding_model)
    return _model


@dataclass
class CacheResult:
    hit: bool
    response: Optional[str] = None
    matched_prompt_id: Optional[str] = None


async def embed(text: str) -> list[float]:
    """384-dim embedding vector. Runs in a thread to avoid blocking the event loop."""
    vector = await asyncio.to_thread(_get_model().encode, text)
    return vector.tolist()


async def lookup(text: str) -> CacheResult:
    """Return a hit if the nearest cache entry is within similarity_threshold."""
    query_vec = await embed(text)
    distance_col = CacheEntry.embedding.cosine_distance(query_vec).label("dist")

    stmt = (
        select(CacheEntry, distance_col)
        .where(CacheEntry.ttl_expires_at > datetime.now())
        .order_by(distance_col)
        .limit(1)
    )

    async with AsyncSessionLocal() as session:
        row = (await session.execute(stmt)).first()
        if row is None or row.dist >= (1.0 - settings.similarity_threshold):
            return CacheResult(hit=False)

        entry: CacheEntry = row.CacheEntry
        await session.execute(
            update(CacheEntry)
            .where(CacheEntry.id == entry.id)
            .values(hit_count=CacheEntry.hit_count + 1, last_hit_at=datetime.now())
        )
        await session.commit()
        return CacheResult(hit=True, response=entry.response_text, matched_prompt_id=entry.id)


async def store(prompt_id: str, text: str, embedding: list[float], response: str) -> None:
    """Persist a new cache entry with a TTL expiry."""
    entry = CacheEntry(
        id=prompt_id,
        embedding=embedding,
        response_text=response,
        ttl_expires_at=datetime.now() + timedelta(seconds=settings.cache_ttl_seconds),
    )
    async with AsyncSessionLocal() as session:
        session.add(entry)
        await session.commit()
