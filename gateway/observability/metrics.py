"""Prometheus metric definitions — one place for all counters, histograms, and gauges."""
from prometheus_client import Counter, Gauge, Histogram

request_total = Counter(
    "gateway_requests_total", "Total prompt submissions", ["priority", "status"]
)

cache_hits = Counter(
    "gateway_cache_hits_total", "Semantic cache hits"
)

cache_misses = Counter(
    "gateway_cache_misses_total", "Semantic cache misses"
)

queue_depth = Gauge(
    "gateway_queue_depth", "Current queue depth", ["priority"]
)

worker_saturation = Gauge(
    "gateway_worker_saturation", "Fraction of workers currently processing (0–1)"
)

rate_limiter_wait = Histogram(
    "gateway_rate_limiter_wait_seconds", "Time spent waiting for a rate-limit slot",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0]
)

request_latency = Histogram(
    "gateway_request_latency_seconds", "End-to-end latency per request",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    labelnames=["model_tier", "cached"]
)

dlq_size = Gauge(
    "gateway_dlq_size", "Number of entries in the dead-letter queue"
)

cost_per_minute = Counter(
    "gateway_cost_units_total", "Relative cost units consumed", ["model_tier"]
)
