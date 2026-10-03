"""Pydantic schemas for Reply Delivery and Send Idempotency enforcement."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ApproveAndSendRequest(BaseModel):
    """Payload to trigger human-approved sending of a reply draft."""

    draft_version_id: uuid.UUID | None = None
    custom_notes: str | None = None
    override_recipient: str | None = None


class ApproveAndSendResponse(BaseModel):
    """Response returned upon successful or idempotent email delivery."""

    success: bool
    status: str  # 'sent'
    email_id: uuid.UUID
    delivery_attempt_id: uuid.UUID
    outgoing_message_id: str
    smtp_response: str | None = None
    sent_folder_append_status: str  # 'success' | 'failed' | 'pending'
    sent_at: datetime.datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ManualResolveRequest(BaseModel):
    """Payload for operator resolution of ambiguous delivery_unknown state."""

    notes: str = Field(..., min_length=1, description="Operator explanation of resolution")
