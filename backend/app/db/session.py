"""Database engine and session management supporting PostgreSQL and SQLite."""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool, StaticPool

from app.config import get_settings

settings = get_settings()


def create_engine_for_url(db_url: str, echo: bool = False) -> AsyncEngine:
    """Creates properly configured AsyncEngine based on URL dialect."""
    if db_url.startswith("sqlite"):
        is_memory = ":memory:" in db_url
        engine = create_async_engine(
            db_url,
            poolclass=StaticPool if is_memory else NullPool,
            connect_args={"check_same_thread": False},
            echo=echo,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine

    # PostgreSQL configuration
    return create_async_engine(
        db_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        echo=echo,
    )


engine = create_engine_for_url(settings.DATABASE_URL, echo=settings.DB_ECHO)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding an async database session with auto-rollback on error."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def lock_active_users_for_mutation(session: AsyncSession) -> list[Any]:
    """Acquires an exclusive lock on all currently active users.

    On PostgreSQL: executes pg_advisory_xact_lock and SELECT ... FOR UPDATE.
    On SQLite: locks database transaction ensuring serialized execution.
    """
    from app.db.models.user import User

    bind = session.get_bind()
    dialect_name = bind.dialect.name

    if dialect_name == "postgresql":
        # Acquire advisory transaction lock for mutation serialization
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('user_status_mutation_lock'))")
        )
        # Row lock all active users
        stmt = (
            select(User)
            .where(User.status == "active")
            .with_for_update()
        )
    else:
        # SQLite: standard select, serialized via transaction isolation
        stmt = select(User).where(User.status == "active")

    result = await session.execute(stmt)
    return list(result.scalars().all())
