"""FastAPI dependencies for Authentication and Authorization."""

from collections.abc import Callable
from typing import Any

from fastapi import Cookie, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.services import AuthBusinessError, AuthService, UserService
from app.config import get_settings
from app.db.models.user import User
from app.db.session import get_db

settings = get_settings()


async def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    """Dependency for AuthService."""
    return AuthService(db)


async def get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    """Dependency for UserService."""
    return UserService(db)


async def get_current_user(
    request: Request,
    session_id: str | None = Cookie(None, alias=settings.SESSION_COOKIE_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> User:
    """Extracts and validates active user from HTTP session cookie."""
    # Check cookie or Authorization header fallback (sess_ token)
    raw_token = session_id
    if not raw_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            bearer_val = auth_header[7:].strip()
            if bearer_val.startswith("sess_"):
                raw_token = bearer_val

    if not raw_token:
        raise AuthBusinessError(
            code="UNAUTHENTICATED",
            message="Missing session cookie",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user = await auth_service.validate_session(raw_token)
    if not user:
        raise AuthBusinessError(
            code="UNAUTHENTICATED",
            message="Session is invalid, expired or revoked",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    return user


def require_permission(permission_name: str) -> Callable[..., Any]:
    """Dependency checking user permission (RBAC-ready).

    In Phase 1, all active users possess the admin role with full system privileges.
    """

    async def permission_checker(current_user: User = Depends(get_current_user)) -> User:
        return current_user

    return permission_checker
