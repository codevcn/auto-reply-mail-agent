"""Database models for Shopify enrichment snapshots (Orders and Products).

INVARIANTS:
- R-09: 60-day order lookup window, non-test orders only.
- R-10: 5 independent boolean status flags, preserves has_order_record on cancel/refund.
- R-11: Explicit separation of lookup_unavailable and no_order.
- R-16: Live product search, No Catalog Mirroring, immutable audit snapshots.
"""

from __future__ import annotations

import datetime
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, JSONVariant, UTCDateTime

if TYPE_CHECKING:
    from app.db.models.email import IncomingEmail
    from app.db.models.store import StoreProfile


class ShopifyOrderSnapshot(Base):
    """Immutable audit snapshot of Shopify 60-day order lookups."""

    __tablename__ = "shopify_order_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("incoming_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    customer_email: Mapped[str] = mapped_column(String(255), index=True, nullable=False)

    # lookup_status: 'success', 'no_order', 'lookup_unavailable'
    lookup_status: Mapped[str] = mapped_column(String(30), nullable=False)

    # 5 Independent boolean flags (Invariant R-10)
    matched_order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    has_paid_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_active_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_cancelled_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_refunded_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    has_fulfilled_order: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Latest order facts (if any matched)
    latest_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latest_order_name: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. #1001
    latest_order_created_at: Mapped[datetime.datetime | None] = mapped_column(UTCDateTime, nullable=True)
    latest_order_financial_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latest_order_fulfillment_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latest_order_total_price: Mapped[str | None] = mapped_column(String(30), nullable=True)
    latest_order_currency: Mapped[str | None] = mapped_column(String(10), default="USD", nullable=True)

    # Lightweight line items summary (without raw customer payload)
    line_items_summary: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONVariant, nullable=True)

    # Diagnostics & Timing
    lookup_checked_at: Mapped[datetime.datetime] = mapped_column(UTCDateTime, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )

    # Relationships
    incoming_email: Mapped[IncomingEmail] = relationship(
        "IncomingEmail", back_populates="order_snapshots", foreign_keys=[incoming_email_id]
    )
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile")


class ShopifyProductSnapshot(Base):
    """Immutable audit snapshot of Shopify on-demand live product search.

    INVARIANT R-16: Zero Catalog Mirroring. Only queried items for the email are frozen.
    """

    __tablename__ = "shopify_product_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    store_profile_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="CASCADE"), index=True, nullable=False
    )
    incoming_email_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("incoming_emails.id", ondelete="CASCADE"), index=True, nullable=False
    )
    search_query: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_query_terms: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    product_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    matched_products: Mapped[list[dict[str, Any]]] = mapped_column(JSONVariant, nullable=False)
    warning_codes: Mapped[list[str]] = mapped_column(JSONVariant, nullable=False)
    execution_time_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fetched_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    # Relationships
    incoming_email: Mapped[IncomingEmail] = relationship(
        "IncomingEmail", back_populates="product_snapshots", foreign_keys=[incoming_email_id]
    )
    store_profile: Mapped[StoreProfile] = relationship("StoreProfile")
