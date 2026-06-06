"""Complexity classifier — scores a prompt and picks a model tier (small/medium/large).

Strategy:
  1. Try Ollama (local LLM) for a semantic judgement — target < 50ms.
  2. If Ollama is down or slow, fall back to heuristics (token count, keywords, code markers).
     Fallback is deterministic and adds zero latency.

Why classify before queuing?
  Cheap prompts go to a cheap model (1x cost). Complex prompts go to a capable model (15x cost).
  The classifier runs on every request, so it must be fast — not a full inference call.
"""
import httpx

from gateway.config import settings
from enum import Enum

# Phrases that strongly suggest the prompt needs multi-step reasoning.
_REASONING_KEYWORDS = {
    "step by step", "explain why", "analyze", "compare", "evaluate",
    "design", "implement", "debug", "optimize", "architecture",
    "tradeoff", "pros and cons", "walk me through",
}

# Presence of any of these means the prompt involves code.
_CODE_MARKERS = ["```", "def ", "class ", "SELECT ", "import ", "function(", "=> {"]


class ModelTier(str, Enum):
    small = "small"     # factual lookups, short rewrites  — cost weight 1x
    medium = "medium"   # summaries, structured extraction — cost weight 4x
    large = "large"     # reasoning, long generation, code — cost weight 15x


async def classify(text: str) -> ModelTier:
    """Return the cheapest tier that can handle this prompt."""
    try:
        return await _ollama_classify(text)
    except Exception:
        # Ollama unavailable or timed out — heuristics are good enough for v0.
        return _heuristic_classify(text)


async def _ollama_classify(text: str) -> ModelTier:
    """Ask a tiny local model to classify complexity. Timeout = 2s."""
    system = (
        "Classify the following prompt into exactly one word: small, medium, or large.\n"
        "small = simple factual question or one-sentence rewrite.\n"
        "medium = summary, translation, or structured data extraction.\n"
        "large = code generation, multi-step reasoning, or long output.\n"
        "Reply with only one word."
    )
    async with httpx.AsyncClient(timeout=2.0) as client:
        resp = await client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.classifier_model,
                "prompt": f"{system}\n\nPrompt: {text[:300]}",
                "stream": False,
            },
        )
        resp.raise_for_status()
        answer = resp.json().get("response", "").strip().lower()

    if "large" in answer:
        return ModelTier.large
    if "medium" in answer:
        return ModelTier.medium
    return ModelTier.small


def _heuristic_classify(text: str) -> ModelTier:
    """Rule-based fallback — no network calls, always < 1ms."""
    words = text.split()

    # Code is always large — even short code prompts need a capable model.
    if any(marker in text for marker in _CODE_MARKERS):
        return ModelTier.large

    # Long prompts or complex reasoning → large.
    if len(words) > 150 or any(kw in text.lower() for kw in _REASONING_KEYWORDS):
        return ModelTier.large

    # Mid-length prompts → medium.
    if len(words) > 40:
        return ModelTier.medium

    return ModelTier.small
