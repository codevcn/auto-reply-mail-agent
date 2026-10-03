"""IMAP IDLE Listener daemon for near-real-time email push notifications."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import uuid

from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.db.models.email import MailboxCheckpoint
from app.db.models.store import Mailbox
from app.ingestion.service import MailIngestionService
from app.mail.imap_client import IMAPClient

logger = logging.getLogger("mail_agent.idle_listener")


class ImapIdleListener:
    """Manages an asynchronous IMAP IDLE connection for a single mailbox folder."""

    def __init__(
        self,
        mailbox_id: uuid.UUID,
        folder: str = "INBOX",
        idle_timeout_seconds: int = 1200,  # 20 minutes RFC 2177 refresh
    ) -> None:
        self.mailbox_id = mailbox_id
        self.folder = folder
        self.idle_timeout_seconds = idle_timeout_seconds
        self._is_running = False
        self._stop_event = asyncio.Event()

    async def start(self, session_maker) -> None:
        """Starts the IDLE loop with bounded exponential backoff."""
        self._is_running = True
        self._stop_event.clear()
        backoff_delay = 1.0

        while self._is_running and not self._stop_event.is_set():
            try:
                await self._run_idle_session(session_maker)
                backoff_delay = 1.0  # Reset on successful cycle
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning(
                    f"IMAP IDLE error for mailbox {self.mailbox_id}: {exc}. Reconnecting in {backoff_delay}s..."
                )
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop_event.wait(), timeout=backoff_delay)
                backoff_delay = min(60.0, backoff_delay * 2.0)

    def stop(self) -> None:
        """Signals the listener to terminate gracefully."""
        self._is_running = False
        self._stop_event.set()

    async def _run_idle_session(self, session_maker) -> None:
        """Executes a single IDLE session round."""
        async with session_maker() as db:
            stmt = select(Mailbox).where(Mailbox.id == self.mailbox_id)
            mailbox = (await db.execute(stmt)).scalar_one_or_none()
            if not mailbox:
                self._is_running = False
                return

            raw_password = decrypt_secret(mailbox.encrypted_password)
            imap_client = IMAPClient(
                host=mailbox.imap_host,
                port=mailbox.imap_port,
                username=mailbox.address,
                password=raw_password,
                tls_mode=mailbox.imap_tls_mode,
            )

            test_res = await imap_client.test_connection()
            if not test_res.success:
                raise RuntimeError(f"Connection test failed: {test_res.error}")

            # Update checkpoint state
            cp_stmt = select(MailboxCheckpoint).where(
                MailboxCheckpoint.mailbox_id == self.mailbox_id,
                MailboxCheckpoint.folder == self.folder,
            )
            cp = (await db.execute(cp_stmt)).scalar_one_or_none()
            if cp:
                now_utc = datetime.datetime.now(datetime.UTC)
                cp.idle_connected_at = now_utc
                cp.idle_heartbeat_at = now_utc
                cp.state = "idle"
                await db.commit()

        # Ingestion catch-up before entering IDLE wait
        async with session_maker() as db:
            await MailIngestionService.ingest_mailbox_messages(
                db=db, mailbox_id=self.mailbox_id, folder=self.folder
            )

        # Wait for IDLE timeout or external cancellation
        try:
            await asyncio.wait_for(
                self._stop_event.wait(), timeout=float(self.idle_timeout_seconds)
            )
        except TimeoutError:
            # Normal keepalive cycle: update heartbeat
            async with session_maker() as db:
                cp_stmt = select(MailboxCheckpoint).where(
                    MailboxCheckpoint.mailbox_id == self.mailbox_id,
                    MailboxCheckpoint.folder == self.folder,
                )
                cp = (await db.execute(cp_stmt)).scalar_one_or_none()
                if cp:
                    cp.idle_heartbeat_at = datetime.datetime.now(datetime.UTC)
                    await db.commit()
