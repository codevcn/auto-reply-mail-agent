"""Opaque Server-Side Session database model."""

import datetime
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import GUID, UTCDateTime

if TYPE_CHECKING:
    from app.db.models.user import User


class Session(Base):
    """Server-side session storage holding SHA-256 hashed opaque tokens."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    csrf_secret_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        UTCDateTime, index=True, nullable=False
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        UTCDateTime, index=True, nullable=True
    )
    created_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_seen_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent_summary: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions", lazy="selectin")

    @property
    def is_valid(self) -> bool:
        """Returns True if the session has not been revoked and has not expired."""
        now = datetime.datetime.now(datetime.UTC)
        return self.revoked_at is None and self.expires_at > now
