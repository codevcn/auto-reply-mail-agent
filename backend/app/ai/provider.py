"""Abstract Base Class for AI Provider integration."""

from __future__ import annotations

import abc

from app.ai.schemas import (
    AITestConnectionResult,
    AIUsageMetadata,
    ClassificationResult,
    DraftGenerationInput,
    DraftResult,
    EmailClassificationInput,
    StoreContext,
)


class AIProvider(abc.ABC):
    """Contract for email classification and response draft generation.

    INVARIANT R-19: Implementations MUST connect directly over HTTPS without
    using the Shopify SOCKS5 proxy.
    """

    @abc.abstractmethod
    async def classify_email(
        self,
        email_input: EmailClassificationInput,
        store_context: StoreContext,
    ) -> ClassificationResult:
        """Performs 3D classification, prompt injection detection, and entity extraction."""
        pass

    @abc.abstractmethod
    async def generate_reply_draft(
        self,
        draft_input: DraftGenerationInput,
        store_context: StoreContext,
    ) -> DraftResult:
        """Generates an email reply draft according to business context (Phase 6)."""
        pass

    @abc.abstractmethod
    async def test_connection(self) -> AITestConnectionResult:
        """Executes a synthetic test to verify API connectivity and credentials."""
        pass

    @abc.abstractmethod
    def get_usage_metadata(self) -> AIUsageMetadata:
        """Returns runtime token usage and invocation metrics."""
        pass
