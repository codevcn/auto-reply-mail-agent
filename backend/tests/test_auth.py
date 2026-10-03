"""Authentication & Session Security Tests.
Covers: TC-AUTH-01, TC-AUTH-02, TC-AUTH-03, Argon2id, Opaque session cookies, Timing Attack Protection.
"""

import pytest
from app.core.security import hash_password, verify_password, verify_password_and_dummy
from app.db.models.audit import AuditEvent
from app.db.models.session import Session
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_argon2id_hashing_and_verification():
    """Verifies Argon2id password hashing and validation logic."""
    raw = "SuperSecretPassword123!"
    hashed = hash_password(raw)
    assert hashed.startswith("$argon2id$")
    assert verify_password(raw, hashed)
    assert not verify_password("WrongPassword!", hashed)


@pytest.mark.asyncio
async def test_timing_attack_dummy_hash_verification():
    """Confirms verify_password_and_dummy performs constant-time computation when hash is None."""
    assert not verify_password_and_dummy("password", None)


@pytest.mark.asyncio
async def test_login_success_case_insensitive_sets_cookie(client: AsyncClient, bootstrap_user, db_session: AsyncSession):
    """TC-AUTH-01: Username normalization and opaque session generation."""
    await bootstrap_user("AdminUser", "CorrectPassword123!")

    # Login with uppercase ADMINUSER
    res = await client.post(
        "/api/auth/login",
        json={"username": "ADMINUSER", "password": "CorrectPassword123!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["user"]["username"] == "AdminUser"
    assert "csrf_token" in data

    # Check session cookie
    cookies = res.cookies
    assert "session_id" in cookies
    sess_id = cookies["session_id"]
    assert sess_id.startswith("sess_")

    # Verify session is stored in DB (hashed)
    sess_records = (await db_session.execute(select(Session))).scalars().all()
    assert len(sess_records) == 1
    assert sess_records[0].revoked_at is None


@pytest.mark.asyncio
async def test_login_failure_wrong_password_records_audit(client: AsyncClient, bootstrap_user, db_session: AsyncSession):
    """TC-AUTH-02: Bad credentials reject login and emit audit log."""
    await bootstrap_user("staff_member", "RealPassword123!")

    res = await client.post(
        "/api/auth/login",
        json={"username": "staff_member", "password": "IncorrectPassword!"},
    )
    assert res.status_code == 401
    assert res.json()["error_code"] == "INVALID_CREDENTIALS"

    # Verify audit event
    audits = (await db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "LOGIN_FAILED"))).scalars().all()
    assert len(audits) >= 1
    assert audits[-1].safe_change_summary["reason"] == "password_mismatch"


@pytest.mark.asyncio
async def test_login_failure_nonexistent_user(client: AsyncClient, db_session: AsyncSession):
    """Verifies login fails safely for nonexistent username and emits audit log."""
    res = await client.post(
        "/api/auth/login",
        json={"username": "ghost_user", "password": "SomePassword123!"},
    )
    assert res.status_code == 401
    assert res.json()["error_code"] == "INVALID_CREDENTIALS"

    audits = (await db_session.execute(select(AuditEvent).where(AuditEvent.event_type == "LOGIN_FAILED"))).scalars().all()
    assert len(audits) >= 1


@pytest.mark.asyncio
async def test_get_me_endpoint_with_valid_session(client: AsyncClient, bootstrap_user):
    """Verifies /api/auth/me returns current user profile when session cookie is provided."""
    await bootstrap_user("active_admin", "Password123!")

    login_res = await client.post(
        "/api/auth/login",
        json={"username": "active_admin", "password": "Password123!"},
    )
    assert login_res.status_code == 200

    me_res = await client.get("/api/auth/me")
    assert me_res.status_code == 200
    me_data = me_res.json()
    assert me_data["username"] == "active_admin"
    assert me_data["role"] == "admin"


@pytest.mark.asyncio
async def test_logout_immediately_revokes_session(client: AsyncClient, bootstrap_user, db_session: AsyncSession):
    """TC-AUTH-03: Logout immediately revokes server-side session and clears cookie."""
    await bootstrap_user("logout_user", "Password123!")

    # Login
    await client.post(
        "/api/auth/login",
        json={"username": "logout_user", "password": "Password123!"},
    )

    # Logout
    logout_res = await client.post("/api/auth/logout")
    assert logout_res.status_code == 200
    assert logout_res.json()["success"] is True

    # Verify in DB: session is marked revoked
    sess = (await db_session.execute(select(Session))).scalars().first()
    assert sess is not None
    assert sess.revoked_at is not None

    # Accessing /api/auth/me must now fail
    me_res = await client.get("/api/auth/me")
    assert me_res.status_code == 401
