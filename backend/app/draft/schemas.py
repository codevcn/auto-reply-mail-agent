"""Pydantic schemas for Reply Drafts, Immutable Versioning, and Approvals."""

from __future__ import annotations

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


class DraftVersionDTO(BaseModel):
    """Data transfer object for an immutable reply draft version."""

    id: uuid.UUID
    draft_id: uuid.UUID
    incoming_email_id: uuid.UUID
    version_number: int
    subject: str
    body_text: str
    body_html: str
    language: str
    source: str  # 'ai' | 'user'
    ai_provider: str | None = None
    ai_model: str | None = None
    prompt_version: str | None = None
    order_snapshot_id: uuid.UUID | None = None
    product_snapshot_id: uuid.UUID | None = None
    policy_hashes_used: dict[str, str] | None = None
    warning_codes: list[str] = Field(default_factory=list)
    content_hash: str
    is_current_version: bool
    created_by: uuid.UUID | None = None
    created_at: datetime.datetime

    model_config = ConfigDict(from_attributes=True)


class ReplyDraftDetailResponse(BaseModel):
    """Complete detail response of a ReplyDraft with full version history and stale status."""

    draft_id: uuid.UUID
    incoming_email_id: uuid.UUID
    store_profile_id: uuid.UUID
    status: str
    current_version_number: int
    current_version: DraftVersionDTO | None = None
    versions: list[DraftVersionDTO] = Field(default_factory=list)
    is_stale: bool = False
    stale_reason: str | None = None
    stale_details: list[dict[str, str]] | None = None
    warning_codes: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class CreateDraftVersionRequest(BaseModel):
    """Payload to save a manual edit as a new immutable draft version."""

    subject: str = Field(..., min_length=1, max_length=500)
    body_text: str = Field(..., min_length=1)
    body_html: str | None = None
    base_version_number: int | None = None


class RegenerateDraftRequest(BaseModel):
    """Payload to request AI regeneration of a draft with specified language or instructions."""

    target_language: str | None = None
    custom_instructions: str | None = None


class RejectDraftRequest(BaseModel):
    """Payload to reject a draft or mark as no reply needed."""

    reason: str | None = None
    action_type: str = "no_reply_needed"  # 'no_reply_needed' | 'manual_review' | 'rejected'
