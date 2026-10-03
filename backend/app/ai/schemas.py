"""Pydantic schemas for AIProvider integration and 3D email classification."""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.attachment.schemas import AttachmentMetadataDTO

SpamStatusType = Literal["spam", "not_spam", "uncertain"]
OrderStatusType = Literal["has_order_record", "no_order", "lookup_unavailable", "not_checked"]
IntentType = Literal[
    "product_inquiry",
    "order_support",
    "complaint",
    "return_or_refund",
    "partnership",
    "other",
    "uncertain",
]


class OrderFlags(BaseModel):
    """Boolean order state flags retained per Invariants R-07 and R-10."""

    has_paid_order: bool = False
    has_active_order: bool = False
    has_cancelled_order: bool = False
    has_refunded_order: bool = False
    has_fulfilled_order: bool = False


class ExtractedEntities(BaseModel):
    """Entities extracted from customer email and attachments."""

    order_numbers: list[str] = Field(default_factory=list)
    tracking_numbers: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)


class StoreContext(BaseModel):
    """Store profile business context provided to AI for classification and reply drafting."""

    store_profile_id: uuid.UUID
    brand_name: str
    public_domain: str
    canonical_domain: str | None = None
    industry: str | None = None
    brand_description: str | None = None
    default_language: str = "en"
    tone_of_voice: str | None = "Professional, helpful, empathetic"
    forbidden_claims: list[str] = Field(default_factory=list)
    additional_ai_instructions: str | None = None


class EmailClassificationInput(BaseModel):
    """Input payload delivered to AIProvider."""

    email_id: uuid.UUID
    mailbox_address: str
    sender_email: str
    sender_name: str | None = None
    recipient_email: str
    subject: str
    body_text: str
    body_html_sanitized: str | None = None
    received_at: datetime.datetime
    attachments: list[AttachmentMetadataDTO] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ClassificationResult(BaseModel):
    """Complete 3-dimensional classification result returned by AIProvider."""

    # Dimension 1: Spam status & score
    is_spam: bool = False
    spam_status: SpamStatusType = "not_spam"
    spam_score: float = Field(default=0.0, ge=0.0, le=1.0)
    spam_reason: str | None = None

    # Dimension 2: Customer / Order status
    order_status: OrderStatusType = "not_checked"
    order_flags: OrderFlags = Field(default_factory=OrderFlags)

    # Dimension 3: Intent
    intent: IntentType = "product_inquiry"
    confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)

    # Language & Entities
    detected_language: str = "en"
    entities: ExtractedEntities = Field(default_factory=ExtractedEntities)

    # Security & Manual Review routing
    requires_manual_review: bool = False
    review_reason_code: str | None = None
    is_prompt_injection: bool = False
    reasoning_summary: str = ""

    # Provider metadata
    provider_type: str = "vertex_gemini"
    model_identifier: str = ""
    prompt_version: str = "v1.0"
    execution_time_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class DraftGenerationInput(BaseModel):
    """Input payload for generating reply drafts (Phase 6)."""

    email_id: uuid.UUID
    subject: str
    body_text: str
    classification: ClassificationResult
    order_snapshot: dict[str, Any] | None = None
    product_snapshot: dict[str, Any] | None = None
    policies: dict[str, str] = Field(default_factory=dict)
    target_language: str | None = None


class DraftResult(BaseModel):
    """Generated reply draft result (Phase 6)."""

    subject: str
    body_text: str
    body_html: str
    language: str
    warning_codes: list[str] = Field(default_factory=list)
    provider_type: str
    model_identifier: str


class AITestConnectionResult(BaseModel):
    """Diagnostics returned by AIProvider health test."""

    success: bool
    provider_type: str
    model_tested: str
    latency_ms: int
    direct_https_verified: bool
    sample_response: str | None = None
    error_message: str | None = None


class AIUsageMetadata(BaseModel):
    """Aggregated token usage and invocation metrics."""

    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    last_call_at: datetime.datetime | None = None
