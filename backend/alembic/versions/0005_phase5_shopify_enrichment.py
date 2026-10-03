"""0005_phase5_shopify_enrichment: Add Phase 5 store_policies, order & product snapshots, and stale draft fields.

Revision ID: 0005_phase5_shopify_enrichment
Revises: 0004_phase4_classification_and_attachments
Create Date: 2026-10-03 08:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_phase5_shopify_enrichment"
down_revision: str | None = "0004_phase4_classification_and_attachments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create table 'store_policies' (Invariant R-17, R-18)
    op.create_table(
        "store_policies",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("url", sa.String(500), nullable=True),
        sa.Column("is_custom", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_store_policies_store_id", "store_policies", ["store_profile_id"])
    op.create_index("ix_store_policies_policy_type", "store_policies", ["policy_type"])
    op.create_unique_constraint(
        "uq_store_policies_store_policy_type",
        "store_policies",
        ["store_profile_id", "policy_type"],
    )

    # 2. Create table 'shopify_order_snapshots' (Invariant R-09, R-10, R-11)
    op.create_table(
        "shopify_order_snapshots",
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
        sa.Column("customer_email", sa.String(255), nullable=False),
        sa.Column("lookup_status", sa.String(30), nullable=False),
        sa.Column("matched_order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_paid_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_active_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_cancelled_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_refunded_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_fulfilled_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("latest_order_id", sa.String(50), nullable=True),
        sa.Column("latest_order_name", sa.String(50), nullable=True),
        sa.Column("latest_order_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_order_financial_status", sa.String(50), nullable=True),
        sa.Column("latest_order_fulfillment_status", sa.String(50), nullable=True),
        sa.Column("latest_order_total_price", sa.String(30), nullable=True),
        sa.Column("latest_order_currency", sa.String(10), nullable=True, server_default="USD"),
        sa.Column("line_items_summary", sa.JSON(), nullable=True),
        sa.Column("lookup_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_shopify_order_snapshots_email_id", "shopify_order_snapshots", ["incoming_email_id"])
    op.create_index("ix_shopify_order_snapshots_store_id", "shopify_order_snapshots", ["store_profile_id"])
    op.create_index("ix_shopify_order_snapshots_customer_email", "shopify_order_snapshots", ["customer_email"])

    # 3. Create table 'shopify_product_snapshots' (Invariant R-16: No Catalog Mirroring)
    op.create_table(
        "shopify_product_snapshots",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "incoming_email_id",
            sa.CHAR(36),
            sa.ForeignKey("incoming_emails.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("search_query", sa.String(255), nullable=False),
        sa.Column("raw_query_terms", sa.JSON(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_resolved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("matched_products", sa.JSON(), nullable=False),
        sa.Column("warning_codes", sa.JSON(), nullable=False),
        sa.Column("execution_time_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_shopify_product_snapshots_email_id", "shopify_product_snapshots", ["incoming_email_id"])
    op.create_index("ix_shopify_product_snapshots_store_id", "shopify_product_snapshots", ["store_profile_id"])

    # 4. Alter table 'incoming_emails' (batch alter for SQLite & PostgreSQL compatibility)
    with op.batch_alter_table("incoming_emails") as batch_op:
        batch_op.add_column(
            sa.Column(
                "order_snapshot_id",
                sa.CHAR(36),
                sa.ForeignKey("shopify_order_snapshots.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "product_snapshot_id",
                sa.CHAR(36),
                sa.ForeignKey("shopify_product_snapshots.id", ondelete="SET NULL"),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("policy_hashes_used", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("stale_reason", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("stale_details", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("incoming_emails") as batch_op:
        batch_op.drop_column("stale_details")
        batch_op.drop_column("stale_reason")
        batch_op.drop_column("is_stale")
        batch_op.drop_column("policy_hashes_used")
        batch_op.drop_column("product_snapshot_id")
        batch_op.drop_column("order_snapshot_id")

    op.drop_index("ix_shopify_product_snapshots_store_id", table_name="shopify_product_snapshots")
    op.drop_index("ix_shopify_product_snapshots_email_id", table_name="shopify_product_snapshots")
    op.drop_table("shopify_product_snapshots")

    op.drop_index("ix_shopify_order_snapshots_customer_email", table_name="shopify_order_snapshots")
    op.drop_index("ix_shopify_order_snapshots_store_id", table_name="shopify_order_snapshots")
    op.drop_index("ix_shopify_order_snapshots_email_id", table_name="shopify_order_snapshots")
    op.drop_table("shopify_order_snapshots")

    op.drop_constraint("uq_store_policies_store_policy_type", "store_policies", type_="unique")
    op.drop_index("ix_store_policies_policy_type", table_name="store_policies")
    op.drop_index("ix_store_policies_store_id", table_name="store_policies")
    op.drop_table("store_policies")
