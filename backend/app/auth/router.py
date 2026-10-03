"""API Routers for Authentication and User Management."""

import uuid

from fastapi import APIRouter, Cookie, Depends, Request, Response, status

from app.auth.dependencies import (
    get_auth_service,
    get_current_user,
    get_user_service,
    require_permission,
)
from app.auth.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    ResetPasswordRequest,
    UserCreateRequest,
    UserSummary,
)
from app.auth.services import AuthService, UserService
from app.config import get_settings
from app.db.models.user import User

settings = get_settings()

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])
users_router = APIRouter(prefix="/users", tags=["User Management"])


@auth_router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> LoginResponse:
    """Authenticates credentials and establishes server-side session with secure cookie."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    correlation_id = request.headers.get("x-correlation-id")

    user, raw_token, csrf_token = await auth_service.login(
        username=payload.username,
        password=payload.password,
        client_ip=client_ip,
        user_agent=user_agent,
        correlation_id=correlation_id,
    )

    # Set Session Cookie
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_token,
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )

    return LoginResponse(
        user=UserSummary.model_validate(user),
        csrf_token=csrf_token,
    )


@auth_router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    session_id: str | None = Cookie(None, alias=settings.SESSION_COOKIE_NAME),
    auth_service: AuthService = Depends(get_auth_service),
) -> MessageResponse:
    """Terminates session and deletes authentication cookie."""
    raw_token = session_id
    if not raw_token:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            raw_token = auth_header[7:].strip()

    client_ip = request.client.host if request.client else None

    if raw_token:
        await auth_service.logout(raw_token=raw_token, client_ip=client_ip)

    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )

    return MessageResponse(success=True, message="Logged out successfully")


@auth_router.get("/me", response_model=UserSummary)
async def get_me(current_user: User = Depends(get_current_user)) -> UserSummary:
    """Returns profile for currently authenticated user."""
    return UserSummary.model_validate(current_user)


@auth_router.post("/change-password", response_model=MessageResponse)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
) -> MessageResponse:
    """Allows authenticated user to change their password."""
    client_ip = request.client.host if request.client else None
    await user_service.change_password(
        user_id=current_user.id,
        old_password=payload.old_password,
        new_password=payload.new_password,
        client_ip=client_ip,
    )
    return MessageResponse(success=True, message="Password updated successfully")


# --- User Management Endpoints ---


@users_router.get("", response_model=list[UserSummary])
async def list_users(
    user_service: UserService = Depends(get_user_service),
    _: User = Depends(require_permission("users:read")),
) -> list[UserSummary]:
    """Lists all user accounts in the system."""
    users = await user_service.get_all_users()
    return [UserSummary.model_validate(u) for u in users]


@users_router.post("", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateRequest,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_permission("users:write")),
) -> UserSummary:
    """Creates a new user account."""
    client_ip = request.client.host if request.client else None
    user = await user_service.create_user(
        username=payload.username,
        password=payload.password,
        must_change_password=payload.must_change_password,
        actor_user_id=current_user.id,
        role_name=payload.role,
        client_ip=client_ip,
    )
    return UserSummary.model_validate(user)


async def _handle_disable(
    user_id: uuid.UUID,
    request: Request,
    current_user: User,
    user_service: UserService,
) -> UserSummary:
    client_ip = request.client.host if request.client else None
    correlation_id = request.headers.get("x-correlation-id")
    disabled = await user_service.disable_user(
        actor_user_id=current_user.id,
        target_user_id=user_id,
        client_ip=client_ip,
        correlation_id=correlation_id,
    )
    return UserSummary.model_validate(disabled)


@users_router.patch("/{user_id}/disable", response_model=UserSummary)
async def disable_user(
    user_id: uuid.UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_permission("users:write")),
) -> UserSummary:
    """Disables user and immediately revokes all their active sessions."""
    return await _handle_disable(user_id, request, current_user, user_service)


@users_router.post("/{user_id}/disable", response_model=UserSummary)
async def disable_user_post(
    user_id: uuid.UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_permission("users:write")),
) -> UserSummary:
    """POST alias for disable endpoint."""
    return await _handle_disable(user_id, request, current_user, user_service)


async def _handle_enable(
    user_id: uuid.UUID,
    request: Request,
    current_user: User,
    user_service: UserService,
) -> UserSummary:
    client_ip = request.client.host if request.client else None
    enabled = await user_service.enable_user(
        actor_user_id=current_user.id,
        target_user_id=user_id,
        client_ip=client_ip,
    )
    return UserSummary.model_validate(enabled)


@users_router.patch("/{user_id}/enable", response_model=UserSummary)
async def enable_user(
    user_id: uuid.UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_permission("users:write")),
) -> UserSummary:
    """Re-enables a disabled user account."""
    return await _handle_enable(user_id, request, current_user, user_service)


@users_router.post("/{user_id}/enable", response_model=UserSummary)
async def enable_user_post(
    user_id: uuid.UUID,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_permission("users:write")),
) -> UserSummary:
    """POST alias for enable endpoint."""
    return await _handle_enable(user_id, request, current_user, user_service)


@users_router.post("/{user_id}/reset-password", response_model=MessageResponse)
async def reset_password(
    user_id: uuid.UUID,
    payload: ResetPasswordRequest,
    request: Request,
    user_service: UserService = Depends(get_user_service),
    current_user: User = Depends(require_permission("users:write")),
) -> MessageResponse:
    """Resets user password and revokes all active sessions."""
    client_ip = request.client.host if request.client else None
    await user_service.reset_password(
        actor_user_id=current_user.id,
        target_user_id=user_id,
        new_password=payload.new_password,
        must_change_password=payload.must_change_password,
        client_ip=client_ip,
    )
    return MessageResponse(
        success=True,
        message="Password reset successfully and all active sessions revoked",
    )
