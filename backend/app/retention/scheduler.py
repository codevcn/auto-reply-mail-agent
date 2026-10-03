"""Periodic background scheduler for 120-Day Retention Cleanup (Invariant R-34)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.db.session import AsyncSessionLocal
from app.retention.service import RetentionService

logger = logging.getLogger("mail_agent.retention.scheduler")


class RetentionScheduler:
    """Periodic background runner for Retention Cleanup Job."""

    def __init__(
        self,
        interval_hours: int = 24,
        retention_days: int = 120,
        batch_size: int = 500,
    ) -> None:
        self.interval_seconds = interval_hours * 3600
        self.retention_days = min(retention_days, 120)
        self.batch_size = batch_size
        self._is_running = False
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        """Starts recurring 24-hour cleanup cycle."""
        self._is_running = True
        self._stop_event.clear()
        logger.info(
            "RetentionScheduler started (interval=%ds, retention_days=%d)",
            self.interval_seconds,
            self.retention_days,
        )

        while self._is_running and not self._stop_event.is_set():
            try:
                with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=float(self.interval_seconds)
                    )

                if self._stop_event.is_set() or not self._is_running:
                    break

                logger.info("Executing scheduled retention cleanup...")
                async with AsyncSessionLocal() as session:
                    await RetentionService.run_cleanup_job(
                        db=session,
                        retention_days=self.retention_days,
                        dry_run=False,
                        batch_size=self.batch_size,
                        actor_user_id=None,
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error during scheduled retention cleanup: %s", e, exc_info=True)
                # Brief sleep before retry to prevent tight error loop
                await asyncio.sleep(60)

    def stop(self) -> None:
        """Signals scheduler loop to terminate."""
        self._is_running = False
        self._stop_event.set()
        logger.info("RetentionScheduler stopping...")
