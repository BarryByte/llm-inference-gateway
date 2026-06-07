"""POST /batch — submit up to 100 prompts in one call.

Each prompt goes through the same pipeline as /process (cache check → queue → worker).
All prompts run concurrently — total latency = slowest single prompt, not sum of all.
"""
import asyncio

from fastapi import APIRouter, HTTPException

from gateway.api.models import ProcessRequest, ProcessResponse
from gateway.api.routes.process import process_prompt

router = APIRouter()


@router.post("/batch", response_model=list[ProcessResponse])
async def batch(requests: list[ProcessRequest]) -> list[ProcessResponse]:
    if len(requests) > 100:
        raise HTTPException(status_code=422, detail="Max 100 prompts per batch")

    # Fire all concurrently. Errors per item are caught individually
    # so one bad prompt does not fail the whole batch.
    results = await asyncio.gather(
        *[process_prompt(req) for req in requests],
        return_exceptions=True,
    )

    responses = []
    for req, result in zip(requests, results):
        if isinstance(result, Exception):
            responses.append(
                ProcessResponse(
                    user_id=req.user_id,
                    prompt_id=req.prompt_id,
                    status="failed",
                    error=str(result),
                )
            )
        else:
            responses.append(result)

    return responses
