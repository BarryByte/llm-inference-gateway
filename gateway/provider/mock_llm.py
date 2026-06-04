"""Mock LLM provider - simulates a real upstream with realistic constraints.

- 200-500ms randomized latency
- 5% random failure rate
- Internal rate limit: 300 calls/min (independent of the gateway's limiter)
- Deterministic responses for a fixed prompt set (keeps cache tests stable)

No real API keys. Ever.
"""
import asyncio
import random
import time
from collections import deque

from gateway.config import settings
from gateway.provider.base import BaseLLM, ProviderError


_DETERMINISTIC: dict[str, str] = {
    "ping": "pong",
    "hello": "Hello! How can I help?",
    "what is 2+2": "4",
}

_call_times: deque[float] = deque()


class MockLLM(BaseLLM):
    async def complete(self, prompt: str) -> str:
        self._enforce_rate_limit()
        await asyncio.sleep(random.uniform(0.2, 0.5))

        if random.random() < 0.05:
            raise ProviderError("Simulated upstream failure")

        if prompt.strip().lower() in _DETERMINISTIC:
            return _DETERMINISTIC[prompt.strip().lower()]

        return f"Mock response for: {prompt[:80]}"

    def _enforce_rate_limit(self) -> None:
        now = time.time()
        window = settings.rate_limit_window_seconds
        while _call_times and _call_times[0] < now - window:
            _call_times.popleft()
        if len(_call_times) >= settings.provider_rate_limit:
            raise ProviderError("Mock provider rate limit exceeded")
        _call_times.append(now)
