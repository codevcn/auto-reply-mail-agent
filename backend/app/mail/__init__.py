"""IMAP IDLE ingestion, reconciliation, MIME extraction & SMTP STARTTLS delivery."""

from app.mail.exceptions import (
    IMAPConnectionError,
    MailAuthenticationError,
    MailError,
    PiezaprintExclusionError,
    SMTPConnectionError,
    SourceMessageUnavailableError,
    UIDValidityChangedError,
)
from app.mail.imap_client import IMAPClient
from app.mail.router import mail_router
from app.mail.schemas import (
    EmailHeaderMetadata,
    IMAPTestResult,
    MailboxCandidateTestRequest,
    MailboxConnectionTestResponse,
    SMTPTestResult,
)
from app.mail.service import MailService
from app.mail.smtp_client import SMTPClient

__all__ = [
    "EmailHeaderMetadata",
    "IMAPClient",
    "IMAPConnectionError",
    "IMAPTestResult",
    "MailAuthenticationError",
    "MailError",
    "MailService",
    "MailboxCandidateTestRequest",
    "MailboxConnectionTestResponse",
    "PiezaprintExclusionError",
    "SMTPClient",
    "SMTPConnectionError",
    "SMTPTestResult",
    "SourceMessageUnavailableError",
    "UIDValidityChangedError",
    "mail_router",
]
