"""Immutable Audit Event database model."""

import datetime
import uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, JSONVariant, UTCDateTime

if TYPE_CHECKING:
    from app.db.models.store import StoreProfile
    from app.db.models.user import User


class AuditEvent(Base):
    """Immutable audit trail for compliance, security events, and operator actions."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    store_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID, ForeignKey("store_profiles.id", ondelete="SET NULL"), index=True, nullable=True
    )
    request_correlation_id: Mapped[str | None] = mapped_column(
        String(100), index=True, nullable=True
    )
    source_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    safe_change_summary: Mapped[dict[str, Any] | None] = mapped_column(
        JSONVariant, nullable=True
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), index=True, nullable=False
    )

    # Relationships
    actor: Mapped[Optional["User"]] = relationship(
        "User", back_populates="audit_events", foreign_keys=[actor_user_id]
    )
    store_profile: Mapped[Optional["StoreProfile"]] = relationship(
        "StoreProfile", back_populates="audit_events", foreign_keys=[store_profile_id]
    )
