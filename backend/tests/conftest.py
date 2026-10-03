"""Pytest configuration and shared fixtures for backend tests."""

import uuid
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from app.core.redaction import clear_registered_secrets
from app.core.security import hash_password
from app.db.base import Base
from app.db.models.role import Permission, Role, RolePermission, UserRole
from app.db.models.user import User
from app.db.session import get_db
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Core permissions list for test seeding
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


@pytest.fixture(autouse=True)
def reset_secret_registry():
    """Ensure dynamic secret registry is clean before and after every test."""
    clear_registered_secrets()
    yield
    clear_registered_secrets()


@pytest_asyncio.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """In-memory SQLite async engine for isolated test execution."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed admin role and core permissions
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        admin_role = Role(id=uuid.uuid4(), name="admin", description="Full system administrator")
        session.add(admin_role)
        await session.flush()

        for code, desc in CORE_PERMISSIONS:
            perm = Permission(id=uuid.uuid4(), code=code, description=desc)
            session.add(perm)
            await session.flush()
            session.add(RolePermission(role_id=admin_role.id, permission_id=perm.id))

        await session.commit()

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """Yields fresh async DB session for each test case."""
    session_factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Async test client with get_db overridden to use test session."""
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def bootstrap_user(db_session: AsyncSession):
    """Helper fixture to create test users directly in DB."""
    async def _create(username: str = "admin", password: str = "SecretPassword123!", role: str = "admin") -> User:
        user = User(
            id=uuid.uuid4(),
            username=username,
            normalized_username=username.strip().lower(),
            password_hash=hash_password(password),
            status="active",
            row_version=1,
        )
        db_session.add(user)
        await db_session.flush()

        role_res = await db_session.execute(select(Role).where(Role.name == role))
        db_role = role_res.scalar_one_or_none()
        if db_role:
            user_role = UserRole(user_id=user.id, role_id=db_role.id)
            db_session.add(user_role)

        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _create
