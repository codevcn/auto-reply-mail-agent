"""Async IMAP Client with SSL, BODY.PEEK non-destructive metadata fetch, and UID baseline discovery."""

from __future__ import annotations

import asyncio
import contextlib
import email
import email.utils
import imaplib
import re
import ssl
import time
from email.header import decode_header
from email.policy import default

from app.core.redaction import register_secret
from app.mail.exceptions import (
    PiezaprintExclusionError,
)
from app.mail.schemas import EmailHeaderMetadata, IMAPTestResult

EXCLUDED_DOMAINS = ["piezaprint.com", "piezaprint"]


def _clean_header_value(val: str | None) -> str:
    if not val:
        return ""
    decoded_fragments = decode_header(val)
    parts = []
    for frag, enc in decoded_fragments:
        if isinstance(frag, bytes):
            parts.append(frag.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(frag))
    return " ".join(parts).strip()


class IMAPClient:
    """Production IMAP Client ensuring zero Seen-flag mutation and safe envelope password handling."""

    def __init__(
        self,
        host: str = "mail.wrydeco.com",
        port: int = 993,
        username: str = "",
        password: str = "",
        tls_mode: str = "SSL",
        timeout_seconds: float = 15.0,
    ) -> None:
        self.host = host.strip()
        self.port = port
        self.username = username.strip().lower()
        self.password = password
        self.tls_mode = tls_mode.upper()
        self.timeout_seconds = timeout_seconds

        # Enforce R-03 immediately upon instantiation
        if any(exc in self.username or exc in self.host.lower() for exc in EXCLUDED_DOMAINS):
            raise PiezaprintExclusionError()

        # Register plaintext password into Redaction Engine
        if self.password:
            register_secret(self.password)

    def _create_ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        return ctx

    @staticmethod
    def _examine_folder(client: imaplib.IMAP4_SSL | imaplib.IMAP4, folder: str = "INBOX"):
        """Issues IMAP EXAMINE in read-only mode adhering to Invariant R-06."""
        if hasattr(client, "examine") and callable(getattr(client, "examine")):
            return client.examine(folder)
        return client.select(folder, readonly=True)

    def _sync_test_connection(self) -> IMAPTestResult:
        start_time = time.perf_counter()
        client: imaplib.IMAP4 | None = None
        try:
            ssl_ctx = self._create_ssl_context()
            if self.tls_mode == "SSL" or self.port == 993:
                client = imaplib.IMAP4_SSL(
                    host=self.host,
                    port=self.port,
                    ssl_context=ssl_ctx,
                    timeout=self.timeout_seconds,
                )
            else:
                client = imaplib.IMAP4(host=self.host, port=self.port, timeout=self.timeout_seconds)
                if self.tls_mode == "STARTTLS":
                    client.starttls(ssl_context=ssl_ctx)

            # Authenticate
            try:
                client.login(self.username, self.password)
            except imaplib.IMAP4.error as auth_exc:
                latency = int((time.perf_counter() - start_time) * 1000)
                return IMAPTestResult(
                    success=False,
                    latency_ms=latency,
                    error="MAIL_AUTHENTICATION_FAILED",
                    detail=f"IMAP login failed: {auth_exc}",
                )

            # Read-only inspect INBOX (EXAMINE command strictly avoids any flag modification - R-06)
            res_code, select_data = self._examine_folder(client, "INBOX")
            if res_code != "OK":
                latency = int((time.perf_counter() - start_time) * 1000)
                return IMAPTestResult(
                    success=False,
                    latency_ms=latency,
                    error="MAILBOX_NOT_FOUND",
                    detail="Could not open INBOX in read-only mode.",
                )

            msg_count = int(select_data[0].decode("utf-8")) if select_data and select_data[0] else 0

            # Query STATUS for UIDVALIDITY, UIDNEXT, UNSEEN
            status_code, status_data = client.status("INBOX", "(UIDVALIDITY UIDNEXT UNSEEN)")
            uid_validity = None
            uid_next = None
            unseen_count = None
            if status_code == "OK" and status_data:
                raw_status = status_data[0].decode("utf-8")
                m_validity = re.search(r"UIDVALIDITY\s+(\d+)", raw_status)
                if m_validity:
                    uid_validity = int(m_validity.group(1))
                m_next = re.search(r"UIDNEXT\s+(\d+)", raw_status)
                if m_next:
                    uid_next = int(m_next.group(1))
                m_unseen = re.search(r"UNSEEN\s+(\d+)", raw_status)
                if m_unseen:
                    unseen_count = int(m_unseen.group(1))

            # Query CAPABILITY
            cap_code, cap_data = client.capability()
            capabilities = []
            if cap_code == "OK" and cap_data:
                capabilities = cap_data[0].decode("utf-8").split()

            latency = int((time.perf_counter() - start_time) * 1000)
            return IMAPTestResult(
                success=True,
                uid_validity=uid_validity,
                uid_next=uid_next,
                message_count=msg_count,
                unseen_count=unseen_count,
                capabilities=capabilities,
                latency_ms=latency,
            )

        except TimeoutError:
            latency = int((time.perf_counter() - start_time) * 1000)
            return IMAPTestResult(
                success=False,
                latency_ms=latency,
                error="IMAP_TIMEOUT",
                detail=f"IMAP connection timed out after {self.timeout_seconds}s.",
            )
        except ssl.SSLError as exc:
            latency = int((time.perf_counter() - start_time) * 1000)
            return IMAPTestResult(
                success=False,
                latency_ms=latency,
                error="IMAP_SSL_ERROR",
                detail=f"TLS/SSL negotiation failed: {exc}",
            )
        except Exception as exc:
            latency = int((time.perf_counter() - start_time) * 1000)
            return IMAPTestResult(
                success=False,
                latency_ms=latency,
                error="IMAP_CONNECTION_FAILED",
                detail=str(exc),
            )
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    client.logout()

    async def test_connection(self) -> IMAPTestResult:
        """Asynchronously test IMAP credentials and retrieve mailbox status."""
        return await asyncio.to_thread(self._sync_test_connection)

    def _sync_get_mailbox_baseline(self, folder: str = "INBOX") -> tuple[int, int]:
        """Query server for current UIDVALIDITY and calculates baseline UID (max(0, UIDNEXT - 1))."""
        ssl_ctx = self._create_ssl_context()
        client = imaplib.IMAP4_SSL(
            host=self.host, port=self.port, ssl_context=ssl_ctx, timeout=self.timeout_seconds
        )
        try:
            client.login(self.username, self.password)
            status_code, status_data = client.status(folder, "(UIDVALIDITY UIDNEXT)")
            if status_code != "OK" or not status_data:
                raise RuntimeError(f"Failed to query IMAP STATUS for {folder}")

            raw_status = status_data[0].decode("utf-8")
            m_validity = re.search(r"UIDVALIDITY\s+(\d+)", raw_status)
            m_next = re.search(r"UIDNEXT\s+(\d+)", raw_status)
            if not m_validity or not m_next:
                raise RuntimeError(f"Invalid IMAP STATUS response: {raw_status}")

            uid_validity = int(m_validity.group(1))
            uid_next = int(m_next.group(1))
            baseline_uid = max(0, uid_next - 1)
            return uid_validity, baseline_uid
        finally:
            with contextlib.suppress(Exception):
                client.logout()

    async def get_mailbox_baseline(self, folder: str = "INBOX") -> tuple[int, int]:
        return await asyncio.to_thread(self._sync_get_mailbox_baseline, folder)

    def _sync_search_new_uids(self, min_uid: int, folder: str = "INBOX") -> list[int]:
        """Search UIDs strictly greater than min_uid in read-only EXAMINE mode."""
        ssl_ctx = self._create_ssl_context()
        client = imaplib.IMAP4_SSL(
            host=self.host, port=self.port, ssl_context=ssl_ctx, timeout=self.timeout_seconds
        )
        try:
            client.login(self.username, self.password)
            self._examine_folder(client, folder)  # Read-only (issues EXAMINE)
            search_query = f"UID {min_uid + 1}:*"
            res, data = client.uid("SEARCH", search_query)
            if res != "OK" or not data or not data[0]:
                return []
            raw_uids = data[0].decode("utf-8").split()
            uids = [int(u) for u in raw_uids if u.isdigit() and int(u) > min_uid]
            return sorted(uids)
        finally:
            with contextlib.suppress(Exception):
                client.logout()

    async def search_new_uids(self, min_uid: int, folder: str = "INBOX") -> list[int]:
        return await asyncio.to_thread(self._sync_search_new_uids, min_uid, folder)

    def _sync_fetch_header_metadata(self, uid: int, folder: str = "INBOX") -> EmailHeaderMetadata | None:
        """Fetch email headers using BODY.PEEK to strictly prevent Seen flag alteration (R-06)."""
        client: imaplib.IMAP4_SSL | None = None
        try:
            ssl_ctx = self._create_ssl_context()
            client = imaplib.IMAP4_SSL(
                host=self.host, port=self.port, ssl_context=ssl_ctx, timeout=self.timeout_seconds
            )
            client.login(self.username, self.password)
            self._examine_folder(client, folder)  # Read-only mode (issues EXAMINE)

            # CRITICAL INVARIANT R-06: MUST USE BODY.PEEK
            fetch_cmd = (
                "(UID FLAGS INTERNALDATE BODY.PEEK[HEADER.FIELDS "
                "(DATE FROM TO CC BCC SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES "
                "AUTO-SUBMITTED PRECEDENCE LIST-ID CONTENT-TYPE)])"
            )
            res, data = client.uid("FETCH", str(uid), fetch_cmd)
            if res != "OK" or not data or not data[0]:
                return None

            raw_header_bytes = b""
            flags: list[str] = []
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2:
                    raw_header_bytes = item[1]
                    # Parse flags from item[0]
                    first_part = item[0].decode("utf-8", errors="replace")
                    m_flags = re.search(r"FLAGS\s+\((.*?)\)", first_part)
                    if m_flags:
                        flags = m_flags.group(1).split()
                    break

            msg = email.message_from_bytes(raw_header_bytes, policy=default)

            auto_sub = msg.get("Auto-Submitted", "").lower()
            is_auto = bool(auto_sub and auto_sub != "no")

            parsed_date = None
            date_header = msg.get("Date")
            if date_header:
                with contextlib.suppress(Exception):
                    parsed_date = email.utils.parsedate_to_datetime(date_header)

            from_raw = str(msg.get("From", "")).strip()
            realname, addr = email.utils.parseaddr(from_raw)
            sender_name = _clean_header_value(realname) if realname else None
            sender_email = addr or from_raw

            return EmailHeaderMetadata(
                uid=uid,
                message_id=msg.get("Message-ID"),
                subject=_clean_header_value(msg.get("Subject")),
                from_address=sender_email,
                sender_name=sender_name,
                reply_to=msg.get("Reply-To"),
                to_addresses=[str(t).strip() for t in msg.get_all("To", [])],
                cc_addresses=[str(c).strip() for c in msg.get_all("Cc", [])],
                date=parsed_date,
                is_auto_submitted=is_auto,
                precedence=msg.get("Precedence"),
                list_id=msg.get("List-Id"),
                content_type=msg.get_content_type() or "text/plain",
                flags=flags,
            )
        finally:
            if client:
                with contextlib.suppress(Exception):
                    client.logout()

    async def fetch_header_metadata(self, uid: int, folder: str = "INBOX") -> EmailHeaderMetadata | None:
        return await asyncio.to_thread(self._sync_fetch_header_metadata, uid, folder)

    def _sync_fetch_raw_rfc822_peek(self, uid: int, folder: str = "INBOX") -> bytes | None:
        """Fetch full raw RFC822 email bytes via BODY.PEEK[] without setting Seen flag."""
        client: imaplib.IMAP4_SSL | None = None
        try:
            ssl_ctx = self._create_ssl_context()
            client = imaplib.IMAP4_SSL(
                host=self.host, port=self.port, ssl_context=ssl_ctx, timeout=self.timeout_seconds
            )
            client.login(self.username, self.password)
            self._examine_folder(client, folder)

            res, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
            if res != "OK" or not data:
                return None
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2:
                    return item[1]
            return None
        finally:
            if client:
                with contextlib.suppress(Exception):
                    client.logout()

    async def fetch_raw_email_peek(self, uid: int, folder: str = "INBOX") -> bytes | None:
        return await asyncio.to_thread(self._sync_fetch_raw_rfc822_peek, uid, folder)

    def _sync_append_to_sent(self, raw_message: bytes, folder: str = "Sent") -> bool:
        """Append an outbound email copy into the IMAP Sent mailbox with fallback folder discovery."""
        client: imaplib.IMAP4_SSL | None = None
        try:
            ssl_ctx = self._create_ssl_context()
            client = imaplib.IMAP4_SSL(
                host=self.host, port=self.port, ssl_context=ssl_ctx, timeout=self.timeout_seconds
            )
            client.login(self.username, self.password)
            candidate_folders = [folder, "Sent", "INBOX.Sent", "Sent Messages", "Sent Items"]
            for cand in dict.fromkeys(candidate_folders):
                with contextlib.suppress(Exception):
                    res, _ = client.append(
                        cand, "\\Seen", imaplib.Time2Internaldate(time.time()), raw_message
                    )
                    if res == "OK":
                        return True
            return False
        except Exception:
            return False
        finally:
            if client:
                with contextlib.suppress(Exception):
                    client.logout()

    async def append_to_sent_folder(self, raw_message: bytes, folder: str = "Sent") -> bool:
        return await asyncio.to_thread(self._sync_append_to_sent, raw_message, folder)
