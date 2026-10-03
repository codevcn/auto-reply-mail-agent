"""Unit tests for Envelope Encryption AES-256-GCM and Secret Redaction integration."""

import base64

import pytest
from app.core.crypto import (
    DecryptionError,
    decrypt_secret,
    encrypt_secret,
    is_encrypted,
    mask_secret,
)
from app.core.redaction import get_registered_secrets, redact_text


def test_encrypt_decrypt_roundtrip():
    secret = "shpss_extremely_sensitive_shopify_client_secret_998877"
    ciphertext = encrypt_secret(secret)
    assert ciphertext != secret
    assert is_encrypted(ciphertext)
    assert ciphertext.startswith("enc_v1:")

    decrypted = decrypt_secret(ciphertext)
    assert decrypted == secret


def test_nonce_uniqueness():
    secret = "same_secret_encrypted_multiple_times"
    enc1 = encrypt_secret(secret)
    enc2 = encrypt_secret(secret)

    # Different nonces must produce different ciphertexts
    assert enc1 != enc2
    assert decrypt_secret(enc1) == secret
    assert decrypt_secret(enc2) == secret


def test_tampering_detection():
    secret = "integrity_protected_secret"
    ciphertext = encrypt_secret(secret)
    prefix, b64_payload = ciphertext.split(":", 1)

    raw_payload = bytearray(base64.urlsafe_b64decode(b64_payload))
    # Tamper with the last byte (auth tag)
    raw_payload[-1] ^= 0x01
    tampered_b64 = base64.urlsafe_b64encode(bytes(raw_payload)).decode()
    tampered_token = f"{prefix}:{tampered_b64}"

    with pytest.raises(DecryptionError):
        decrypt_secret(tampered_token)


def test_auto_redaction_integration():
    secret = "super_secret_api_key_44332211"
    assert secret not in get_registered_secrets()

    _ = encrypt_secret(secret)
    assert secret in get_registered_secrets()

    # Verify that logs containing the secret are masked
    log_line = f"Connecting to service with secret={secret}!"
    redacted_line = redact_text(log_line)
    assert secret not in redacted_line
    assert "[REDACTED]" in redacted_line


def test_empty_and_idempotent_behavior():
    assert encrypt_secret(None) == ""
    assert encrypt_secret("") == ""
    assert decrypt_secret(None) == ""
    assert decrypt_secret("") == ""

    # Passing already encrypted string returns as-is
    token = encrypt_secret("some_secret")
    assert encrypt_secret(token) == token


def test_mask_secret():
    secret = "shpss_abcdef12345678"
    masked = mask_secret(secret, show_suffix_chars=4)
    assert masked.endswith("5678")
    assert "•" in masked
    assert "abcdef" not in masked

    assert mask_secret("") == ""
    assert mask_secret(None) == ""
    assert mask_secret("123", show_suffix_chars=4) == "•••"
