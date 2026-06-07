"""GET /health — liveness probe for load balancers and Docker HEALTHCHECK.

Checks Postgres and Redis connectivity. Returns overall status "healthy" or "degraded".
Queue depth is included so ops can see if the gateway is backed up.
"""
from datetime import datetime, timezone

import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlalchemy import text

from gateway.api.models import HealthResponse
from gateway.config import settings
from gateway.queue.priority_queue import depth as queue_depth
from gateway.storage.db import AsyncSessionLocal

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    components: dict = {}
    overall = "healthy"

    # Postgres connectivity.
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        components["database"] = "connected"
    except Exception as exc:
        components["database"] = f"error: {exc}"
        overall = "degraded"

    # Redis connectivity.
    try:
        r = aioredis.from_url(settings.redis_url, decode_responses=True)
        await r.ping()
        await r.aclose()
        components["redis"] = "connected"
    except Exception as exc:
        components["redis"] = f"error: {exc}"
        overall = "degraded"

    # Queue depth — not a health signal, just useful context for ops.
    try:
        components["queue"] = await queue_depth()
    except Exception:
        components["queue"] = "unavailable"

    return HealthResponse(
        status=overall,
        timestamp=datetime.now(timezone.utc).isoformat(),
        components=components,
    )
