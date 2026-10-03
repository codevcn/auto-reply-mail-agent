"""AI Provider Factory Service."""

from __future__ import annotations

from app.ai.mock import MockAIProvider
from app.ai.provider import AIProvider
from app.ai.vertex import VertexGeminiProvider
from app.config import get_settings


def get_ai_provider(provider_type: str | None = None) -> AIProvider:
    """Returns configured AI Provider instance."""
    settings = get_settings()
    active_type = (provider_type or settings.AI_PROVIDER_TYPE).strip().lower()

    if active_type in ("vertex", "vertex_gemini", "gemini"):
        return VertexGeminiProvider()

    # Default to deterministic MockAIProvider for offline testing & dev
    return MockAIProvider()
