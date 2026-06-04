"""BaseLLM — the contract every provider must implement.

The worker calls complete() and nothing else. It never imports a
specific provider directly. Swap providers by changing config, not code.
"""
from abc import ABC, abstractmethod


class BaseLLM(ABC):
    @abstractmethod
    async def complete(self, prompt: str) -> str:
        """Send prompt, return response text. Raise ProviderError on failure."""
        ...


class ProviderError(Exception):
    """Raised on any upstream failure — timeout, rate limit, bad response."""
    pass
