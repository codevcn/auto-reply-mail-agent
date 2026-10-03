"""0003_mail_ingestion: Create MailboxCheckpoint, IncomingEmail, and EmailJob tables.

Revision ID: 0003_mail_ingestion
Revises: 0002_store_and_secrets
Create Date: 2026-10-03 06:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_mail_ingestion"
down_revision: str | None = "0002_store_and_secrets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create table 'mailbox_checkpoints'
    op.create_table(
        "mailbox_checkpoints",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "mailbox_id",
            sa.CHAR(36),
            sa.ForeignKey("mailboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("folder", sa.String(50), nullable=False, server_default="INBOX"),
        sa.Column("uid_validity", sa.BigInteger(), nullable=False),
        sa.Column("activation_baseline_uid", sa.BigInteger(), nullable=False),
        sa.Column("last_durably_enqueued_uid", sa.BigInteger(), nullable=False),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idle_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(30), nullable=False, server_default="active"),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint("mailbox_id", "folder", name="uq_mailbox_checkpoints_mailbox_folder"),
    )
    op.create_index("ix_mailbox_checkpoints_mailbox_id", "mailbox_checkpoints", ["mailbox_id"])

    # 2. Create table 'incoming_emails' (Metadata ONLY - Zero Raw Body R-04)
    op.create_table(
        "incoming_emails",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mailbox_id",
            sa.CHAR(36),
            sa.ForeignKey("mailboxes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("folder", sa.String(50), nullable=False, server_default="INBOX"),
        sa.Column("imap_uid", sa.BigInteger(), nullable=False),
        sa.Column("uidvalidity", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=True),
        sa.Column("sender_email", sa.String(255), nullable=False),
        sa.Column("sender_name", sa.String(255), nullable=True),
        sa.Column("recipient_email", sa.String(255), nullable=False),
        sa.Column("reply_to_email", sa.String(255), nullable=True),
        sa.Column("to_addresses", sa.JSON(), nullable=True),
        sa.Column("cc_addresses", sa.JSON(), nullable=True),
        sa.Column("subject", sa.String(500), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("classification_category", sa.String(50), nullable=True),
        sa.Column("customer_status", sa.String(50), nullable=True, server_default="no_order"),
        sa.Column("spam_status", sa.String(30), nullable=True, server_default="not_spam"),
        sa.Column("intent_confidence", sa.Float(), nullable=True),
        sa.Column("current_draft_version", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("manual_review_reason", sa.String(255), nullable=True),
        sa.Column(
            "first_detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_transition_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.UniqueConstraint(
            "mailbox_id",
            "folder",
            "uidvalidity",
            "imap_uid",
            name="uq_incoming_emails_uid",
        ),
    )
    op.create_index("ix_incoming_emails_store_profile_id", "incoming_emails", ["store_profile_id"])
    op.create_index("ix_incoming_emails_mailbox_id", "incoming_emails", ["mailbox_id"])
    op.create_index("ix_incoming_emails_sender_email", "incoming_emails", ["sender_email"])
    op.create_index("ix_incoming_emails_message_id", "incoming_emails", ["message_id"])
    op.create_index("ix_incoming_emails_status", "incoming_emails", ["status"])
    op.create_index("ix_incoming_emails_received_at", "incoming_emails", ["received_at"])
    op.create_index(
        "ix_incoming_emails_store_status_received",
        "incoming_emails",
        ["store_profile_id", "status", "received_at"],
    )

    # 3. Create table 'email_jobs' (Transactional Queue)
    op.create_table(
        "email_jobs",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "incoming_email_id",
            "job_type",
            name="uq_email_jobs_email_jobtype",
        ),
    )
    op.create_index("ix_email_jobs_incoming_email_id", "email_jobs", ["incoming_email_id"])
    op.create_index("ix_email_jobs_store_profile_id", "email_jobs", ["store_profile_id"])
    op.create_index("ix_email_jobs_status", "email_jobs", ["status"])
    op.create_index("ix_email_jobs_scheduled_at", "email_jobs", ["scheduled_at"])
    op.create_index("ix_email_jobs_status_scheduled", "email_jobs", ["status", "scheduled_at"])


def downgrade() -> None:
    op.drop_table("email_jobs")
    op.drop_table("incoming_emails")
    op.drop_table("mailbox_checkpoints")
