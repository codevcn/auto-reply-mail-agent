"""Empirical Adversarial Stress Test Suite for Auth, Session, and Concurrency Lockout Guards.

Covers:
1. Concurrency Race Conditions & Anti-Lockout Guards (Invariant R-28).
2. Self-Disable Protection (Actor cannot self-disable).
3. Immediate Session Revocation on Disable & Password Reset (100% blocking).
4. Username Normalization & Duplicate Prevention (Case-folding & whitespace).
5. Dummy Hash Timing Attack Protection (Argon2id constant-time mitigation).
"""

import asyncio
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import app.auth.services as auth_services
import pytest
from app.auth.services import AuthBusinessError, UserService
from app.core.security import (
    hash_password,
    verify_password_and_dummy,
)
from app.db.base import Base
from app.db.models.role import Permission, Role, RolePermission, UserRole
from app.db.models.session import Session
from app.db.models.user import User
from app.db.session import get_db
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from tests.conftest import CORE_PERMISSIONS


@pytest.fixture(autouse=True)
def reset_module_user_mutation_lock():
    """Resets module-level asyncio.Lock for the current test event loop.

    Addresses the architectural edge case where a module-level asyncio.Lock
    binds to a single event loop and causes RuntimeError across pytest async loops.
    """
    auth_services._user_mutation_lock = asyncio.Lock()
    yield
    auth_services._user_mutation_lock = asyncio.Lock()


