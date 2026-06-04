"""Returns the configured LLM provider. Import this everywhere — never import a provider directly."""
import os

from gateway.provider.base import BaseLLM


def get_provider() -> BaseLLM:
    provider = os.getenv("PROVIDER", "mock").lower()

    if provider == "mock":
        from gateway.provider.mock_llm import MockLLM
        return MockLLM()

    if provider == "claude":
        from gateway.provider.claude_llm import ClaudeLLM
        return ClaudeLLM(model=os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001"))

    raise ValueError(f"Unknown provider: {provider!r}. Choose 'mock' or 'claude'.")
