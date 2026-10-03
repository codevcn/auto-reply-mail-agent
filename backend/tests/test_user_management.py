"""User Management & Session Revocation Tests.
Covers: Creating users, duplicate username rejection, disabling/enabling users, session revocation on disable & reset.
"""

import pytest
from app.db.models.session import Session
from app.db.models.user import User
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, bootstrap_user):
    """Verifies listing users returns all created users without exposing password hashes."""
    await bootstrap_user("admin_one", "Password123!")
    await bootstrap_user("admin_two", "Password123!")

    # Login as admin_one
    await client.post(
        "/api/auth/login",
        json={"username": "admin_one", "password": "Password123!"},
    )

    res = await client.get("/api/users")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 2
    usernames = [u["username"] for u in data]
    assert "admin_one" in usernames
    assert "admin_two" in usernames
    # Ensure password hashes are never exposed
    for u in data:
        assert "password_hash" not in u


@pytest.mark.asyncio
async def test_create_user_and_prevent_duplicate(client: AsyncClient, bootstrap_user):
    """Verifies creating new user accounts and rejecting duplicates."""
    await bootstrap_user("lead_admin", "Password123!")
    await client.post(
        "/api/auth/login",
        json={"username": "lead_admin", "password": "Password123!"},
    )

    # Create new operator
    res = await client.post(
        "/api/users",
        json={"username": "new_operator", "password": "OperatorPass123!"},
    )
    assert res.status_code == 201
    assert res.json()["username"] == "new_operator"
    assert res.json()["status"] == "active"

    # Try creating duplicate with different case
    dup_res = await client.post(
        "/api/users",
        json={"username": "NEW_OPERATOR", "password": "AnotherPass123!"},
    )
    assert dup_res.status_code == 409
    assert dup_res.json()["error_code"] == "USER_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_disable_and_enable_user(client: AsyncClient, bootstrap_user, db_session: AsyncSession):
    """Verifies disabling and re-enabling a user account."""
    await bootstrap_user("master_admin", "Password123!")
    target = await bootstrap_user("operator_target", "Password123!")

    await client.post(
        "/api/auth/login",
        json={"username": "master_admin", "password": "Password123!"},
    )

    # Disable operator
    dis_res = await client.patch(f"/api/users/{target.id}/disable")
    assert dis_res.status_code == 200
    assert dis_res.json()["status"] == "disabled"

    # Verify target status in DB
    updated = await db_session.get(User, target.id)
    assert updated is not None
    assert updated.status == "disabled"

    # Re-enable operator
    en_res = await client.patch(f"/api/users/{target.id}/enable")
    assert en_res.status_code == 200
    assert en_res.json()["status"] == "active"


@pytest.mark.asyncio
async def test_disable_user_immediately_revokes_all_active_sessions(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """TC-AUTH-06: Disabling a user immediately invalidates all their active sessions."""
    await bootstrap_user("super_admin", "Password123!")
    staff = await bootstrap_user("staff_target", "Password123!")

    # Login as staff to create a session
    staff_login = await client.post(
        "/api/auth/login",
        json={"username": "staff_target", "password": "Password123!"},
    )
    staff_cookie = staff_login.cookies.get("session_id")
    assert staff_cookie is not None

    # Check staff session in DB is valid
    staff_sess = (
        await db_session.execute(
            select(Session).where(Session.user_id == staff.id)
        )
    ).scalars().first()
    assert staff_sess is not None
    assert staff_sess.revoked_at is None

    # Now login as admin and disable staff
    await client.post(
        "/api/auth/login",
        json={"username": "super_admin", "password": "Password123!"},
    )
    dis_res = await client.patch(f"/api/users/{staff.id}/disable")
    assert dis_res.status_code == 200

    # Verify staff session in DB is revoked
    await db_session.refresh(staff_sess)
    assert staff_sess.revoked_at is not None

    # Using staff's old session cookie to access me must be rejected
    test_client = AsyncClient(transport=client._transport, base_url="http://test")
    test_client.cookies.set("session_id", staff_cookie)
    me_res = await test_client.get("/api/auth/me")
    assert me_res.status_code == 401


@pytest.mark.asyncio
async def test_reset_password_revokes_active_sessions(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """Verifies reset-password updates hash and revokes all active sessions."""
    await bootstrap_user("admin_resetter", "Password123!")
    staff = await bootstrap_user("staff_to_reset", "OldPassword123!")

    # Login as staff
    staff_login = await client.post(
        "/api/auth/login",
        json={"username": "staff_to_reset", "password": "OldPassword123!"},
    )
    staff_cookie = staff_login.cookies.get("session_id")

    # Admin resets staff password
    await client.post(
        "/api/auth/login",
        json={"username": "admin_resetter", "password": "Password123!"},
    )
    reset_res = await client.post(
        f"/api/users/{staff.id}/reset-password",
        json={"new_password": "NewSecretPassword456!", "must_change_password": True},
    )
    assert reset_res.status_code == 200

    # Old session is revoked
    test_client = AsyncClient(transport=client._transport, base_url="http://test")
    test_client.cookies.set("session_id", staff_cookie)
    me_res = await test_client.get("/api/auth/me")
    assert me_res.status_code == 401

    # Login with new password succeeds
    login_new = await test_client.post(
        "/api/auth/login",
        json={"username": "staff_to_reset", "password": "NewSecretPassword456!"},
    )
    assert login_new.status_code == 200
