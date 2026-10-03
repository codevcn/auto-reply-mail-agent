"""Pydantic schemas for Data Retention & Cleanup (Invariant R-34)."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class RetentionRunRequest(BaseModel):
    """Request payload for manual retention cleanup trigger."""

    retention_days: int | None = Field(
        default=None,
        ge=1,
        le=120,
        description="Override retention days (strictly <= 120). Defaults to server configuration.",
    )
    dry_run: bool = Field(
        default=False,
        description="If True, only simulates and counts expired records without deleting.",
    )
    batch_size: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Chunk size for batch processing.",
    )


class RetentionDeletedCounts(BaseModel):
    """Aggregate counts of deleted or identified records per table."""

    incoming_emails: int = 0
    reply_drafts: int = 0
    reply_draft_versions: int = 0
    reply_delivery_attempts: int = 0
    shopify_order_snapshots: int = 0
    shopify_product_snapshots: int = 0
    email_classifications: int = 0
    email_attachment_metadata: int = 0
    email_jobs: int = 0
    audit_events: int = 0


class RetentionCleanupReport(BaseModel):
    """Comprehensive execution report of retention cleanup job."""

    timestamp: datetime.datetime
    retention_days: int
    cutoff_date: datetime.datetime
    dry_run: bool
    duration_ms: float
    deleted_counts: RetentionDeletedCounts
    total_records_affected: int
    status: str = "completed"
    error_message: str | None = None


class RetentionStatusResponse(BaseModel):
    """Current retention configuration and estimate of pending expired records."""

    configured_retention_days: int
    current_cutoff_date: datetime.datetime
    batch_size: int
    scheduler_enabled: bool
    scheduler_interval_hours: int
    pending_expired_emails_count: int
    pending_expired_audit_events_count: int
    last_cleanup_report: RetentionCleanupReport | None = None
