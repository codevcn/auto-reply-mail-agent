"""StoreProfile and subordinate configuration models for Multi-Store isolation and credentials management."""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, JSONVariant, UTCDateTime

if TYPE_CHECKING:
    from app.db.models.audit import AuditEvent


class ProxyProfile(Base):
    """SOCKS5 / HTTP Proxy entity shared across one or more store profiles."""

    __tablename__ = "proxy_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    protocol: Mapped[str] = mapped_column(String(20), default="socks5", nullable=False)  # socks5 | http
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    encrypted_username: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_key_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    connect_timeout_seconds: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_test_status: Mapped[str | None] = mapped_column(String(30), nullable=True)  # success | failed | untested
    last_tested_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_exit_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_detected_country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    store_profiles: Mapped[list[StoreProfile]] = relationship("StoreProfile", back_populates="proxy_profile")
    shopify_connections: Mapped[list[ShopifyConnection]] = relationship(
        "ShopifyConnection", back_populates="proxy_profile"
    )


class ShopifyConnection(Base):
    """Shopify Admin API connection credentials and cached OAuth access token."""

    __tablename__ = "shopify_connections"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), unique=True, index=True, nullable=False
    )
    shop_domain: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)  # *.myshopify.com
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    encrypted_client_secret: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    granted_scopes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    auth_status: Mapped[str] = mapped_column(
        String(30), default="unconfigured", nullable=False
    )  # unconfigured | authenticated | expired | failed
    last_auth_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_connected_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    proxy_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("proxy_profiles.id", ondelete="SET NULL"), nullable=True
    )
    secret_key_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile", back_populates="shopify_connection")
    proxy_profile: Mapped[ProxyProfile | None] = relationship("ProxyProfile", back_populates="shopify_connections")


class Mailbox(Base):
    """Customer support mailbox configuration (IMAP/SMTP)."""

    __tablename__ = "mailboxes"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    address: Mapped[str] = mapped_column(String(255), index=True, nullable=False)  # Normalized lowercase
    encrypted_password: Mapped[str] = mapped_column(Text, nullable=False)
    secret_key_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    imap_host: Mapped[str] = mapped_column(String(255), default="mail.wrydeco.com", nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, default=993, nullable=False)
    imap_tls_mode: Mapped[str] = mapped_column(String(20), default="SSL", nullable=False)  # SSL | STARTTLS
    smtp_host: Mapped[str] = mapped_column(String(255), default="mail.wrydeco.com", nullable=False)
    smtp_port: Mapped[int] = mapped_column(Integer, default=587, nullable=False)
    smtp_tls_mode: Mapped[str] = mapped_column(String(20), default="STARTTLS", nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), default="unconfigured", nullable=False
    )  # unconfigured | active | error
    last_imap_success_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_smtp_auth_success_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile", back_populates="mailboxes")


class CustomPolicy(Base):
    """Store-specific custom text policies (warranty, cancellation)."""

    __tablename__ = "custom_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    policy_type: Mapped[str] = mapped_column(String(50), index=True, nullable=False)  # warranty_policy | cancellation_policy
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile", back_populates="custom_policies")


class StorePolicy(Base):
    """Store policies synchronized from Shopify or created as custom store terms."""

    __tablename__ = "store_policies"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    policy_type: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False
    )  # REFUND, PRIVACY, TERMS, SHIPPING, CONTACT, LEGAL, WARRANTY, CANCELLATION, etc.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hex
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    synced_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile", back_populates="policies")

    __table_args__ = (
        UniqueConstraint("store_profile_id", "policy_type", name="uq_store_policies_store_policy_type"),
    )


class StoreProfile(Base):
    """Store Profile entity holding brand persona, policies, and mailbox configurations."""

    __tablename__ = "store_profiles"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    brand_name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_domain: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_domain: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    brand_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_language: Mapped[str] = mapped_column(String(10), default="en", nullable=False)
    tone_of_voice: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email_signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    common_faqs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONVariant, default=list, nullable=True)
    forbidden_claims: Mapped[list[str] | None] = mapped_column(JSONVariant, default=list, nullable=True)
    escalation_rules: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONVariant, default=list, nullable=True)
    additional_ai_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_warranty_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_cancellation_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(30), default="draft", index=True, nullable=False
    )  # draft | active | paused | connection_error | archived
    proxy_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("proxy_profiles.id", ondelete="SET NULL"), nullable=True
    )
    activation_baseline_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uid_validity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secret_key_version: Mapped[str] = mapped_column(String(20), default="v1", nullable=False)
    active_context_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    proxy_profile: Mapped[ProxyProfile | None] = relationship("ProxyProfile", back_populates="store_profiles")
    shopify_connection: Mapped[ShopifyConnection | None] = relationship(
        "ShopifyConnection", back_populates="store_profile", uselist=False, cascade="all, delete-orphan"
    )
    mailboxes: Mapped[list[Mailbox]] = relationship(
        "Mailbox", back_populates="store_profile", cascade="all, delete-orphan"
    )
    custom_policies: Mapped[list[CustomPolicy]] = relationship(
        "CustomPolicy", back_populates="store_profile", cascade="all, delete-orphan"
    )
    policies: Mapped[list[StorePolicy]] = relationship(
        "StorePolicy", back_populates="store_profile", cascade="all, delete-orphan"
    )
    audit_events: Mapped[list[AuditEvent]] = relationship(
        "AuditEvent", back_populates="store_profile"
    )
