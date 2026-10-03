"""Production Worker Process Entrypoint (Phase 7).

Coordinates:
- Transactional queue job consumer (FOR UPDATE SKIP LOCKED)
- Periodic 24-hour retention auto-cleanup (Invariant R-34)
- Graceful shutdown handling
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
import sys

from app.config import get_settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.ingestion.reconciliation import ReconciliationPoller
from app.queue.service import TransactionalQueueService
from app.retention.scheduler import RetentionScheduler

logger = logging.getLogger("mail_agent.worker")


class MailAgentWorker:
    """Production background worker for queue processing and retention."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.worker_id = f"worker-{socket.gethostname()}-{os.getpid()}"
        self.stop_event = asyncio.Event()
        self.retention_scheduler = RetentionScheduler(
            interval_hours=self.settings.RETENTION_SCHEDULER_INTERVAL_HOURS,
            retention_days=self.settings.RETENTION_DAYS,
            batch_size=self.settings.RETENTION_BATCH_SIZE,
        )
        self.reconciliation_poller = ReconciliationPoller(interval_seconds=30)

    async def run_queue_loop(self) -> None:
        """Polls and processes queue jobs with exponential backoff on idle."""
        logger.info("Starting queue processing loop (worker_id=%s)", self.worker_id)
        idle_sleep = 1.0

        while not self.stop_event.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    job = await TransactionalQueueService.acquire_next_job(
                        session=session,
                        worker_id=self.worker_id,
                    )
                    if job:
                        logger.info(
                            "Acquired job %s (type=%s, attempt=%d/%d)",
                            job.id,
                            job.job_type,
                            job.attempts,
                            job.max_attempts,
                        )
                        success = await TransactionalQueueService.process_job(
                            session=session,
                            job_id=job.id,
                            worker_id=self.worker_id,
                        )
                        logger.info("Job %s execution result: %s", job.id, "success" if success else "failed")
                        idle_sleep = 0.1  # Fast loop when jobs are active
                    else:
                        idle_sleep = min(idle_sleep * 1.5, 3.0)

                await asyncio.sleep(idle_sleep)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in worker queue loop: %s", e, exc_info=True)
                await asyncio.sleep(2.0)

    async def start(self) -> None:
        """Starts worker tasks and awaits termination signal."""
        logger.info("Initializing Mail Agent Worker [%s]...", self.worker_id)
        setup_logging(
            level=self.settings.LOG_LEVEL,
            json_format=self.settings.JSON_LOGS,
            extra_secrets=[self.settings.SECRET_KEY],
        )

        # Run queue consumer, retention scheduler, and reconciliation poller concurrently
        scheduler_task = asyncio.create_task(self.retention_scheduler.start())
        queue_task = asyncio.create_task(self.run_queue_loop())
        poller_task = asyncio.create_task(self.reconciliation_poller.start(AsyncSessionLocal))

        try:
            await self.stop_event.wait()
        finally:
            logger.info("Stopping worker gracefully...")
            self.retention_scheduler.stop()
            self.reconciliation_poller.stop()
            queue_task.cancel()
            scheduler_task.cancel()
            poller_task.cancel()
            await asyncio.gather(queue_task, scheduler_task, poller_task, return_exceptions=True)
            logger.info("Worker shutdown complete.")

    def stop(self) -> None:
        """Triggers graceful shutdown."""
        self.stop_event.set()


async def main() -> None:
    """Worker CLI entrypoint."""
    worker = MailAgentWorker()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, worker.stop)

    try:
        await worker.start()
    except (KeyboardInterrupt, asyncio.CancelledError):
        worker.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
