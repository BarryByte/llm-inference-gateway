"""FastAPI application entry point. Mounts all routers and starts the worker pool."""
from fastapi import FastAPI

app = FastAPI(title="LLM Inference Gateway")
