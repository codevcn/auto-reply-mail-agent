"""Mail Agent Mailbox & Protocol Exceptions."""

from __future__ import annotations


class MailError(Exception):
    """Base exception for all mail operations."""

    def __init__(self, message: str, code: str = "MAIL_ERROR", detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.detail = detail or message


class PiezaprintExclusionError(MailError):
    """Raised when an attempt is made to use or test a piezaprint.com mailbox (R-03)."""

    def __init__(
        self,
        message: str = "Piezaprint mailboxes are strictly excluded from the system (R-03).",
    ):
        super().__init__(message, code="STORE_EXCLUDED_FROM_SYSTEM")


class MailAuthenticationError(MailError):
    """Raised when IMAP or SMTP authentication fails."""

    def __init__(
        self, message: str = "Mail credentials authentication failed.", detail: str | None = None
    ):
        super().__init__(message, code="MAIL_AUTHENTICATION_FAILED", detail=detail)


class IMAPConnectionError(MailError):
    """Raised when IMAP connection cannot be established."""

    def __init__(
        self, message: str = "Failed to connect to IMAP server.", detail: str | None = None
    ):
        super().__init__(message, code="IMAP_CONNECTION_FAILED", detail=detail)


class SMTPConnectionError(MailError):
    """Raised when SMTP connection cannot be established."""

    def __init__(
        self, message: str = "Failed to connect to SMTP server.", detail: str | None = None
    ):
        super().__init__(message, code="SMTP_CONNECTION_FAILED", detail=detail)


class UIDValidityChangedError(MailError):
    """Raised when the server's UIDVALIDITY differs from the checkpoint (R-05)."""

    def __init__(self, expected: int, found: int):
        msg = f"IMAP UIDVALIDITY changed on server (expected {expected}, found {found}). Ingestion paused."
        super().__init__(msg, code="IMAP_UIDVALIDITY_CHANGED")
        self.expected = expected
        self.found = found


class SourceMessageUnavailableError(MailError):
    """Raised when the target email is no longer found on the IMAP server (e.g. deleted)."""

    def __init__(self, uid: int, message: str = "Source email is unavailable on mailserver."):
        super().__init__(message, code="SOURCE_MESSAGE_UNAVAILABLE")
        self.uid = uid
