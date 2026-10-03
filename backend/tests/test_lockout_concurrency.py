"""User Lockout Safeguards & Concurrency Invariant Tests.
Covers: TC-AUTH-04 (Self-disable blocked), TC-AUTH-05 (Last active user blocked), Concurrent disable race protection.
"""

import asyncio
import uuid
from pathlib import Path

import pytest
from app.auth.services import AuthBusinessError, UserService
from app.core.security import hash_password
from app.db.base import Base
from app.db.models.role import Role, UserRole
from app.db.models.user import User
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


@pytest.mark.asyncio
async def test_self_disable_strictly_blocked(client: AsyncClient, bootstrap_user, db_session: AsyncSession):
    """TC-AUTH-04: User cannot disable their own account (Invariant R-28)."""
    admin = await bootstrap_user("self_admin", "Password123!")

    # Login as admin
    await client.post(
        "/api/auth/login",
        json={"username": "self_admin", "password": "Password123!"},
    )

    # Admin attempts to self-disable
    res = await client.patch(f"/api/users/{admin.id}/disable")
    assert res.status_code == 400
    err_data = res.json()
    assert err_data["error_code"] == "SELF_DISABLE_NOT_ALLOWED"
    assert "Self-disable is not permitted" in err_data["message"]

    # Verify admin remains active in DB
    refreshed = await db_session.get(User, admin.id)
    assert refreshed is not None
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_last_active_user_disable_invariant_blocked(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """TC-AUTH-05: Cannot disable last active user in the system (Invariant R-28)."""
    admin1 = await bootstrap_user("admin_one", "Password123!")
    admin2 = await bootstrap_user("admin_two", "Password123!")

    # Login as admin1
    await client.post(
        "/api/auth/login",
        json={"username": "admin_one", "password": "Password123!"},
    )

    # Disable admin2 -> should succeed because admin1 is still active
    dis1 = await client.patch(f"/api/users/{admin2.id}/disable")
    assert dis1.status_code == 200
    assert dis1.json()["status"] == "disabled"

    # Now admin1 is the ONLY active user in the system.
    # An attempt (even by direct service call or another actor) to disable admin1 must fail
    user_service = UserService(db_session)
    with pytest.raises(AuthBusinessError) as exc_info:
        await user_service.disable_user(actor_user_id=admin2.id, target_user_id=admin1.id)

    assert exc_info.value.code == "LAST_ACTIVE_USER_REQUIRED"
    assert "Cannot disable the last active user" in exc_info.value.message

    # Admin1 remains active
    refreshed = await db_session.get(User, admin1.id)
    assert refreshed is not None
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_concurrent_disable_cannot_leave_zero_active_users(tmp_path: Path):
    """Simulates 2 concurrent disable operations targeting the only 2 active users.

    Under concurrency, strictly one request must succeed and the second must be rejected
    with LAST_ACTIVE_USER_REQUIRED. The system must NEVER be left with 0 active users.
    """
    db_file = tmp_path / "concurrent_race.db"
    concurrent_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        poolclass=NullPool,
        connect_args={"timeout": 30},
    )

    async with concurrent_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=concurrent_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as setup_session:
        admin_role = Role(id=uuid.uuid4(), name="admin", description="Admin role")
        setup_session.add(admin_role)
        await setup_session.flush()

        user_a = User(
            id=uuid.uuid4(),
            username="concurrent_a",
            normalized_username="concurrent_a",
            password_hash=hash_password("Password123!"),
            status="active",
            row_version=1,
        )
        user_b = User(
            id=uuid.uuid4(),
            username="concurrent_b",
            normalized_username="concurrent_b",
            password_hash=hash_password("Password123!"),
            status="active",
            row_version=1,
        )
        setup_session.add_all([user_a, user_b])
        await setup_session.flush()
        setup_session.add_all([
            UserRole(user_id=user_a.id, role_id=admin_role.id),
            UserRole(user_id=user_b.id, role_id=admin_role.id),
        ])
        await setup_session.commit()

        user_a_id = user_a.id
        user_b_id = user_b.id

    # Task 1: user_a disables user_b
    async def request_disable_b():
        async with session_factory() as session1:
            srv = UserService(session1)
            return await srv.disable_user(actor_user_id=user_a_id, target_user_id=user_b_id)

    # Task 2: user_b disables user_a
    async def request_disable_a():
        async with session_factory() as session2:
            srv = UserService(session2)
            return await srv.disable_user(actor_user_id=user_b_id, target_user_id=user_a_id)

    # Execute concurrently
    results = await asyncio.gather(
        request_disable_b(),
        request_disable_a(),
        return_exceptions=True,
    )

    successes = [r for r in results if isinstance(r, User)]
    errors = [r for r in results if isinstance(r, AuthBusinessError)]

    # Exactly one must succeed and one must fail
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
    assert len(errors) == 1, f"Expected 1 error, got {len(errors)}: {results}"
    assert errors[0].code == "LAST_ACTIVE_USER_REQUIRED"

    # Verify at database level: exactly 1 active user remains
    async with session_factory() as check_session:
        active_users = (
            await check_session.execute(
                select(User).where(User.status == "active")
            )
        ).scalars().all()
        assert len(active_users) == 1, "System must maintain at least 1 active user at all times"

    await concurrent_engine.dispose()
