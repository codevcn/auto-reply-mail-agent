"""Security primitives: Argon2id password hashing, opaque session token generation, and normalization."""

import contextlib
import hashlib
import secrets

import argon2
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.config import get_settings

settings = get_settings()

# Initialize Argon2id PasswordHasher with RFC 9106 / OWASP compliant parameters
PASSWORD_HASHER = PasswordHasher(
    time_cost=settings.ARGON2_TIME_COST,
    memory_cost=settings.ARGON2_MEMORY_COST,
    parallelism=settings.ARGON2_PARALLELISM,
    hash_len=settings.ARGON2_HASH_LEN,
    salt_len=settings.ARGON2_SALT_LEN,
    type=argon2.Type.ID,
)

# Dummy hash for timing attack mitigation when username does not exist
DUMMY_ARGON2_HASH = PASSWORD_HASHER.hash("dummy_password_constant_for_timing_safety")


def hash_password(password: str) -> str:
    """Hashes a password using Argon2id."""
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verifies a password against an Argon2id hash."""
    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def verify_password_and_dummy(password: str, password_hash: str | None) -> bool:
    """Verifies password with constant-time defense.

    If password_hash is None, executes dummy hash verification to prevent timing side-channel attacks.
    """
    if password_hash is None:
        with contextlib.suppress(Exception):
            PASSWORD_HASHER.verify(DUMMY_ARGON2_HASH, password)
        return False

    try:
        return PASSWORD_HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def normalize_username(username: str) -> str:
    """Normalizes username by stripping whitespace and converting to lowercase."""
    if not username:
        return ""
    return username.strip().lower()


def generate_opaque_session_token() -> str:
    """Generates an opaque session token with 'sess_' prefix and 32 bytes (64 hex chars) of CSPRNG entropy."""
    return f"sess_{secrets.token_hex(32)}"


def hash_token(token: str) -> str:
    """Computes SHA-256 hash of a token for secure database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_csrf_token() -> str:
    """Generates a cryptographically random CSRF token."""
    return secrets.token_urlsafe(32)
