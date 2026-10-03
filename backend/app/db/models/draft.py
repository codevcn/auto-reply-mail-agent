"""Database models for Phase 6: Reply Drafts, Immutable Draft Versions, and Delivery Attempts."""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, JSONVariant, UTCDateTime

if TYPE_CHECKING:
    from app.db.models.email import IncomingEmail
    from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
    from app.db.models.store import StoreProfile
    from app.db.models.user import User


class ReplyDraft(Base):
    """Container entity representing the reply draft state for an incoming email.

    Maintains the current version reference and overall approval status.
    """

    __tablename__ = "reply_drafts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("incoming_emails.id", ondelete="CASCADE"),
        unique=True,
        index=True,
        nullable=False,
    )
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("store_profiles.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey(
            "reply_draft_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_reply_drafts_current_version_id",
        ),
        nullable=True,
    )
    current_version_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending_approval",
        index=True,
        nullable=False,
    )  # pending_approval, approved, sending, sent, rejected, delivery_unknown, no_reply_needed

    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    incoming_email: Mapped[IncomingEmail] = relationship("IncomingEmail", back_populates="reply_draft_rel")
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile")
    current_version: Mapped[ReplyDraftVersion | None] = relationship(
        "ReplyDraftVersion",
        foreign_keys=[current_version_id],
        post_update=True,
    )
    versions: Mapped[list[ReplyDraftVersion]] = relationship(
        "ReplyDraftVersion",
        back_populates="draft",
        foreign_keys="ReplyDraftVersion.draft_id",
        cascade="all, delete-orphan",
        order_by="ReplyDraftVersion.version_number",
    )
    delivery_attempts: Mapped[list[ReplyDeliveryAttempt]] = relationship(
        "ReplyDeliveryAttempt",
        back_populates="draft",
        cascade="all, delete-orphan",
    )


class ReplyDraftVersion(Base):
    """Immutable snapshot of a reply draft version.

    INVARIANT R-24: Every edit or AI regeneration creates a new version record.
    Past versions cannot be mutated.
    """

    __tablename__ = "reply_draft_versions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("reply_drafts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("incoming_emails.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    source: Mapped[str] = mapped_column(
        String(20), default="ai", nullable=False
    )  # 'ai' | 'user'
    ai_provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    order_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("shopify_order_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    product_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("shopify_product_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    policy_hashes_used: Mapped[dict[str, str] | None] = mapped_column(JSONVariant, nullable=True)
    warning_codes: Mapped[list[str]] = mapped_column(JSONVariant, default=list, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of subject + body_text
    is_current_version: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    draft: Mapped[ReplyDraft] = relationship(
        "ReplyDraft",
        back_populates="versions",
        foreign_keys=[draft_id],
    )
    creator: Mapped[User | None] = relationship("User")
    order_snapshot: Mapped[ShopifyOrderSnapshot | None] = relationship("ShopifyOrderSnapshot")
    product_snapshot: Mapped[ShopifyProductSnapshot | None] = relationship("ShopifyProductSnapshot")

    __table_args__ = (
        UniqueConstraint("draft_id", "version_number", name="uq_reply_draft_versions_draft_version"),
        Index("ix_reply_draft_versions_draft_current", "draft_id", "is_current_version"),
    )


class ReplyDeliveryAttempt(Base):
    """Send operation tracking and idempotency enforcement.

    INVARIANT R-01: 100% Human approval before sending.
    INVARIANT R-26: Unique Idempotency Key prevents double-sending.
    INVARIANT R-25: SMTP delivery and IMAP Sent-folder copy tracking.
    """

    __tablename__ = "reply_delivery_attempts"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("incoming_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    draft_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("reply_drafts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    draft_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("reply_draft_versions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    approved_by: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="queued", index=True, nullable=False
    )  # queued, sending, sent, delivery_unknown, failed
    outgoing_message_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, index=True, nullable=True
    )
    smtp_started_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    smtp_completed_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    smtp_response_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_folder_append_status: Mapped[str] = mapped_column(
        String(30), default="pending", nullable=False
    )  # pending, success, failed, skipped
    sent_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    draft: Mapped[ReplyDraft] = relationship("ReplyDraft", back_populates="delivery_attempts")
    draft_version: Mapped[ReplyDraftVersion] = relationship("ReplyDraftVersion")
    approver: Mapped[User] = relationship("User")
