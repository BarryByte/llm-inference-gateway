"""GET /metrics — Prometheus-format scrape endpoint.

Prometheus pulls this every 15s (configured in infra/prometheus/prometheus.yml).
All metric objects are defined in gateway/observability/metrics.py — this route
just serialises whatever prometheus_client has collected so far.
"""
from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter()


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
