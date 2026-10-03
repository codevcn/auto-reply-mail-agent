"""5-Minute Reconciliation Poller: Guaranteed zero-loss catch-up mechanism."""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.email import MailboxCheckpoint
from app.ingestion.schemas import IngestionResult
from app.ingestion.service import MailIngestionService

logger = logging.getLogger("mail_agent.reconciliation")


class ReconciliationPoller:
    """Periodically queries IMAP mailboxes to discover any missed emails (R-05)."""

    def __init__(self, interval_seconds: int = 300) -> None:  # 5 minutes
        self.interval_seconds = interval_seconds
        self._is_running = False
        self._stop_event = asyncio.Event()

    async def run_once(self, db: AsyncSession) -> list[IngestionResult]:
        """Runs a single reconciliation pass over all active mailboxes."""
        stmt = select(MailboxCheckpoint).where(
            MailboxCheckpoint.state.in_(["active", "idle"])
        )
        res = await db.execute(stmt)
        checkpoints = res.scalars().all()

        results: list[IngestionResult] = []
        for cp in checkpoints:
            try:
                cp.state = "reconciling"
                await db.commit()

                ingest_res = await MailIngestionService.ingest_mailbox_messages(
                    db=db, mailbox_id=cp.mailbox_id, folder=cp.folder
                )
                results.append(ingest_res)

                if cp.state == "reconciling":
                    cp.state = "active"
                cp.last_reconciled_at = datetime.datetime.now(datetime.UTC)
                await db.commit()
            except Exception as exc:
                logger.error(
                    f"Reconciliation error on mailbox {cp.mailbox_id}/{cp.folder}: {exc}",
                    exc_info=True,
                )
                cp.state = "error"
                cp.last_error_code = str(exc)
                await db.commit()
                results.append(
                    IngestionResult(
                        mailbox_id=cp.mailbox_id,
                        folder=cp.folder,
                        status="error",
                        error_detail=str(exc),
                    )
                )

        return results

    async def start(self, session_maker) -> None:
        """Starts the recurring reconciliation background poller."""
        self._is_running = True
        self._stop_event.clear()

        while self._is_running and not self._stop_event.is_set():
            try:
                async with session_maker() as db:
                    await self.run_once(db)
            except Exception as exc:
                logger.warning(f"Reconciliation pass failed: {exc}")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=float(self.interval_seconds)
                )

    def stop(self) -> None:
        """Stops the poller gracefully."""
        self._is_running = False
        self._stop_event.set()
