"""Authentication and User Management Services enforcing core business invariants."""

import asyncio
import datetime
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.security import (
    generate_csrf_token,
    generate_opaque_session_token,
    hash_password,
    hash_token,
    normalize_username,
    verify_password,
    verify_password_and_dummy,
)
from app.db.models.audit import AuditEvent
from app.db.models.role import Role, UserRole
from app.db.models.session import Session
from app.db.models.user import User
from app.db.session import lock_active_users_for_mutation

settings = get_settings()

_user_mutation_lock = asyncio.Lock()


class AuthBusinessError(Exception):
    """Exception raised for business invariant or security constraint violations."""

    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class AuthService:
    """Authentication lifecycle handling: login, logout, and session validation."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def login(
        self,
        username: str,
        password: str,
        client_ip: str | None = None,
        user_agent: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[User, str, str]:
        """Authenticates user with timing-safe checks and issues server-side session."""
        normalized = normalize_username(username)
        stmt = (
            select(User)
            .options(selectinload(User.roles))
            .where(User.normalized_username == normalized)
        )
        result = await self.db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            # Constant-time mitigation against user enumeration
            verify_password_and_dummy(password, None)
            audit = AuditEvent(
                actor_user_id=None,
                event_type="LOGIN_FAILED",
                target_type="user",
                target_id=normalized,
                source_ip=client_ip,
                request_correlation_id=correlation_id,
                safe_change_summary={"username": normalized, "reason": "user_not_found_or_inactive"},
            )
            self.db.add(audit)
            await self.db.commit()
            raise AuthBusinessError(
                code="INVALID_CREDENTIALS",
                message="Invalid username or password",
                status_code=401,
            )

        if not verify_password(password, user.password_hash):
            audit = AuditEvent(
                actor_user_id=user.id,
                event_type="LOGIN_FAILED",
                target_type="user",
                target_id=str(user.id),
                source_ip=client_ip,
                request_correlation_id=correlation_id,
                safe_change_summary={"username": normalized, "reason": "password_mismatch"},
            )
            self.db.add(audit)
            await self.db.commit()
            raise AuthBusinessError(
                code="INVALID_CREDENTIALS",
                message="Invalid username or password",
                status_code=401,
            )

        # Issue opaque session token
        raw_token = generate_opaque_session_token()
        token_hash = hash_token(raw_token)
        csrf_token = generate_csrf_token()
        csrf_hash = hash_token(csrf_token)
        now = datetime.datetime.now(datetime.UTC)
        expires_at = now + datetime.timedelta(seconds=settings.SESSION_MAX_AGE_SECONDS)

        new_session = Session(
            user_id=user.id,
            token_hash=token_hash,
            csrf_secret_hash=csrf_hash,
            created_at=now,
            last_seen_at=now,
            expires_at=expires_at,
            created_ip=client_ip,
            last_seen_ip=client_ip,
            user_agent_summary=(user_agent[:255] if user_agent else None),
        )
        self.db.add(new_session)

        # Update last login
        user.last_login_at = now
        user.updated_at = now

        # Emit audit event
        audit = AuditEvent(
            actor_user_id=user.id,
            event_type="LOGIN_SUCCESS",
            target_type="user",
            target_id=str(user.id),
            source_ip=client_ip,
            request_correlation_id=correlation_id,
            safe_change_summary={"username": user.username},
        )
        self.db.add(audit)

        await self.db.commit()
        await self.db.refresh(user)
        return user, raw_token, csrf_token

    async def logout(
        self,
        raw_token: str,
        current_user_id: uuid.UUID | None = None,
        client_ip: str | None = None,
    ) -> bool:
        """Immediately revokes the session associated with the opaque token."""
        token_hash = hash_token(raw_token)
        now = datetime.datetime.now(datetime.UTC)
        stmt = (
            update(Session)
            .where(Session.token_hash == token_hash)
            .where(Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        result = await self.db.execute(stmt)

        if current_user_id:
            audit = AuditEvent(
                actor_user_id=current_user_id,
                event_type="LOGOUT",
                target_type="user",
                target_id=str(current_user_id),
                source_ip=client_ip,
                safe_change_summary={"action": "logout"},
            )
            self.db.add(audit)

        await self.db.commit()
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)

    async def validate_session(self, raw_token: str) -> User | None:
        """Validates opaque session token from database and returns active user if valid."""
        token_hash = hash_token(raw_token)
        now = datetime.datetime.now(datetime.UTC)

        stmt = (
            select(Session)
            .options(selectinload(Session.user).selectinload(User.roles))
            .where(Session.token_hash == token_hash)
            .where(Session.revoked_at.is_(None))
            .where(Session.expires_at > now)
        )
        result = await self.db.execute(stmt)
        sess = result.scalar_one_or_none()

        if not sess or not sess.user:
            return None

        if not sess.user.is_active:
            # Target user was disabled; revoke session immediately
            sess.revoked_at = now
            await self.db.commit()
            return None

        # Update last seen
        sess.last_seen_at = now
        await self.db.commit()
        return sess.user


class UserService:
    """User account lifecycle, RBAC assignments, and concurrency-locked safeguards."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_all_users(self) -> list[User]:
        """Retrieves all users ordered by creation date."""
        stmt = (
            select(User)
            .options(selectinload(User.roles))
            .order_by(User.created_at.asc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        """Retrieves a single user by ID."""
        stmt = (
            select(User)
            .options(selectinload(User.roles))
            .where(User.id == user_id)
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(
        self,
        username: str,
        password: str,
        must_change_password: bool = False,
        actor_user_id: uuid.UUID | None = None,
        role_name: str = "admin",
        client_ip: str | None = None,
    ) -> User:
        """Creates a new user account with normalized username and hashed password."""
        if len(username.strip()) < 3:
            raise AuthBusinessError(
                code="INVALID_USERNAME",
                message="Username must be at least 3 characters",
                status_code=400,
            )
        if len(password) < 8:
            raise AuthBusinessError(
                code="INVALID_PASSWORD",
                message="Password must be at least 8 characters",
                status_code=400,
            )

        normalized = normalize_username(username)
        existing = await self.db.execute(
            select(User).where(User.normalized_username == normalized)
        )
        if existing.scalar_one_or_none():
            raise AuthBusinessError(
                code="USER_ALREADY_EXISTS",
                message="Username is already taken",
                status_code=409,
            )

        pass_hash = hash_password(password)
        now = datetime.datetime.now(datetime.UTC)
        user = User(
            username=username.strip(),
            normalized_username=normalized,
            password_hash=pass_hash,
            status="active",
            must_change_password=must_change_password,
            created_by=actor_user_id,
            created_at=now,
            updated_at=now,
            row_version=1,
        )
        self.db.add(user)
        await self.db.flush()

        # Assign role
        role_res = await self.db.execute(select(Role).where(Role.name == role_name))
        role = role_res.scalar_one_or_none()
        if role:
            user_role = UserRole(user_id=user.id, role_id=role.id, assigned_at=now)
            self.db.add(user_role)

        audit = AuditEvent(
            actor_user_id=actor_user_id,
            event_type="USER_CREATED",
            target_type="user",
            target_id=str(user.id),
            source_ip=client_ip,
            safe_change_summary={"username": normalized, "role": role_name},
        )
        self.db.add(audit)

        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def disable_user(
        self,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        client_ip: str | None = None,
        correlation_id: str | None = None,
    ) -> User:
        """Safely disables user enforcing anti-lockout invariants and revokes sessions immediately."""
        async with _user_mutation_lock:
            # Invariant 1: Block self-disable (R-28)
            if actor_user_id == target_user_id:
                raise AuthBusinessError(
                    code="SELF_DISABLE_NOT_ALLOWED",
                    message="Self-disable is not permitted (SELF_DISABLE_NOT_ALLOWED)",
                    status_code=400,
                )

            # Invariant 2: Concurrency-locked check on remaining active users
            active_users = await lock_active_users_for_mutation(self.db)
            active_map = {u.id: u for u in active_users}

            target_user = active_map.get(target_user_id)
            if not target_user:
                target_user = await self.get_user_by_id(target_user_id)
                if not target_user:
                    raise AuthBusinessError(
                        code="USER_NOT_FOUND",
                        message="Target user does not exist",
                        status_code=404,
                    )
                if target_user.status == "disabled":
                    raise AuthBusinessError(
                        code="USER_ALREADY_INACTIVE",
                        message="User is already inactive",
                        status_code=400,
                    )

            if len(active_users) <= 1 and target_user_id in active_map:
                raise AuthBusinessError(
                    code="LAST_ACTIVE_USER_REQUIRED",
                    message="Cannot disable the last active user in the system (LAST_ACTIVE_USER_REQUIRED)",
                    status_code=400,
                )

            now = datetime.datetime.now(datetime.UTC)
            target_user.status = "disabled"
            target_user.row_version += 1
            target_user.updated_at = now

            # Invariant 3: Immediately invalidate all active sessions
            revoke_stmt = (
                update(Session)
                .where(Session.user_id == target_user_id)
                .where(Session.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await self.db.execute(revoke_stmt)

            # Emit audit log
            audit = AuditEvent(
                actor_user_id=actor_user_id,
                event_type="USER_DISABLED",
                target_type="user",
                target_id=str(target_user_id),
                source_ip=client_ip,
                request_correlation_id=correlation_id,
                safe_change_summary={
                    "username": target_user.username,
                    "status": "disabled",
                    "sessions_revoked": True,
                },
            )
            self.db.add(audit)

            await self.db.commit()
            await self.db.refresh(target_user)
            return target_user

    async def enable_user(
        self,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        client_ip: str | None = None,
    ) -> User:
        """Re-enables a disabled user account."""
        target_user = await self.get_user_by_id(target_user_id)
        if not target_user:
            raise AuthBusinessError(
                code="USER_NOT_FOUND",
                message="Target user does not exist",
                status_code=404,
            )
        if target_user.status == "active":
            raise AuthBusinessError(
                code="USER_ALREADY_ACTIVE",
                message="User is already active",
                status_code=400,
            )

        now = datetime.datetime.now(datetime.UTC)
        target_user.status = "active"
        target_user.row_version += 1
        target_user.updated_at = now

        audit = AuditEvent(
            actor_user_id=actor_user_id,
            event_type="USER_ENABLED",
            target_type="user",
            target_id=str(target_user_id),
            source_ip=client_ip,
            safe_change_summary={"username": target_user.username, "status": "active"},
        )
        self.db.add(audit)

        await self.db.commit()
        await self.db.refresh(target_user)
        return target_user

    async def reset_password(
        self,
        actor_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        new_password: str,
        must_change_password: bool = True,
        client_ip: str | None = None,
    ) -> User:
        """Resets user password and immediately revokes all current sessions."""
        if len(new_password) < 8:
            raise AuthBusinessError(
                code="INVALID_PASSWORD",
                message="New password must be at least 8 characters",
                status_code=400,
            )

        target_user = await self.get_user_by_id(target_user_id)
        if not target_user:
            raise AuthBusinessError(
                code="USER_NOT_FOUND",
                message="Target user does not exist",
                status_code=404,
            )

        now = datetime.datetime.now(datetime.UTC)
        target_user.password_hash = hash_password(new_password)
        target_user.password_changed_at = now
        target_user.must_change_password = must_change_password
        target_user.row_version += 1
        target_user.updated_at = now

        # Revoke all active sessions
        revoke_stmt = (
            update(Session)
            .where(Session.user_id == target_user_id)
            .where(Session.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        await self.db.execute(revoke_stmt)

        audit = AuditEvent(
            actor_user_id=actor_user_id,
            event_type="PASSWORD_RESET",
            target_type="user",
            target_id=str(target_user_id),
            source_ip=client_ip,
            safe_change_summary={
                "username": target_user.username,
                "sessions_revoked": True,
                "must_change_password": must_change_password,
            },
        )
        self.db.add(audit)

        await self.db.commit()
        await self.db.refresh(target_user)
        return target_user

    async def change_password(
        self,
        user_id: uuid.UUID,
        old_password: str,
        new_password: str,
        client_ip: str | None = None,
    ) -> User:
        """Allows active user to update their own password."""
        if len(new_password) < 8:
            raise AuthBusinessError(
                code="INVALID_PASSWORD",
                message="New password must be at least 8 characters",
                status_code=400,
            )

        user = await self.get_user_by_id(user_id)
        if not user:
            raise AuthBusinessError(
                code="USER_NOT_FOUND",
                message="User not found",
                status_code=404,
            )

        if not verify_password(old_password, user.password_hash):
            raise AuthBusinessError(
                code="INVALID_CREDENTIALS",
                message="Current password does not match",
                status_code=400,
            )

        now = datetime.datetime.now(datetime.UTC)
        user.password_hash = hash_password(new_password)
        user.password_changed_at = now
        user.must_change_password = False
        user.row_version += 1
        user.updated_at = now

        audit = AuditEvent(
            actor_user_id=user_id,
            event_type="PASSWORD_CHANGED",
            target_type="user",
            target_id=str(user_id),
            source_ip=client_ip,
            safe_change_summary={"username": user.username},
        )
        self.db.add(audit)

        await self.db.commit()
        await self.db.refresh(user)
        return user
