"""0004_phase4_classification_and_attachments: Add Phase 4 classification and attachment tables.

Revision ID: 0004_phase4_classification_and_attachments
Revises: 0003_mail_ingestion
Create Date: 2026-10-03 07:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_phase4_classification_and_attachments"
down_revision: str | None = "0003_mail_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Alter table 'incoming_emails' (batch alter for SQLite & PostgreSQL compatibility)
    with op.batch_alter_table("incoming_emails") as batch_op:
        batch_op.add_column(sa.Column("detected_language", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("review_reason_code", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("spam_score", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("extracted_entities", sa.JSON(), nullable=True))

    # 2. Create table 'email_classifications'
    op.create_table(
        "email_classifications",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(20), nullable=False, server_default="ai"),
        sa.Column("provider_model", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column("spam_status", sa.String(30), nullable=False, server_default="not_spam"),
        sa.Column("spam_score", sa.Float(), nullable=True),
        sa.Column("customer_status", sa.String(50), nullable=False, server_default="no_order"),
        sa.Column("intent", sa.String(50), nullable=False),
        sa.Column("order_state_flags", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.JSON(), nullable=True),
        sa.Column("reason_codes", sa.JSON(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("detected_language", sa.String(10), nullable=True),
        sa.Column("requires_manual_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("review_reason_code", sa.String(100), nullable=True),
        sa.Column(
            "created_by",
            sa.CHAR(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_email_classifications_email_id",
        "email_classifications",
        ["incoming_email_id"],
    )

    # 3. Create table 'email_attachment_metadata' (Zero Raw Blob Invariant R-04)
    op.create_table(
        "email_attachment_metadata",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256_hash", sa.String(64), nullable=False),
        sa.Column("is_inline", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("content_id", sa.String(255), nullable=True),
        sa.Column("is_valid", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("validation_error", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_email_attachment_metadata_email_id",
        "email_attachment_metadata",
        ["incoming_email_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_attachment_metadata_email_id", table_name="email_attachment_metadata")
    op.drop_table("email_attachment_metadata")

    op.drop_index("ix_email_classifications_email_id", table_name="email_classifications")
    op.drop_table("email_classifications")

    with op.batch_alter_table("incoming_emails") as batch_op:
        batch_op.drop_column("extracted_entities")
        batch_op.drop_column("spam_score")
        batch_op.drop_column("review_reason_code")
        batch_op.drop_column("detected_language")
