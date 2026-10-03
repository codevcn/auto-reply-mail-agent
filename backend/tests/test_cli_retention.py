"""Unit tests for retention-cleanup CLI command (Invariant R-34)."""

from __future__ import annotations

import pytest
from app.cli import main, retention_cleanup_async
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_cli_retention_cleanup_async_dry_run(db_session: AsyncSession):
    """Verifies retention_cleanup_async runs successfully with --dry-run."""
    exit_code = await retention_cleanup_async(
        days=120,
        dry_run=True,
        batch_size=500,
        session=db_session,
    )
    assert exit_code == 0


@pytest.mark.asyncio
async def test_cli_retention_cleanup_enforces_r34(db_session: AsyncSession):
    """Verifies retention_cleanup_async rejects --days > 120 (Invariant R-34)."""
    exit_code_over = await retention_cleanup_async(
        days=121,
        dry_run=True,
        session=db_session,
    )
    assert exit_code_over == 1

    exit_code_zero = await retention_cleanup_async(
        days=0,
        dry_run=True,
        session=db_session,
    )
    assert exit_code_zero == 1


def test_cli_retention_cleanup_parser_help():
    """Verifies CLI argument parsing for retention-cleanup."""
    with pytest.raises(SystemExit) as exc_info:
        main(["retention-cleanup", "--help"])
    assert exc_info.value.code == 0
