"""0002_store_and_secrets: Expand StoreProfile, add ProxyProfiles, ShopifyConnections, Mailboxes, CustomPolicies.

Revision ID: 0002_store_and_secrets
Revises: 0001_initial_schema
Create Date: 2026-10-03 05:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_store_and_secrets"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Create table 'proxy_profiles'
    op.create_table(
        "proxy_profiles",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("protocol", sa.String(20), nullable=False, server_default="socks5"),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("encrypted_username", sa.Text(), nullable=True),
        sa.Column("encrypted_password", sa.Text(), nullable=True),
        sa.Column("secret_key_version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("connect_timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_test_status", sa.String(30), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_exit_ip", sa.String(45), nullable=True),
        sa.Column("last_detected_country", sa.String(10), nullable=True),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_proxy_profiles_name", "proxy_profiles", ["name"], unique=True)

    # 2. Alter table 'store_profiles' to add new columns and foreign key
    with op.batch_alter_table("store_profiles") as batch_op:
        batch_op.add_column(sa.Column("canonical_domain", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("proxy_profile_id", sa.CHAR(36), nullable=True))
        batch_op.add_column(sa.Column("custom_warranty_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("custom_cancellation_policy", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("activation_baseline_uid", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("uid_validity", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("secret_key_version", sa.String(20), nullable=False, server_default="v1"))
        batch_op.create_foreign_key(
            "fk_store_profiles_proxy_profile_id",
            "proxy_profiles",
            ["proxy_profile_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_store_profiles_canonical_domain", ["canonical_domain"], unique=True)

    # 3. Create table 'shopify_connections'
    op.create_table(
        "shopify_connections",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("shop_domain", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("encrypted_client_secret", sa.Text(), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("access_token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("granted_scopes", sa.String(500), nullable=True),
        sa.Column("auth_status", sa.String(30), nullable=False, server_default="unconfigured"),
        sa.Column("last_auth_error_code", sa.String(100), nullable=True),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "proxy_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("proxy_profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("secret_key_version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_shopify_connections_store_profile_id", "shopify_connections", ["store_profile_id"], unique=True)
    op.create_index("ix_shopify_connections_shop_domain", "shopify_connections", ["shop_domain"], unique=True)

    # 4. Create table 'mailboxes'
    op.create_table(
        "mailboxes",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column("encrypted_password", sa.Text(), nullable=False),
        sa.Column("secret_key_version", sa.String(20), nullable=False, server_default="v1"),
        sa.Column("imap_host", sa.String(255), nullable=False, server_default="mail.wrydeco.com"),
        sa.Column("imap_port", sa.Integer(), nullable=False, server_default="993"),
        sa.Column("imap_tls_mode", sa.String(20), nullable=False, server_default="SSL"),
        sa.Column("smtp_host", sa.String(255), nullable=False, server_default="mail.wrydeco.com"),
        sa.Column("smtp_port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("smtp_tls_mode", sa.String(20), nullable=False, server_default="STARTTLS"),
        sa.Column("status", sa.String(30), nullable=False, server_default="unconfigured"),
        sa.Column("last_imap_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_smtp_auth_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_mailboxes_store_profile_id", "mailboxes", ["store_profile_id"])
    op.create_index("ix_mailboxes_address", "mailboxes", ["address"])

    # 5. Create table 'custom_policies'
    op.create_table(
        "custom_policies",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column(
            "store_profile_id",
            sa.CHAR(36),
            sa.ForeignKey("store_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_html", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_custom_policies_store_profile_id", "custom_policies", ["store_profile_id"])
    op.create_index("ix_custom_policies_policy_type", "custom_policies", ["policy_type"])


def downgrade() -> None:
    op.drop_table("custom_policies")
    op.drop_table("mailboxes")
    op.drop_table("shopify_connections")
    with op.batch_alter_table("store_profiles") as batch_op:
        batch_op.drop_index("ix_store_profiles_canonical_domain")
        batch_op.drop_constraint("fk_store_profiles_proxy_profile_id", type_="foreignkey")
        batch_op.drop_column("secret_key_version")
        batch_op.drop_column("uid_validity")
        batch_op.drop_column("activation_baseline_uid")
        batch_op.drop_column("custom_cancellation_policy")
        batch_op.drop_column("custom_warranty_policy")
        batch_op.drop_column("proxy_profile_id")
        batch_op.drop_column("canonical_domain")
    op.drop_table("proxy_profiles")
