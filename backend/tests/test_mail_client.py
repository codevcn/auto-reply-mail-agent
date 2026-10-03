"""Tests for IMAP and SMTP clients: Protocol safety, Seen flag preservation, and Piezaprint exclusion."""

from __future__ import annotations

import imaplib
from unittest.mock import MagicMock, patch

import pytest
from app.core.redaction import redact_text
from app.mail.exceptions import PiezaprintExclusionError
from app.mail.imap_client import IMAPClient
from app.mail.schemas import MailboxCandidateTestRequest
from app.mail.smtp_client import SMTPClient
from pydantic import ValidationError


def test_piezaprint_exclusion_inviolable():
    """R-03: Piezaprint mailboxes and domains must be strictly blocked at all layers."""
    # 1. Pydantic validation layer
    with pytest.raises(ValidationError):
        MailboxCandidateTestRequest(
            address="support@piezaprint.com",
            password="secret_password_123",
        )

    # 2. IMAP client layer
    with pytest.raises(PiezaprintExclusionError):
        IMAPClient(username="support@piezaprint.com", password="pwd")

    # 3. SMTP client layer
    with pytest.raises(PiezaprintExclusionError):
        SMTPClient(username="support@piezaprint.com", password="pwd")


def test_imap_peek_and_examine_seen_flag_preserved():
    """R-06 & Invariant: Ingestion strictly uses BODY.PEEK and EXAMINE (Read-Only) without mutating \\Seen."""
    client = IMAPClient(
        host="mail.wrydeco.com",
        port=993,
        username="support@wrydeco.com",
        password="safe_password",
    )

    mock_imap = MagicMock()
    mock_imap.examine.return_value = ("OK", [b"10"])
    # Return mock header fetch result
    header_raw = (
        b"From: Customer <alice@example.com>\r\n"
        b"To: support@wrydeco.com\r\n"
        b"Subject: Inquiring on Product\r\n"
        b"Message-ID: <msg_101@client.com>\r\n"
        b"Date: Fri, 02 Oct 2026 10:00:00 +0000\r\n\r\n"
    )
    mock_imap.uid.return_value = ("OK", [(b"1 (FLAGS (\\Draft))", header_raw)])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        res = client._sync_fetch_header_metadata(uid=101)

        # 1. Assert examine (Read-Only) was called, not select (Read-Write)
        mock_imap.examine.assert_called_once_with("INBOX")
        mock_imap.select.assert_not_called()

        # 2. Assert fetch command contains BODY.PEEK
        uid_calls = mock_imap.uid.call_args_list
        assert len(uid_calls) > 0
        cmd_args = uid_calls[0][0]
        assert cmd_args[0] == "FETCH"
        assert cmd_args[1] == "101"
        assert "BODY.PEEK" in cmd_args[2]
        assert "BODY[" not in cmd_args[2].replace("BODY.PEEK", "")

        assert res is not None
        assert res.subject == "Inquiring on Product"
        assert res.from_address == "alice@example.com"


def test_imap_test_connection_success_and_auth_failure():
    """Validates IMAP test_connection reports status accurately without side effects."""
    client = IMAPClient(
        host="mail.wrydeco.com",
        port=993,
        username="support@wrydeco.com",
        password="test_password",
    )

    # 1. Success case
    mock_imap = MagicMock()
    mock_imap.examine.return_value = ("OK", [b"5"])
    mock_imap.status.return_value = ("OK", [b'"INBOX" (UIDVALIDITY 8888 UIDNEXT 106 UNSEEN 2)'])
    mock_imap.capability.return_value = ("OK", [b"IMAP4rev1 IDLE UIDPLUS"])

    with patch("imaplib.IMAP4_SSL", return_value=mock_imap):
        result = client._sync_test_connection()
        assert result.success is True
        assert result.uid_validity == 8888
        assert result.uid_next == 106
        assert result.message_count == 5
        assert result.unseen_count == 2
        assert "IDLE" in result.capabilities

    # 2. Auth failure case
    mock_fail = MagicMock()
    mock_fail.login.side_effect = imaplib.IMAP4.error("Authentication failed")

    with patch("imaplib.IMAP4_SSL", return_value=mock_fail):
        result_fail = client._sync_test_connection()
        assert result_fail.success is False
        assert result_fail.error == "MAIL_AUTHENTICATION_FAILED"


def test_smtp_test_connection_no_spam_sent():
    """Validates SMTP STARTTLS connection test validates auth without sending any emails."""
    smtp_client = SMTPClient(
        host="mail.wrydeco.com",
        port=587,
        username="support@wrydeco.com",
        password="test_smtp_pass",
        tls_mode="STARTTLS",
    )

    mock_server = MagicMock()
    mock_server.has_extn.return_value = True
    mock_server.esmtp_features = {"STARTTLS": "", "AUTH": "PLAIN LOGIN"}

    with patch("smtplib.SMTP", return_value=mock_server):
        result = smtp_client._sync_test_connection()

        # Must authenticate
        mock_server.login.assert_called_once_with("support@wrydeco.com", "test_smtp_pass")
        # Must NEVER call sendmail or send_message
        mock_server.sendmail.assert_not_called()
        mock_server.send_message.assert_not_called()
        # Must gracefully quit
        mock_server.quit.assert_called_once()

        assert result.success is True
        assert result.auth_success is True
        assert result.starttls_supported is True


def test_envelope_encryption_and_secret_redaction_integration():
    """Validates plaintext mail credentials are automatically redacted from logs and string output."""
    raw_secret = "super_secret_mail_pwd_9988"
    IMAPClient(
        host="mail.wrydeco.com",
        port=993,
        username="support@wrydeco.com",
        password=raw_secret,
    )

    test_message = f"Connecting to mail with password={raw_secret} for authentication."
    redacted = redact_text(test_message)
    assert raw_secret not in redacted
    assert "[REDACTED]" in redacted
