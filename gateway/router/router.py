"""Smart router — decides which model tier handles a prompt before it hits the queue."""
from gateway.router.classifier import ModelTier, classify


async def route(text: str) -> ModelTier:
    """Return the cheapest tier that can handle this prompt."""
    return await classify(text)
