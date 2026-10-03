"""0006_phase6_reply_drafts_and_delivery: Add Phase 6 reply_drafts, reply_draft_versions, and reply_delivery_attempts.

Revision ID: 0006_phase6_reply_drafts_and_delivery
Revises: 0005_phase5_shopify_enrichment
Create Date: 2026-10-03 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_phase6_reply_drafts_and_delivery"
down_revision: str | None = "0005_phase5_shopify_enrichment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create table 'reply_drafts'
    op.create_table(
        "reply_drafts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("current_version_id", sa.CHAR(36), nullable=True),
        sa.Column("current_version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending_approval"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_reply_drafts_email_id", "reply_drafts", ["incoming_email_id"])
    op.create_index("ix_reply_drafts_store_id", "reply_drafts", ["store_profile_id"])
    op.create_index("ix_reply_drafts_status", "reply_drafts", ["status"])

    # 2. Create table 'reply_draft_versions' (Invariant R-24)
    op.create_table(
        "reply_draft_versions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.CHAR(36),
            sa.ForeignKey("reply_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=False),
        sa.Column("language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("source", sa.String(20), nullable=False, server_default="ai"),
        sa.Column("ai_provider", sa.String(50), nullable=True),
        sa.Column("ai_model", sa.String(100), nullable=True),
        sa.Column("prompt_version", sa.String(50), nullable=True),
        sa.Column(
            "order_snapshot_id",
            sa.CHAR(36),
            sa.ForeignKey("shopify_order_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "product_snapshot_id",
            sa.CHAR(36),
            sa.ForeignKey("shopify_product_snapshots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("policy_hashes_used", sa.JSON(), nullable=True),
        sa.Column("warning_codes", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_current_version", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by",
            sa.CHAR(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reply_draft_versions_draft_id", "reply_draft_versions", ["draft_id"])
    op.create_index("ix_reply_draft_versions_email_id", "reply_draft_versions", ["incoming_email_id"])
    op.create_index(
        "ix_reply_draft_versions_draft_current",
        "reply_draft_versions",
        ["draft_id", "is_current_version"],
    )
    op.create_unique_constraint(
        "uq_reply_draft_versions_draft_version",
        "reply_draft_versions",
        ["draft_id", "version_number"],
    )

    # 3. Add foreign key from reply_drafts.current_version_id to reply_draft_versions.id
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind else "sqlite"
    if dialect_name != "sqlite":
        op.create_foreign_key(
            "fk_reply_drafts_current_version_id",
            "reply_drafts",
            "reply_draft_versions",
            ["current_version_id"],
            ["id"],
            ondelete="SET NULL",
            use_alter=True,
        )

    # 4. Create table 'reply_delivery_attempts' (Invariant R-01, R-25, R-26)
    op.create_table(
        "reply_delivery_attempts",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "draft_id",
            sa.CHAR(36),
            sa.ForeignKey("reply_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "draft_version_id",
            sa.CHAR(36),
            sa.ForeignKey("reply_draft_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(100), unique=True, nullable=False),
        sa.Column(
            "approved_by",
            sa.CHAR(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("outgoing_message_id", sa.String(255), unique=True, nullable=True),
        sa.Column("smtp_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("smtp_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("smtp_response_summary", sa.Text(), nullable=True),
        sa.Column("sent_folder_append_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_reply_delivery_attempts_email_id", "reply_delivery_attempts", ["incoming_email_id"])
    op.create_index("ix_reply_delivery_attempts_draft_id", "reply_delivery_attempts", ["draft_id"])
    op.create_index("ix_reply_delivery_attempts_status", "reply_delivery_attempts", ["status"])
    op.create_index("ix_reply_delivery_attempts_idempotency", "reply_delivery_attempts", ["idempotency_key"])


def downgrade() -> None:
    bind = op.get_bind()
    dialect_name = bind.dialect.name if bind else "sqlite"

    op.drop_table("reply_delivery_attempts")
    if dialect_name != "sqlite":
        op.drop_constraint("fk_reply_drafts_current_version_id", "reply_drafts", type_="foreignkey")
    op.drop_table("reply_draft_versions")
    op.drop_table("reply_drafts")
