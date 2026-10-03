"""AI module export."""

from app.ai.mock import MockAIProvider
from app.ai.provider import AIProvider
from app.ai.schemas import (
    ClassificationResult,
    EmailClassificationInput,
    ExtractedEntities,
    OrderFlags,
    StoreContext,
)
from app.ai.service import get_ai_provider
from app.ai.vertex import VertexGeminiProvider

__all__ = [
    "AIProvider",
    "ClassificationResult",
    "EmailClassificationInput",
    "ExtractedEntities",
    "MockAIProvider",
    "OrderFlags",
    "StoreContext",
    "VertexGeminiProvider",
    "get_ai_provider",
]