# ==============================================================================
# Helper for multi-connection concurrent database testing
# ==============================================================================
async def create_isolated_sqlite_factory(db_path: Path):
    """Creates an async engine and sessionmaker using a file-backed SQLite database with timeout."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"timeout": 60},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

    # Seed admin role and permissions
    async with session_factory() as session:
        admin_role = Role(id=uuid.uuid4(), name="admin", description="Admin role")
        session.add(admin_role)
        await session.flush()
        for code, desc in CORE_PERMISSIONS:
            p = Permission(id=uuid.uuid4(), code=code, description=desc)
            session.add(p)
            await session.flush()
            session.add(RolePermission(role_id=admin_role.id, permission_id=p.id))
        await session.commit()

    return engine, session_factory


# ==============================================================================
# Vector 1: Race Condition & Concurrency Lockout Guards (Invariant R-28)
# ==============================================================================
@pytest.mark.asyncio
async def test_adversarial_mutual_disable_race_never_leaves_zero_active(tmp_path: Path):
    """STRESS TEST 1.1: Two active users concurrently attempt to disable each other.

    Invariant R-28 requires that under extreme concurrency, strictly one operation
    may succeed and the other MUST fail with LAST_ACTIVE_USER_REQUIRED.
    The database must NEVER reach 0 active users.
    """
    db_file = tmp_path / "mutual_disable_race.db"
    engine, session_factory = await create_isolated_sqlite_factory(db_file)

    async with session_factory() as s:
        admin_role = (await s.execute(select(Role).where(Role.name == "admin"))).scalar_one()
        u1 = User(
            id=uuid.uuid4(),
            username="user_one",
            normalized_username="user_one",
            password_hash=hash_password("Password123!"),
            status="active",
        )
        u2 = User(
            id=uuid.uuid4(),
            username="user_two",
            normalized_username="user_two",
            password_hash=hash_password("Password123!"),
            status="active",
        )
        s.add_all([u1, u2])
        await s.flush()
        s.add_all([
            UserRole(user_id=u1.id, role_id=admin_role.id),
            UserRole(user_id=u2.id, role_id=admin_role.id),
        ])
        await s.commit()
        u1_id, u2_id = u1.id, u2.id

    async def task_disable_u2_by_u1():
        async with session_factory() as sess:
            srv = UserService(sess)
            return await srv.disable_user(actor_user_id=u1_id, target_user_id=u2_id)

    async def task_disable_u1_by_u2():
        async with session_factory() as sess:
            srv = UserService(sess)
            return await srv.disable_user(actor_user_id=u2_id, target_user_id=u1_id)

    # Launch both tasks concurrently
    res1, res2 = await asyncio.gather(
        task_disable_u2_by_u1(),
        task_disable_u1_by_u2(),
        return_exceptions=True,
    )

    results = [res1, res2]
    successes = [r for r in results if isinstance(r, User)]
    errors = [r for r in results if isinstance(r, AuthBusinessError)]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
    assert len(errors) == 1, f"Expected exactly 1 error, got {len(errors)}"
    assert errors[0].code == "LAST_ACTIVE_USER_REQUIRED"

    # Verify directly from database
    async with session_factory() as s:
        active_users = (
            await s.execute(select(User).where(User.status == "active"))
        ).scalars().all()
        assert len(active_users) == 1, "INVARIANT VIOLATED: DB must have exactly 1 active user remaining!"

    await engine.dispose()


@pytest.mark.asyncio
async def test_adversarial_n_way_concurrent_disable_storm(tmp_path: Path):
    """STRESS TEST 1.2: Multi-worker concurrency storm on N active users.

    Simulates 5 active users. 20 concurrent tasks are fired, with random actors
    attempting to disable targets. The remaining active user count MUST be >= 1 at all times.
    """
    db_file = tmp_path / "n_way_disable_storm.db"
    engine, session_factory = await create_isolated_sqlite_factory(db_file)

    user_ids = []
    async with session_factory() as s:
        admin_role = (await s.execute(select(Role).where(Role.name == "admin"))).scalar_one()
        for i in range(5):
            u = User(
                id=uuid.uuid4(),
                username=f"storm_user_{i}",
                normalized_username=f"storm_user_{i}",
                password_hash=hash_password("Password123!"),
                status="active",
            )
            s.add(u)
            await s.flush()
            s.add(UserRole(user_id=u.id, role_id=admin_role.id))
            user_ids.append(u.id)
        await s.commit()

    # Generate 20 distinct mutation attempts: actor != target
    attempts = []
    for i in range(5):
        for j in range(5):
            if i != j:
                attempts.append((user_ids[i], user_ids[j]))

    async def execute_disable_attempt(actor_id, target_id):
        async with session_factory() as sess:
            srv = UserService(sess)
            try:
                return await srv.disable_user(actor_user_id=actor_id, target_user_id=target_id)
            except AuthBusinessError as e:
                return e

    # Fire all 20 attempts concurrently
    results = await asyncio.gather(
        *(execute_disable_attempt(actor, target) for actor, target in attempts)
    )

    successes = [r for r in results if isinstance(r, User)]
    errors = [r for r in results if isinstance(r, AuthBusinessError)]

    # At most 4 users can ever be successfully disabled (out of 5 initial active users)
    assert len(successes) <= 4, f"Cannot disable all active users! Succeeded: {len(successes)}"
    assert len(errors) >= 16

    # Verify database state: MUST contain at least 1 active user
    async with session_factory() as s:
        active_users = (
            await s.execute(select(User).where(User.status == "active"))
        ).scalars().all()
        assert len(active_users) >= 1, "INVARIANT VIOLATION: Zero active users left in database!"
        # Specifically, since attempts covered all pairs, exactly 1 active user must remain
        assert len(active_users) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_adversarial_concurrent_disable_via_http_endpoints(tmp_path: Path):
    """STRESS TEST 1.3: Concurrency race directly through FastAPI HTTP endpoints.

    Admin A and Admin B both attempt to disable Admin C, or attempt mutual disable via HTTP PATCH.
    """
    db_file = tmp_path / "http_race.db"
    engine, session_factory = await create_isolated_sqlite_factory(db_file)

    async with session_factory() as s:
        admin_role = (await s.execute(select(Role).where(Role.name == "admin"))).scalar_one()
        user_a = User(
            id=uuid.uuid4(),
            username="http_admin_a",
            normalized_username="http_admin_a",
            password_hash=hash_password("Pass123456!"),
            status="active",
        )
        user_b = User(
            id=uuid.uuid4(),
            username="http_admin_b",
            normalized_username="http_admin_b",
            password_hash=hash_password("Pass123456!"),
            status="active",
        )
        s.add_all([user_a, user_b])
        await s.flush()
        s.add_all([
            UserRole(user_id=user_a.id, role_id=admin_role.id),
            UserRole(user_id=user_b.id, role_id=admin_role.id),
        ])
        await s.commit()
        id_a, id_b = user_a.id, user_b.id

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client_a, \
               AsyncClient(transport=transport, base_url="http://test") as client_b:

        # Login client_a as user_a
        resp_a = await client_a.post("/api/auth/login", json={"username": "http_admin_a", "password": "Pass123456!"})
        assert resp_a.status_code == 200

        # Login client_b as user_b
        resp_b = await client_b.post("/api/auth/login", json={"username": "http_admin_b", "password": "Pass123456!"})
        assert resp_b.status_code == 200

        # Concurrently fire mutual disable
        r_a, r_b = await asyncio.gather(
            client_a.patch(f"/api/users/{id_b}/disable"),
            client_b.patch(f"/api/users/{id_a}/disable"),
        )

        statuses = [r_a.status_code, r_b.status_code]
        assert 200 in statuses, "One request should succeed"
        assert 400 in statuses, "One request should be blocked"

        failed_resp = r_a if r_a.status_code == 400 else r_b
        assert failed_resp.json()["error_code"] == "LAST_ACTIVE_USER_REQUIRED"

    # Verify at DB level
    async with session_factory() as s:
        active_count = len((await s.execute(select(User).where(User.status == "active"))).scalars().all())
        assert active_count == 1, "Invariant R-28 violated: active count is not 1"

    app.dependency_overrides.clear()
    await engine.dispose()


# ==============================================================================
# Vector 2: Self-Disable Protection
# ==============================================================================
@pytest.mark.asyncio
async def test_adversarial_self_disable_patch_and_post_blocked(client: AsyncClient, bootstrap_user):
    """STRESS TEST 2.1: Verify self-disable is unconditionally blocked on PATCH and POST."""
    admin = await bootstrap_user("self_tester", "Password123!")

    # Login
    await client.post("/api/auth/login", json={"username": "self_tester", "password": "Password123!"})

    # Try PATCH self-disable
    patch_res = await client.patch(f"/api/users/{admin.id}/disable")
    assert patch_res.status_code == 400
    assert patch_res.json()["error_code"] == "SELF_DISABLE_NOT_ALLOWED"

    # Try POST alias self-disable
    post_res = await client.post(f"/api/users/{admin.id}/disable")
    assert post_res.status_code == 400
    assert post_res.json()["error_code"] == "SELF_DISABLE_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_adversarial_self_disable_blocked_even_with_multiple_active_users(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """STRESS TEST 2.2: Even with 10 active admins, self-disable MUST be rejected with SELF_DISABLE_NOT_ALLOWED.

    This ensures self-disable check is distinct and precedes last-active-user check.
    """
    admin1 = await bootstrap_user("main_admin", "Password123!")
    for i in range(9):
        await bootstrap_user(f"other_admin_{i}", "Password123!")

    # Login as admin1
    await client.post("/api/auth/login", json={"username": "main_admin", "password": "Password123!"})

    # Attempt self-disable
    res = await client.patch(f"/api/users/{admin1.id}/disable")
    assert res.status_code == 400
    assert res.json()["error_code"] == "SELF_DISABLE_NOT_ALLOWED"
    assert "Self-disable is not permitted" in res.json()["message"]

    # Verify admin1 is still active
    refreshed = await db_session.get(User, admin1.id)
    assert refreshed.status == "active"


@pytest.mark.asyncio
async def test_adversarial_self_disable_concurrency_hammer(tmp_path: Path):
    """STRESS TEST 2.3: 10 parallel HTTP requests attempting self-disable simultaneously.

    Uses independent DB sessions per request to accurately model real concurrent traffic.
    """
    db_file = tmp_path / "hammer_self_disable.db"
    engine, session_factory = await create_isolated_sqlite_factory(db_file)

    async with session_factory() as s:
        admin_role = (await s.execute(select(Role).where(Role.name == "admin"))).scalar_one()
        admin = User(
            id=uuid.uuid4(),
            username="hammer_admin",
            normalized_username="hammer_admin",
            password_hash=hash_password("Password123!"),
            status="active",
        )
        s.add(admin)
        await s.flush()
        s.add(UserRole(user_id=admin.id, role_id=admin_role.id))
        await s.commit()
        admin_id = admin.id

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login_res = await c.post("/api/auth/login", json={"username": "hammer_admin", "password": "Password123!"})
        assert login_res.status_code == 200
        cookie_val = login_res.cookies["session_id"]

        # Run 10 requests concurrently each with their own client instance sharing the session cookie
        async def send_self_disable():
            async with AsyncClient(transport=transport, base_url="http://test") as sub_client:
                sub_client.cookies.set("session_id", cookie_val)
                return await sub_client.patch(f"/api/users/{admin_id}/disable")

        results = await asyncio.gather(*(send_self_disable() for _ in range(10)))

        for r in results:
            assert r.status_code == 400
            assert r.json()["error_code"] == "SELF_DISABLE_NOT_ALLOWED"

    async with session_factory() as s:
        refreshed = await s.get(User, admin_id)
        assert refreshed.status == "active"

    app.dependency_overrides.clear()
    await engine.dispose()


# ==============================================================================
# Vector 3: Immediate Session Revocation on Disable & Password Reset
# ==============================================================================
@pytest.mark.asyncio
async def test_adversarial_multi_session_revocation_on_disable(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """STRESS TEST 3.1: User has 5 active sessions from different browsers/devices.

    When disabled:
    1. 100% of the 5 sessions must be marked revoked in DB immediately.
    2. All 5 session tokens must be rejected (HTTP 401) immediately.
    3. If re-enabled, the old sessions MUST REMAIN REVOKED.
    """
    _admin = await bootstrap_user("actor_admin", "Password123!")
    target = await bootstrap_user("multi_session_target", "TargetPass123!")

    # Create 5 distinct sessions for target user
    session_tokens = []
    transport = client._transport

    for _ in range(5):
        c = AsyncClient(transport=transport, base_url="http://test")
        login_res = await c.post(
            "/api/auth/login",
            json={"username": "multi_session_target", "password": "TargetPass123!"},
        )
        assert login_res.status_code == 200
        cookie_token = login_res.cookies.get("session_id")
        assert cookie_token is not None
        session_tokens.append(cookie_token)

    # Verify all 5 sessions are active
    for tok in session_tokens:
        c = AsyncClient(transport=transport, base_url="http://test")
        c.cookies.set("session_id", tok)
        me_res = await c.get("/api/auth/me")
        assert me_res.status_code == 200

    # Login as admin and disable target
    await client.post("/api/auth/login", json={"username": "actor_admin", "password": "Password123!"})
    dis_res = await client.patch(f"/api/users/{target.id}/disable")
    assert dis_res.status_code == 200

    # 1. Verify in DB: ALL 5 sessions have revoked_at NOT NULL
    sessions_in_db = (
        await db_session.execute(select(Session).where(Session.user_id == target.id))
    ).scalars().all()
    assert len(sessions_in_db) == 5
    for s in sessions_in_db:
        assert s.revoked_at is not None, f"Session {s.id} was not revoked!"

    # 2. Verify all 5 session tokens are rejected immediately
    for tok in session_tokens:
        c = AsyncClient(transport=transport, base_url="http://test")
        c.cookies.set("session_id", tok)
        me_res = await c.get("/api/auth/me")
        assert me_res.status_code == 401
        assert me_res.json()["error_code"] == "UNAUTHENTICATED"

    # 3. Re-enable target user
    en_res = await client.patch(f"/api/users/{target.id}/enable")
    assert en_res.status_code == 200

    # 4. Old sessions must STILL be rejected!
    for tok in session_tokens:
        c = AsyncClient(transport=transport, base_url="http://test")
        c.cookies.set("session_id", tok)
        me_res = await c.get("/api/auth/me")
        assert me_res.status_code == 401, "Old revoked session became valid after user re-enable!"


@pytest.mark.asyncio
async def test_adversarial_session_revocation_on_password_reset(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """STRESS TEST 3.2: Password reset must revoke all current sessions immediately."""
    _admin = await bootstrap_user("reset_admin", "Password123!")
    user = await bootstrap_user("reset_victim", "OldVictimPass123!")

    # Establish 3 sessions for victim
    victim_tokens = []
    transport = client._transport
    for _ in range(3):
        c = AsyncClient(transport=transport, base_url="http://test")
        resp = await c.post("/api/auth/login", json={"username": "reset_victim", "password": "OldVictimPass123!"})
        victim_tokens.append(resp.cookies["session_id"])

    # Admin resets password
    await client.post("/api/auth/login", json={"username": "reset_admin", "password": "Password123!"})
    reset_res = await client.post(
        f"/api/users/{user.id}/reset-password",
        json={"new_password": "BrandNewPassword789!", "must_change_password": True},
    )
    assert reset_res.status_code == 200

    # Old sessions must all fail with 401
    for tok in victim_tokens:
        c = AsyncClient(transport=transport, base_url="http://test")
        c.cookies.set("session_id", tok)
        assert (await c.get("/api/auth/me")).status_code == 401

    # Login with old password must fail
    bad_login = await client.post(
        "/api/auth/login",
        json={"username": "reset_victim", "password": "OldVictimPass123!"},
    )
    assert bad_login.status_code == 401

    # Login with new password must succeed
    good_login = await client.post(
        "/api/auth/login",
        json={"username": "reset_victim", "password": "BrandNewPassword789!"},
    )
    assert good_login.status_code == 200


@pytest.mark.asyncio
async def test_adversarial_tampered_and_expired_session_handling(client: AsyncClient, bootstrap_user, db_session: AsyncSession):
    """STRESS TEST 3.3: Invalidation against tampered, forged, expired or SQLi session tokens."""
    await bootstrap_user("normal_user", "Password123!")

    # Test forged/tampered tokens
    malicious_tokens = [
        "sess_0000000000000000000000000000000000000000000000000000000000000000",
        "sess_' OR '1'='1",
        "sess_<script>alert(1)</script>",
        "sess_" + "a" * 1000,
        "not_even_a_session_token",
        "",
    ]

    transport = client._transport
    for bad_tok in malicious_tokens:
        c = AsyncClient(transport=transport, base_url="http://test")
        if bad_tok:
            c.cookies.set("session_id", bad_tok)
        resp = await c.get("/api/auth/me")
        assert resp.status_code == 401, f"Expected 401 for token {bad_tok!r}, got {resp.status_code}"
        assert resp.json()["error_code"] == "UNAUTHENTICATED"


# ==============================================================================
# Vector 4: Username Normalization & Duplicate Prevention
# ==============================================================================
@pytest.mark.asyncio
async def test_adversarial_username_normalization_and_variants(client: AsyncClient, bootstrap_user):
    """STRESS TEST 4.1: Canonical normalization across case-folding and whitespace variations.

    ADMINUSER, adminuser , AdminUser must access the SAME account.
    """
    target = await bootstrap_user("AdminUser", "ComplexPass123!")

    test_variants = [
        "ADMINUSER",
        "adminuser ",
        " AdminUser",
        "  adminuser  ",
        "\tAdminUser\t",
        "aDmInUsEr",
    ]

    transport = client._transport
    for variant in test_variants:
        c = AsyncClient(transport=transport, base_url="http://test")
        res = await c.post(
            "/api/auth/login",
            json={"username": variant, "password": "ComplexPass123!"},
        )
        assert res.status_code == 200, f"Variant {variant!r} failed to login!"
        data = res.json()
        assert data["user"]["id"] == str(target.id)
        assert data["user"]["username"] == "AdminUser"


@pytest.mark.asyncio
async def test_adversarial_duplicate_username_creation_strictly_blocked(client: AsyncClient, bootstrap_user):
    """STRESS TEST 4.2: Creating user variants in different cases/whitespace must be rejected (409 Conflict)."""
    await bootstrap_user("MasterOperator", "Password123!")

    # Login as MasterOperator
    await client.post("/api/auth/login", json={"username": "masteroperator", "password": "Password123!"})

    # Try creating variants of the existing user
    collision_variants = [
        "masteroperator",
        "MASTEROPERATOR",
        "MasterOperator ",
        "  masteroperator  ",
        "mAsTeRoPeRaToR",
    ]

    for variant in collision_variants:
        res = await client.post(
            "/api/users",
            json={"username": variant, "password": "NewSecretPass456!"},
        )
        assert res.status_code == 409, f"Variant {variant!r} was not rejected with 409!"
        assert res.json()["error_code"] == "USER_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_adversarial_username_edge_cases_rejected(client: AsyncClient, bootstrap_user):
    """STRESS TEST 4.3: Invalid usernames (empty, whitespace-only, short) must be rejected with 400 or 422."""
    await bootstrap_user("valid_admin", "Password123!")
    await client.post("/api/auth/login", json={"username": "valid_admin", "password": "Password123!"})

    invalid_names = [
        "",
        "   ",
        "a",
        "ab",
        "  a  ",
        "\t\t",
    ]

    for name in invalid_names:
        res = await client.post(
            "/api/users",
            json={"username": name, "password": "NewSecretPass456!"},
        )
        assert res.status_code in (400, 422), f"Expected 400 or 422 for invalid username {name!r}, got {res.status_code}"


# ==============================================================================
# Vector 5: Dummy Hash Timing Attack Protection
# ==============================================================================
@pytest.mark.asyncio
async def test_adversarial_dummy_hash_timing_protection_invoked_on_nonexistent_user(client: AsyncClient):
    """STRESS TEST 5.1: Non-existent username login MUST invoke verify_password_and_dummy with None hash."""
    with patch("app.auth.services.verify_password_and_dummy", wraps=verify_password_and_dummy) as spy_dummy:
        res = await client.post(
            "/api/auth/login",
            json={"username": "completely_ghost_nonexistent_user", "password": "SomePassword123!"},
        )
        assert res.status_code == 401
        assert res.json()["error_code"] == "INVALID_CREDENTIALS"

        # Verify spy was called with password and None for hash (triggering dummy constant time check)
        assert spy_dummy.called, "verify_password_and_dummy was NOT called on nonexistent user!"
        args, _ = spy_dummy.call_args
        assert args[1] is None, "Expected password_hash to be None to trigger dummy Argon2id hash!"


@pytest.mark.asyncio
async def test_adversarial_dummy_hash_timing_protection_invoked_on_inactive_user(
    client: AsyncClient, bootstrap_user, db_session: AsyncSession
):
    """STRESS TEST 5.2: Disabled user login MUST ALSO execute dummy hash check to prevent enumeration."""
    user = await bootstrap_user("disabled_account", "Password123!")
    user.status = "disabled"
    await db_session.commit()

    with patch("app.auth.services.verify_password_and_dummy", wraps=verify_password_and_dummy) as spy_dummy:
        res = await client.post(
            "/api/auth/login",
            json={"username": "disabled_account", "password": "Password123!"},
        )
        assert res.status_code == 401
        assert res.json()["error_code"] == "INVALID_CREDENTIALS"

        assert spy_dummy.called, "verify_password_and_dummy was NOT called on disabled user!"
        args, _ = spy_dummy.call_args
        assert args[1] is None, "Expected password_hash to be None to trigger dummy Argon2id hash for disabled user!"


@pytest.mark.asyncio
async def test_adversarial_timing_comparative_benchmark(client: AsyncClient, bootstrap_user):
    """STRESS TEST 5.3: Comparative timing measurement between non-existent user and wrong password.

    Both cases MUST perform Argon2id hashing and neither should return in sub-millisecond trivial time.
    """
    await bootstrap_user("real_existing_user", "RealPassword123!")

    # Warm-up call
    await client.post("/api/auth/login", json={"username": "warmup", "password": "password"})

    # 1. Measure non-existent user login timing
    times_nonexistent = []
    for _ in range(3):
        t0 = time.perf_counter()
        res = await client.post(
            "/api/auth/login",
            json={"username": f"nonexistent_{uuid.uuid4().hex[:8]}", "password": "WrongPassword123!"},
        )
        t1 = time.perf_counter()
        assert res.status_code == 401
        times_nonexistent.append(t1 - t0)

    # 2. Measure existing user with wrong password timing
    times_wrong_pw = []
    for _ in range(3):
        t0 = time.perf_counter()
        res = await client.post(
            "/api/auth/login",
            json={"username": "real_existing_user", "password": "WrongPassword123!"},
        )
        t1 = time.perf_counter()
        assert res.status_code == 401
        times_wrong_pw.append(t1 - t0)

    avg_nonexistent = sum(times_nonexistent) / len(times_nonexistent)
    avg_wrong_pw = sum(times_wrong_pw) / len(times_wrong_pw)

    # Argon2id with memory_cost=65536 and time_cost=2 takes substantial time (> 10ms on modern CPU)
    assert avg_nonexistent > 0.010, f"Non-existent login was suspiciously fast ({avg_nonexistent:.4f}s), dummy hash skipped?"
    assert avg_wrong_pw > 0.010, f"Wrong password login was suspiciously fast ({avg_wrong_pw:.4f}s)?"

    # The ratios must be within an order of magnitude (both bound by Argon2id)
    ratio = avg_nonexistent / avg_wrong_pw
    assert 0.4 <= ratio <= 2.5, f"Timing discrepancy too large between nonexistent ({avg_nonexistent:.4f}s) and wrong pw ({avg_wrong_pw:.4f}s), ratio={ratio:.2f}"
