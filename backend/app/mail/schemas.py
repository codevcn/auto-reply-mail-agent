"""Pydantic schemas for Mailbox configuration, tests, and metadata."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

EXCLUDED_DOMAINS = ["piezaprint.com", "piezaprint"]


def validate_not_piezaprint(email_val: str) -> str:
    cleaned = email_val.strip().lower()
    if any(exc in cleaned for exc in EXCLUDED_DOMAINS):
        raise ValueError("STORE_EXCLUDED_FROM_SYSTEM")
    return cleaned


class MailboxCandidateTestRequest(BaseModel):
    """Candidate mailbox test request during setup wizard or editing."""

    model_config = ConfigDict(extra="forbid")

    address: str = Field(
        ..., min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    )
    password: str = Field(..., min_length=1, description="Plaintext password for candidate test")
    imap_host: str = Field(default="mail.wrydeco.com", max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_tls_mode: str = Field(default="SSL", pattern="^(SSL|STARTTLS)$")
    smtp_host: str = Field(default="mail.wrydeco.com", max_length=255)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_tls_mode: str = Field(default="STARTTLS", pattern="^(STARTTLS|SSL)$")
    test_imap: bool = True
    test_smtp: bool = True

    @field_validator("address", mode="after")
    @classmethod
    def check_piezaprint(cls, v: str) -> str:
        return validate_not_piezaprint(str(v))


class IMAPTestResult(BaseModel):
    """Structured IMAP connection test outcome."""

    success: bool
    uid_validity: int | None = None
    uid_next: int | None = None
    message_count: int | None = None
    unseen_count: int | None = None
    capabilities: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    detail: str | None = None


class SMTPTestResult(BaseModel):
    """Structured SMTP connection test outcome."""

    success: bool
    auth_success: bool = False
    starttls_supported: bool = False
    capabilities: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    detail: str | None = None


class MailboxConnectionTestResponse(BaseModel):
    """Aggregated response for Mailbox connection testing."""

    success: bool
    address: str
    tested_at: datetime.datetime
    overall_latency_ms: int
    imap: IMAPTestResult
    smtp: SMTPTestResult
    error_code: str | None = None
    detail: str | None = None


class EmailHeaderMetadata(BaseModel):
    """Parsed email header metadata (BODY.PEEK without mutating Seen flag)."""

    uid: int
    message_id: str | None = None
    subject: str = ""
    from_address: str
    sender_name: str | None = None
    reply_to: str | None = None
    to_addresses: list[str] = Field(default_factory=list)
    cc_addresses: list[str] = Field(default_factory=list)
    date: datetime.datetime | None = None
    is_auto_submitted: bool = False
    precedence: str | None = None
    list_id: str | None = None
    content_type: str = "text/plain"
    flags: list[str] = Field(default_factory=list)
    has_attachments: bool = False
    attachment_count: int = 0


class SMTPDeliveryResult(BaseModel):
    """Result of an SMTP message delivery attempt (Phase 6)."""

    status: str  # "sent" | "failed" | "delivery_unknown"
    response_code: int | None = None
    response_text: str | None = None
    error_code: str | None = None
    error_detail: str | None = None

