"""Schemas for Email Queues, Items, and Stats."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, Field


class EmailListItem(BaseModel):
    """Summarized email metadata item for queue list display."""

    id: uuid.UUID
    store_profile_id: uuid.UUID
    store_name: str
    from_address: str
    sender_name: str | None = None
    subject: str
    received_at: datetime.datetime
    processing_status: str
    intent: str | None = None
    intent_confidence: float | None = None
    customer_status: str | None = None
    spam_status: str | None = None
    has_attachments: bool = False
    attachment_count: int = 0
    current_draft_version: int = 0
    manual_review_reason: str | None = None
    review_reason_code: str | None = None
    spam_score: float | None = None
    detected_language: str | None = None


class OverrideClassificationRequest(BaseModel):
    """Payload for manual operator override of email classification."""

    intent: str
    customer_status: str = "no_order"
    spam_status: str = "not_spam"
    generate_draft: bool = True
    notes: str | None = None


class EmailQueueListResponse(BaseModel):
    """Paginated list of email items in a specific queue."""

    items: list[EmailListItem]
    total: int
    page: int
    limit: int
    total_pages: int
    has_next: bool
    has_prev: bool


class QueueStatsResponse(BaseModel):
    """Aggregate statistics for all 7 mail queues supporting real-time badges."""

    ready_to_review: int = 0
    needs_manual_review: int = 0
    product_inquiry: int = 0
    recent_order: int = 0
    complaint: int = 0
    spam: int = 0
    sent: int = 0
    total_unprocessed: int = 0

    # Frontend aliases for backwards compatibility
    ready: int = 0
    manual: int = 0
    product: int = 0

    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.UTC)
    )
