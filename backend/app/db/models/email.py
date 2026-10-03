"""Database models for Mail Ingestion, Metadata tracking, and Transactional Queue."""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
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
    from app.db.models.draft import ReplyDraft
    from app.db.models.shopify import ShopifyOrderSnapshot, ShopifyProductSnapshot
    from app.db.models.store import Mailbox, StoreProfile
    from app.db.models.user import User


class MailboxCheckpoint(Base):
    """Tracks per-folder IMAP synchronization state and baseline UID safety boundaries."""

    __tablename__ = "mailbox_checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("mailboxes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    folder: Mapped[str] = mapped_column(String(50), default="INBOX", nullable=False)
    uid_validity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    activation_baseline_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_durably_enqueued_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_reconciled_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    idle_connected_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    idle_heartbeat_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    state: Mapped[str] = mapped_column(
        String(30), default="active", nullable=False
    )  # active, idle, reconciling, paused, uidvalidity_changed, error
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    mailbox: Mapped[Mailbox] = relationship("Mailbox", backref="checkpoints")

    __table_args__ = (
        UniqueConstraint("mailbox_id", "folder", name="uq_mailbox_checkpoints_mailbox_folder"),
    )


class IncomingEmail(Base):
    """Stores purely email metadata and IMAP references.

    INVARIANT R-04: ZERO raw body or attachments are stored in the database.
    Email body and attachments must be fetched on-demand from the mailserver.
    """

    __tablename__ = "incoming_emails"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    mailbox_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("mailboxes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    folder: Mapped[str] = mapped_column(String(50), default="INBOX", nullable=False)
    imap_uid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uidvalidity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    message_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    sender_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_email: Mapped[str] = mapped_column(String(255), nullable=False)
    reply_to_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    to_addresses: Mapped[list[str] | None] = mapped_column(JSONVariant, default=list, nullable=True)
    cc_addresses: Mapped[list[str] | None] = mapped_column(JSONVariant, default=list, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    received_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, index=True, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="pending", index=True, nullable=False
    )  # pending, classified, drafted, manual_review, sent, spam, pending_approval
    classification_category: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # product_inquiry, order_support, complaint, return_or_refund, spam, other
    customer_status: Mapped[str | None] = mapped_column(
        String(50), default="no_order", nullable=True
    )  # has_order_record, no_order
    spam_status: Mapped[str | None] = mapped_column(
        String(30), default="not_spam", nullable=True
    )  # not_spam, spam, uncertain
    spam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    review_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    extracted_entities: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    intent_confidence: Mapped[float | None] = mapped_column(nullable=True)
    current_draft_version: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    has_attachments: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    attachment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    manual_review_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    order_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("shopify_order_snapshots.id", ondelete="SET NULL", use_alter=True, name="fk_incoming_emails_order_snapshot_id"),
        nullable=True,
    )
    product_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("shopify_product_snapshots.id", ondelete="SET NULL", use_alter=True, name="fk_incoming_emails_product_snapshot_id"),
        nullable=True,
    )
    policy_hashes_used: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    stale_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stale_details: Mapped[list[dict] | None] = mapped_column(JSONVariant, nullable=True)
    first_detected_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    last_transition_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile", backref="incoming_emails")
    mailbox: Mapped[Mailbox] = relationship("Mailbox", backref="incoming_emails")
    jobs: Mapped[list[EmailJob]] = relationship(
        "EmailJob", back_populates="incoming_email", cascade="all, delete-orphan"
    )
    classifications: Mapped[list[EmailClassification]] = relationship(
        "EmailClassification", back_populates="incoming_email", cascade="all, delete-orphan"
    )
    attachments_metadata: Mapped[list[EmailAttachmentMetadata]] = relationship(
        "EmailAttachmentMetadata", back_populates="incoming_email", cascade="all, delete-orphan"
    )
    order_snapshots: Mapped[list[ShopifyOrderSnapshot]] = relationship(
        "ShopifyOrderSnapshot", back_populates="incoming_email", foreign_keys="ShopifyOrderSnapshot.incoming_email_id", cascade="all, delete-orphan"
    )
    product_snapshots: Mapped[list[ShopifyProductSnapshot]] = relationship(
        "ShopifyProductSnapshot", back_populates="incoming_email", foreign_keys="ShopifyProductSnapshot.incoming_email_id", cascade="all, delete-orphan"
    )
    reply_draft_rel: Mapped[ReplyDraft | None] = relationship(
        "ReplyDraft", back_populates="incoming_email", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "mailbox_id", "folder", "uidvalidity", "imap_uid", name="uq_incoming_emails_uid"
        ),
        Index(
            "ix_incoming_emails_store_status_received",
            "store_profile_id",
            "status",
            "received_at",
        ),
    )


class EmailJob(Base):
    """Transactional queue job for asynchronous processing of email workflow stages.

    Supports FOR UPDATE SKIP LOCKED on PostgreSQL with graceful fallback on SQLite.
    """

    __tablename__ = "email_jobs"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("incoming_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    job_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # classify, enrich, generate_draft
    status: Mapped[str] = mapped_column(
        String(30), default="queued", index=True, nullable=False
    )  # queued, processing, completed, failed
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    scheduled_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), index=True, nullable=False
    )
    locked_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Relationships
    incoming_email: Mapped[IncomingEmail] = relationship("IncomingEmail", back_populates="jobs")
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile")

    __table_args__ = (
        UniqueConstraint("incoming_email_id", "job_type", name="uq_email_jobs_email_jobtype"),
        Index("ix_email_jobs_status_scheduled", "status", "scheduled_at"),
    )


class EmailClassification(Base):
    """Stores multi-dimensional classification results with audit and override history."""

    __tablename__ = "email_classifications"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("incoming_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(20), default="ai", nullable=False
    )  # ai, user, rule
    provider_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    spam_status: Mapped[str] = mapped_column(
        String(30), default="not_spam", nullable=False
    )  # spam, not_spam, uncertain
    spam_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    customer_status: Mapped[str] = mapped_column(
        String(50), default="no_order", nullable=False
    )  # has_order_record, no_order, lookup_unavailable, not_checked
    intent: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # product_inquiry, order_support, complaint, return_or_refund, partnership, other, uncertain
    order_state_flags: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    confidence: Mapped[dict | None] = mapped_column(JSONVariant, nullable=True)
    reason_codes: Mapped[list[str] | None] = mapped_column(JSONVariant, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    requires_manual_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    review_reason_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    superseded_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Relationships
    incoming_email: Mapped[IncomingEmail] = relationship(
        "IncomingEmail", back_populates="classifications"
    )
    creator: Mapped[User | None] = relationship("User")


class EmailAttachmentMetadata(Base):
    """Stores safe attachment metadata, integrity hash, and validation status.

    INVARIANT R-04: ZERO raw binary bytes stored in database. Content is fetched
    on-demand from the mailserver via IMAP UID when requested by authenticated users.
    """

    __tablename__ = "email_attachment_metadata"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("incoming_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_inline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_valid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    validation_error: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    incoming_email: Mapped[IncomingEmail] = relationship(
        "IncomingEmail", back_populates="attachments_metadata"
    )
