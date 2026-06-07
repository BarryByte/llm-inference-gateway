"""GET /jobs/{prompt_id} — fetch the current status and result of a submitted prompt.

Useful for polling when the caller didn't set a callback_url and
doesn't want to hold a long-lived HTTP connection open.
"""
from fastapi import APIRouter, HTTPException

from gateway.api.models import JobStatus
from gateway.storage.db import AsyncSessionLocal
from gateway.storage.models import Prompt, Response

router = APIRouter()


@router.get("/jobs/{prompt_id}", response_model=JobStatus)
async def get_job(prompt_id: str) -> JobStatus:
    async with AsyncSessionLocal() as session:
        row = await session.get(Prompt, prompt_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Job {prompt_id!r} not found")

        resp = None
        if row.status == "completed":
            resp = await session.get(Response, prompt_id)

        return JobStatus(
            prompt_id=prompt_id,
            status=row.status,
            response=resp.response_text if resp else None,
            model_tier=resp.model_tier if resp else None,
            cached=bool(resp and resp.cached_from_prompt_id),
            attempts=row.attempts,
            last_error=row.last_error,
        )
