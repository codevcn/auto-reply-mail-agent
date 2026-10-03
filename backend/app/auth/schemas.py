"""Pydantic schemas for Authentication and User Management."""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, computed_field


class LoginRequest(BaseModel):
    """Credentials submitted for user login."""

    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)


class UserSummary(BaseModel):
    """Public user profile information."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    status: str
    role: str = "admin"
    must_change_password: bool
    created_at: datetime.datetime
    last_login_at: datetime.datetime | None = None

    @computed_field
    @property
    def is_active(self) -> bool:
        return self.status == "active"


class LoginResponse(BaseModel):
    """Payload returned on successful authentication."""

    user: UserSummary
    csrf_token: str


class UserCreateRequest(BaseModel):
    """Payload for creating a new user account."""

    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    must_change_password: bool = Field(default=False)
    role: str = Field(default="admin")


class ResetPasswordRequest(BaseModel):
    """Payload for administrative password reset."""

    new_password: str = Field(..., min_length=8)
    must_change_password: bool = Field(default=True)


class ChangePasswordRequest(BaseModel):
    """Payload for self-service password modification."""

    old_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)


class ErrorResponse(BaseModel):
    """Standardized error structure."""

    error_code: str
    message: str


class MessageResponse(BaseModel):
    """Standardized success message structure."""

    success: bool
    message: str
