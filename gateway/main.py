"""FastAPI application entry point. Mounts all routers and starts the worker pool."""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.api.routes import batch, health, jobs, metrics, process
from gateway.observability.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    # Start the worker pool as a background task alongside the API.
    from gateway.worker.pool import start_pool
    task = asyncio.create_task(start_pool())
    yield
    task.cancel()


app = FastAPI(title="LLM Inference Gateway", lifespan=lifespan)

app.include_router(health.router)
app.include_router(process.router)
app.include_router(batch.router)
app.include_router(jobs.router)
app.include_router(metrics.router)
