"""Administrative CLI for Mail Agent application.

Usage:
    python -m backend.app.cli bootstrap-admin --username admin --password MyPassword123!
    python -m app.cli bootstrap-admin -u admin -p MyPassword123! --force
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, normalize_username
from app.db.models.audit import AuditEvent
from app.db.models.role import Role, UserRole
from app.db.models.session import Session
from app.db.models.user import User
from app.db.session import AsyncSessionLocal


async def _run_bootstrap(
    session: AsyncSession,
    username: str,
    password: str,
    force: bool,
    must_change_password: bool,
) -> int:
    normalized = normalize_username(username)
    stmt = select(User).where(User.normalized_username == normalized)
    result = await session.execute(stmt)
    existing_user = result.scalar_one_or_none()

    hashed = hash_password(password)

    if existing_user:
        if not force:
            print(
                f"Error: User '{username}' already exists. Use --force to reset password.",
                file=sys.stderr,
            )
            return 1

        # Update password and activate user
        existing_user.password_hash = hashed
        existing_user.status = "active"
        existing_user.must_change_password = must_change_password
        existing_user.row_version += 1

        # Revoke all existing sessions
        await session.execute(
            update(Session)
            .where(Session.user_id == existing_user.id)
            .where(Session.revoked_at.is_(None))
            .values(revoked_at=existing_user.updated_at)
        )

        audit = AuditEvent(
            actor_user_id=None,
            event_type="USER_BOOTSTRAP_RESET",
            target_type="user",
            target_id=str(existing_user.id),
            safe_change_summary={"username": normalized, "action": "bootstrap_force_reset"},
        )
        session.add(audit)
        await session.commit()
        print(f"Admin user '{username}' successfully updated and activated.")
        return 0

    # Create new user
    new_user = User(
        username=username.strip(),
        normalized_username=normalized,
        password_hash=hashed,
        status="active",
        must_change_password=must_change_password,
        created_by=None,
        row_version=1,
    )
    session.add(new_user)
    await session.flush()

    # Find admin role
    role_res = await session.execute(select(Role).where(Role.name == "admin"))
    admin_role = role_res.scalar_one_or_none()
    if admin_role:
        session.add(UserRole(user_id=new_user.id, role_id=admin_role.id))

    audit = AuditEvent(
        actor_user_id=None,
        event_type="USER_BOOTSTRAPPED",
        target_type="user",
        target_id=str(new_user.id),
        safe_change_summary={"username": normalized, "action": "bootstrap_create"},
    )
    session.add(audit)
    await session.commit()
    print(f"Admin user '{username}' successfully bootstrapped.")
    return 0


async def bootstrap_admin_async(
    username: str,
    password: str,
    force: bool = False,
    must_change_password: bool = False,
    session: AsyncSession | None = None,
) -> int:
    """Creates initial administrator account or resets existing account if force is set."""
    normalized = normalize_username(username)
    if len(normalized) < 3:
        print(f"Error: Username '{username}' must be at least 3 characters.", file=sys.stderr)
        return 1
    if len(password) < 8:
        print("Error: Password must be at least 8 characters.", file=sys.stderr)
        return 1

    if session is not None:
        return await _run_bootstrap(session, username, password, force, must_change_password)

    async with AsyncSessionLocal() as local_session:
        return await _run_bootstrap(local_session, username, password, force, must_change_password)


async def retention_cleanup_async(
    days: int = 120,
    dry_run: bool = False,
    batch_size: int = 500,
    session: AsyncSession | None = None,
) -> int:
    """Executes data retention cleanup via CLI (Invariant R-34)."""
    from app.retention.service import RetentionService

    if days > 120:
        print("Error: Invariant R-34: --days cannot exceed 120.", file=sys.stderr)
        return 1
    if days < 1:
        print("Error: --days must be at least 1.", file=sys.stderr)
        return 1

    mode_label = "[DRY-RUN]" if dry_run else "[PRODUCTION RUN]"
    print(f"Starting {mode_label} Retention Cleanup (Days: {days}, Batch: {batch_size})...")

    async def _execute(db: AsyncSession) -> int:
        report = await RetentionService.run_cleanup_job(
            db=db,
            retention_days=days,
            dry_run=dry_run,
            batch_size=batch_size,
            actor_user_id=None,
        )
        print("\n--- RETENTION CLEANUP REPORT ---")
        print(f"Status:        {report.status}")
        print(f"Mode:          {'Dry-Run (Simulated)' if report.dry_run else 'Live Deletion'}")
        print(f"Cutoff Date:   {report.cutoff_date.isoformat()}")
        print(f"Duration:      {report.duration_ms:.2f} ms")
        print("\nDeleted / Affected Counts:")
        for table, count in report.deleted_counts.model_dump().items():
            print(f"  - {table:<28}: {count}")
        print(f"Total Affected Records: {report.total_records_affected}")
        return 0

    if session:
        return await _execute(session)
    async with AsyncSessionLocal() as local_session:
        return await _execute(local_session)


def main(argv: list[str] | None = None) -> int:
    """CLI Entrypoint parser."""
    parser = argparse.ArgumentParser(description="Mail Agent Admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # bootstrap-admin command
    bootstrap_parser = subparsers.add_parser("bootstrap-admin", help="Bootstrap initial admin account")
    bootstrap_parser.add_argument("-u", "--username", required=True, help="Administrator username")
    bootstrap_parser.add_argument("-p", "--password", help="Administrator password (prompted if omitted)")
    bootstrap_parser.add_argument("-f", "--force", action="store_true", help="Force update if user already exists")
    bootstrap_parser.add_argument("--must-change-password", action="store_true", help="Require password change on next login")

    # retention-cleanup command
    retention_parser = subparsers.add_parser("retention-cleanup", help="Run data retention auto-cleanup job (R-34)")
    retention_parser.add_argument("--days", type=int, default=120, help="Retention period in days (max 120). Defaults to 120.")
    retention_parser.add_argument("--dry-run", action="store_true", help="Simulate cleanup and print counts without deleting records.")
    retention_parser.add_argument("--batch-size", type=int, default=500, help="Number of records to delete per batch (default 500).")

    args = parser.parse_args(argv)

    if args.command == "bootstrap-admin":
        password = args.password
        if not password:
            try:
                password = getpass.getpass("Enter administrator password: ")
                confirm = getpass.getpass("Confirm administrator password: ")
                if password != confirm:
                    print("Error: Passwords do not match.", file=sys.stderr)
                    return 1
            except (KeyboardInterrupt, EOFError):
                print("\nOperation cancelled.", file=sys.stderr)
                return 1

        return asyncio.run(
            bootstrap_admin_async(
                username=args.username,
                password=password,
                force=args.force,
                must_change_password=args.must_change_password,
            )
        )

    if args.command == "retention-cleanup":
        return asyncio.run(
            retention_cleanup_async(
                days=args.days,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
