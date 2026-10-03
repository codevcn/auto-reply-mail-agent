"""Mail Ingestion Pipeline: IDLE Listener, Reconciliation Poller, and On-Demand Content Fetching."""

from app.ingestion.listener import ImapIdleListener
from app.ingestion.reconciliation import ReconciliationPoller
from app.ingestion.schemas import EmailContentResponse, IngestionResult
from app.ingestion.service import MailFetchService, MailIngestionService

__all__ = [
    "EmailContentResponse",
    "ImapIdleListener",
    "IngestionResult",
    "MailFetchService",
    "MailIngestionService",
    "ReconciliationPoller",
]
