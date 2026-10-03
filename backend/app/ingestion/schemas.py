"""Schemas for Mail Ingestion and On-Demand Content Fetching."""

from __future__ import annotations

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class IngestionResult(BaseModel):
    """Result of an ingestion run (either IDLE or Reconciliation)."""

    mailbox_id: uuid.UUID
    folder: str = "INBOX"
    new_emails_count: int = 0
    enqueued_jobs_count: int = 0
    skipped_count: int = 0
    highest_uid: int | None = None
    uid_validity: int | None = None
    status: str = "success"  # success, uidvalidity_changed, error
    error_detail: str | None = None


class EmailContentResponse(BaseModel):
    """On-demand fetched email content directly from mailserver."""

    email_id: uuid.UUID
    mailbox_address: str
    imap_uid: int
    subject: str
    sender_email: str
    sender_name: str | None = None
    recipient_email: str
    received_at: datetime.datetime
    body_text: str = ""
    body_html_sanitized: str = ""
    has_attachments: bool = False
    attachment_count: int = 0
    attachments_metadata: list[dict[str, Any]] = Field(default_factory=list)

    # Phase 5 Enrichment & Stale Draft Fields
    order_snapshot: dict[str, Any] | None = None
    product_snapshot: dict[str, Any] | None = None
    is_stale: bool = False
    stale_reason: str | None = None
    stale_details: list[dict[str, Any]] = Field(default_factory=list)
    policy_hashes_used: dict[str, str] | None = None
    customer_status: str | None = None
    review_reason_code: str | None = None
    manual_review_reason: str | None = None
    intent: str | None = None
    spam_status: str | None = None
