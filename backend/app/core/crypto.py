"""Envelope Encryption Module for Sensitive Credentials at Rest.

Provides AES-256-GCM authenticated symmetric encryption with HKDF key derivation,
key versioning/rotation support, and seamless integration with the Secret Redaction Engine.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import re
import secrets
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings
from app.core.redaction import register_secret

# Header prefix pattern: enc_v1:..., enc_v2:...
ENCRYPTED_PREFIX_PATTERN = re.compile(r"^enc_([a-zA-Z0-9_-]+):(.+)$")
DEFAULT_KEY_VERSION = "v1"
HKDF_SALT = b"mail-agent-crypto-envelope-salt-v1"
HKDF_INFO = b"mail-agent-envelope-master-key"
NONCE_LENGTH_BYTES = 12  # Standard 96-bit nonce for AES-GCM


class CryptoError(Exception):
    """Base exception for cryptographic operations."""


class DecryptionError(CryptoError):
    """Raised when decryption fails due to corrupted data, invalid tag, or wrong key."""


class KeyNotFoundError(CryptoError):
    """Raised when the specified key version cannot be resolved."""



def derive_256bit_key(raw_key: str | bytes) -> bytes:
    """Derives a secure 32-byte (256-bit) encryption key from arbitrary string or bytes using HKDF-SHA256.

    If raw_key is a valid 32-byte urlsafe-base64 or 64-char hex string, it is decoded directly.
    Otherwise, HKDF-SHA256 stretches/compresses it to exactly 32 bytes.
    """
    raw_key_bytes = raw_key.strip().encode("utf-8") if isinstance(raw_key, str) else raw_key

    # Try raw base64 decode if exactly 32 bytes result
    with contextlib.suppress(Exception):
        decoded_b64 = base64.urlsafe_b64decode(raw_key_bytes)
        if len(decoded_b64) == 32:
            return decoded_b64

    # Try hex decode if exactly 64 hex chars (32 bytes)
    with contextlib.suppress(Exception):
        decoded_hex = binascii.unhexlify(raw_key_bytes)
        if len(decoded_hex) == 32:
            return decoded_hex

    # Fallback: Deterministic HKDF expansion
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=HKDF_SALT,
        info=HKDF_INFO,
    )
    return hkdf.derive(raw_key_bytes)


@lru_cache(maxsize=1)
def _get_master_keys() -> dict[str, bytes]:
    """Retrieves and derives registered master encryption keys keyed by version."""
    settings = get_settings()
    master_key_env = os.environ.get("MASTER_ENCRYPTION_KEY") or getattr(
        settings, "MASTER_ENCRYPTION_KEY", None
    )

    if not master_key_env:
        master_key_env = getattr(settings, "SECRET_KEY", "fallback_dev_master_key_min_32_bytes_long!")

    active_version = getattr(settings, "SECRET_KEY_VERSION", DEFAULT_KEY_VERSION)
    if str(active_version) == "1":
        active_version = "v1"
    derived = derive_256bit_key(master_key_env)
    keys: dict[str, bytes] = {active_version: derived, "1": derived, "v1": derived}

    # Check for legacy or rotated keys
    old_keys_env = os.environ.get("PREVIOUS_MASTER_ENCRYPTION_KEYS")
    if old_keys_env:
        with contextlib.suppress(Exception):
            old_dict = json.loads(old_keys_env)
            for ver, raw in old_dict.items():
                keys[ver] = derive_256bit_key(raw)

    return keys


def is_encrypted(value: str | None) -> bool:
    """Checks whether a given string is an encrypted ciphertext token."""
    if not value or not isinstance(value, str):
        return False
    return bool(ENCRYPTED_PREFIX_PATTERN.match(value.strip()))


def encrypt_secret(plaintext: str | None, key_version: str | None = None) -> str:
    """Encrypts a plaintext secret string using AES-256-GCM authenticated encryption.

    Args:
        plaintext: The sensitive string to encrypt (passwords, tokens, client secrets).
        key_version: Optional key version identifier (defaults to current active version).

    Returns:
        Token string formatted as 'enc_<version>:<base64url_payload>' or empty string if input is empty.

    Guarantees:
        1. Plaintext is automatically registered with DynamicSecretRegistry for zero log leakage.
        2. Idempotent: If plaintext is already an 'enc_...' token, it is returned as-is.
        3. Generates a cryptographically strong unique 96-bit nonce on every call.
    """
    if plaintext is None or plaintext == "":
        return ""

    clean_text = str(plaintext).strip()
    if clean_text == "":
        return ""

    # Prevent double-encryption
    if is_encrypted(clean_text):
        return clean_text

    # 1. Immediately register plaintext into Secret Redaction Engine
    register_secret(clean_text)

    # 2. Resolve encryption key
    keys = _get_master_keys()
    settings = get_settings()
    version = key_version or getattr(settings, "SECRET_KEY_VERSION", DEFAULT_KEY_VERSION)
    if str(version) == "1":
        version = "v1"

    if version not in keys:
        raise KeyNotFoundError(f"Encryption key version '{version}' is not registered.")

    raw_key = keys[version]
    aesgcm = AESGCM(raw_key)

    # 3. Generate 12-byte CSPRNG nonce
    nonce = secrets.token_bytes(NONCE_LENGTH_BYTES)
    aad = version.encode("utf-8")

    # 4. Encrypt with AES-GCM (appends 16-byte auth tag)
    plaintext_bytes = clean_text.encode("utf-8")
    ciphertext_and_tag = aesgcm.encrypt(nonce, plaintext_bytes, aad)

    # 5. Pack nonce + ciphertext_and_tag into URL-safe base64
    payload = base64.urlsafe_b64encode(nonce + ciphertext_and_tag).decode("utf-8")
    return f"enc_{version}:{payload}"


def decrypt_secret(ciphertext: str | None) -> str:
    """Decrypts an encrypted token string back to its plaintext representation.

    Args:
        ciphertext: The encrypted token formatted as 'enc_<version>:<base64url_payload>'.

    Returns:
        The decrypted plaintext string or empty string if input is empty.

    Guarantees:
        1. Plaintext is immediately registered with DynamicSecretRegistry upon decryption.
        2. Cryptographic authentication: Tampering with ciphertext or auth tag raises DecryptionError.
        3. If value is not encrypted, returns value as-is and registers it for safety.
    """
    if ciphertext is None or ciphertext == "":
        return ""

    clean_token = str(ciphertext).strip()
    if clean_token == "":
        return ""

    match = ENCRYPTED_PREFIX_PATTERN.match(clean_token)
    if not match:
        # Graceful handling if unencrypted string is passed: register and return
        register_secret(clean_token)
        return clean_token

    version = match.group(1)
    payload_b64 = match.group(2)

    keys = _get_master_keys()
    if version not in keys:
        raise KeyNotFoundError(f"Decryption key version '{version}' not found.")

    raw_key = keys[version]
    aesgcm = AESGCM(raw_key)

    try:
        raw_payload = base64.urlsafe_b64decode(payload_b64.encode("utf-8"))
    except Exception as exc:
        raise DecryptionError(f"Malformed base64 ciphertext payload: {exc}") from exc

    if len(raw_payload) < (NONCE_LENGTH_BYTES + 16):
        raise DecryptionError("Ciphertext payload is shorter than minimum nonce + tag length.")

    nonce = raw_payload[:NONCE_LENGTH_BYTES]
    ciphertext_and_tag = raw_payload[NONCE_LENGTH_BYTES:]
    aad = version.encode("utf-8")

    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext_and_tag, aad)
    except InvalidTag as exc:
        raise DecryptionError("Ciphertext authentication failed (data corrupted or tampered).") from exc
    except Exception as exc:
        raise DecryptionError(f"Failed to decrypt secret: {exc}") from exc

    decrypted_text = decrypted_bytes.decode("utf-8")

    # Immediately register decrypted plaintext in redaction registry
    register_secret(decrypted_text)
    return decrypted_text


def reencrypt_secret(ciphertext: str, target_version: str) -> str:
    """Rotates a secret's encryption from an older key version to target_version."""
    if not ciphertext or not is_encrypted(ciphertext):
        return ciphertext

    plaintext = decrypt_secret(ciphertext)
    return encrypt_secret(plaintext, key_version=target_version)


def mask_secret(plaintext: str | None, show_suffix_chars: int = 4) -> str:
    """Creates a safe masked representation of a secret for administrative UI display.

    Example: 'shpss_abcdef12345678' -> '••••••••••••5678'
    """
    if not plaintext:
        return ""
    clean = str(plaintext).strip()
    if len(clean) <= show_suffix_chars:
        return "•" * len(clean)
    masked_prefix = "•" * max(8, len(clean) - show_suffix_chars)
    return f"{masked_prefix}{clean[-show_suffix_chars:]}"
