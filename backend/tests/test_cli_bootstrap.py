"""CLI Admin Bootstrap Tests."""

import pytest
from app.cli import bootstrap_admin_async
from app.core.security import verify_password
from app.db.models.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_cli_bootstrap_creates_admin_user(db_session: AsyncSession):
    """Verifies bootstrap_admin_async creates a new admin user in clean database."""
    ret = await bootstrap_admin_async(
        username="cli_admin",
        password="CliPassword123!",
        force=False,
        session=db_session,
    )
    assert ret == 0

    # Verify user exists in DB
    user = (
        await db_session.execute(
            select(User).where(User.normalized_username == "cli_admin")
        )
    ).scalar_one_or_none()
    assert user is not None
    assert user.status == "active"
    assert verify_password("CliPassword123!", user.password_hash)


@pytest.mark.asyncio
async def test_cli_bootstrap_fails_without_force_when_user_exists(db_session: AsyncSession):
    """Verifies bootstrap_admin_async rejects duplicate user without --force."""
    await bootstrap_admin_async("cli_unique", "InitialPass123!", force=False, session=db_session)

    # Attempt again without force
    ret = await bootstrap_admin_async("cli_unique", "AnotherPass123!", force=False, session=db_session)
    assert ret == 1


@pytest.mark.asyncio
async def test_cli_bootstrap_succeeds_with_force_when_user_exists(db_session: AsyncSession):
    """Verifies bootstrap_admin_async updates password when --force is supplied."""
    await bootstrap_admin_async("cli_force_user", "InitialPass123!", force=False, session=db_session)

    # Re-bootstrap with force
    ret = await bootstrap_admin_async("cli_force_user", "NewForcedPass456!", force=True, session=db_session)
    assert ret == 0

    user = (
        await db_session.execute(
            select(User).where(User.normalized_username == "cli_force_user")
        )
    ).scalar_one_or_none()
    assert user is not None
    assert verify_password("NewForcedPass456!", user.password_hash)
