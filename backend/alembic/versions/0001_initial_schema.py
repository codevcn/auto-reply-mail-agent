"""0001_initial_schema: Initial schema for IAM, Sessions, Audit and StoreProfile.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-10-03 04:00:00.000000
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Core permissions to seed
CORE_PERMISSIONS = [
    ("users:read", "View user accounts"),
    ("users:write", "Create, edit, enable, disable users and reset passwords"),
    ("stores:read", "View store profiles and configurations"),
    ("stores:write", "Manage store profiles and setup wizard"),
    ("proxies:manage", "Manage SOCKS5 proxy profiles and run proxy tests"),
    ("shopify:manage", "Configure Shopify connections and synchronize policies"),
    ("ai:manage", "Configure AI providers and test models"),
    ("mail:read", "View incoming emails and draft replies"),
    ("mail:send", "Approve and dispatch outbound email replies"),
    ("audit:read", "Inspect immutable audit logs and health status"),
]


def upgrade() -> None:
    # 1. Create table 'users'
    op.create_table(
        "users",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("normalized_username", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.CHAR(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_users_normalized_username", "users", ["normalized_username"], unique=True)
    op.create_index("ix_users_status", "users", ["status"])

    # 2. Create table 'roles'
    op.create_table(
        "roles",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
    )
    op.create_index("ix_roles_name", "roles", ["name"], unique=True)

    # 3. Create table 'permissions'
    op.create_table(
        "permissions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
    )
    op.create_index("ix_permissions_code", "permissions", ["code"], unique=True)

    # 4. Create table 'user_roles'
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.CHAR(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_id", sa.CHAR(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 5. Create table 'role_permissions'
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.CHAR(36), sa.ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_id", sa.CHAR(36), sa.ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
    )

    # 6. Create table 'sessions'
    op.create_table(
        "sessions",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("user_id", sa.CHAR(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_secret_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_ip", sa.String(45), nullable=True),
        sa.Column("last_seen_ip", sa.String(45), nullable=True),
        sa.Column("user_agent_summary", sa.String(255), nullable=True),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)
    op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    op.create_index("ix_sessions_revoked_at", "sessions", ["revoked_at"])

    # 7. Create table 'store_profiles' (Placeholder for Phase 2)
    op.create_table(
        "store_profiles",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("brand_name", sa.String(100), nullable=False),
        sa.Column("public_domain", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("brand_description", sa.Text(), nullable=True),
        sa.Column("default_language", sa.String(10), nullable=False, server_default="en"),
        sa.Column("tone_of_voice", sa.String(100), nullable=True),
        sa.Column("email_signature", sa.Text(), nullable=True),
        sa.Column("common_faqs", sa.JSON(), nullable=True),
        sa.Column("forbidden_claims", sa.JSON(), nullable=True),
        sa.Column("escalation_rules", sa.JSON(), nullable=True),
        sa.Column("additional_ai_instructions", sa.Text(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("active_context_version_id", sa.CHAR(36), nullable=True),
        sa.Column("created_by", sa.CHAR(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_store_profiles_name", "store_profiles", ["name"], unique=True)
    op.create_index("ix_store_profiles_status", "store_profiles", ["status"])

    # 8. Create table 'audit_events'
    op.create_table(
        "audit_events",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("actor_user_id", sa.CHAR(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(100), nullable=True),
        sa.Column("store_profile_id", sa.CHAR(36), sa.ForeignKey("store_profiles.id", ondelete="SET NULL"), nullable=True),
        sa.Column("request_correlation_id", sa.String(100), nullable=True),
        sa.Column("source_ip", sa.String(45), nullable=True),
        sa.Column("safe_change_summary", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_events_actor_user_id", "audit_events", ["actor_user_id"])
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_store_profile_id", "audit_events", ["store_profile_id"])
    op.create_index("ix_audit_events_request_correlation_id", "audit_events", ["request_correlation_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    # 9. Seed Default RBAC: 'admin' role & core permissions
    admin_role_id = str(uuid.uuid4())
    op.execute(
        sa.text("INSERT INTO roles (id, name, description) VALUES (:id, :name, :description)").bindparams(
            id=admin_role_id, name="admin", description="Full system administrator"
        )
    )

    for code, desc in CORE_PERMISSIONS:
        perm_id = str(uuid.uuid4())
        op.execute(
            sa.text("INSERT INTO permissions (id, code, description) VALUES (:id, :code, :description)").bindparams(
                id=perm_id, code=code, description=desc
            )
        )
        op.execute(
            sa.text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:role_id, :permission_id)").bindparams(
                role_id=admin_role_id, permission_id=perm_id
            )
        )


def downgrade() -> None:
    # Drop in reverse order of foreign key dependencies
    op.drop_table("audit_events")
    op.drop_table("store_profiles")
    op.drop_table("sessions")
    op.drop_table("role_permissions")
    op.drop_table("user_roles")
    op.drop_table("permissions")
    op.drop_table("roles")
    op.drop_table("users")
