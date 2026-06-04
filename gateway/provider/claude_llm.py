"""Claude provider — uses your Anthropic credits for real benchmarking.

Drop-in replacement for MockLLM. Enable by setting PROVIDER=claude in .env.
Requires ANTHROPIC_API_KEY to be set.

Install: pip install anthropic
"""
from gateway.provider.base import BaseLLM, ProviderError


class ClaudeLLM(BaseLLM):
    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        # Haiku is the cheapest/fastest — right choice for benchmarking throughput.
        # Swap to claude-sonnet-4-6 if you want quality comparisons.
        try:
            import anthropic
            self._client = anthropic.AsyncAnthropic()
            self._model = model
        except ImportError:
            raise RuntimeError("Run: pip install anthropic")

    async def complete(self, prompt: str) -> str:
        try:
            message = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            return message.content[0].text
        except Exception as e:
            raise ProviderError(f"Claude error: {e}") from e
