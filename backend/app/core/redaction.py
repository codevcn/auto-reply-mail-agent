"""Secret Redaction Engine.

Provides comprehensive pattern-based and dynamic secret redaction for text,
data structures, URLs, JSON payloads, query parameters, and error tracebacks.
Ensures zero secret leakage in application logs and diagnostics.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from typing import Any

# Standard placeholder for redacted content
REDACTED = "[REDACTED]"
REDACTED_KEY = "[REDACTED_PRIVATE_KEY]"
CIRCULAR_REFERENCE = "[CIRCULAR_REFERENCE]"

# ==============================================================================
# Static Regex Patterns for Known Secret Formats
# ==============================================================================

# 1. Shopify Credentials
# shpss_ (Client Secret), shpat_ (Access Token), shppa_ (Partner Secret), shpca_ (Custom App Secret)
# Supports prefix underscores (e.g. active_shpat_..., var_shpss_...)
SHOPIFY_SECRET_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9])(shpss_|shpat_|shppa_|shpca_)[0-9a-zA-Z]{20,64}\b",
    re.IGNORECASE,
)

# 2. Bearer & Basic Authorization Tokens (supports colon after bearer/basic)
BEARER_TOKEN_PATTERN = re.compile(
    r"\b((?:Bearer|Basic)\s*:?\s+)[A-Za-z0-9\-._~+/]+=*",
    re.IGNORECASE,
)

# 3. URLs with embedded credentials (generic RFC 3986 scheme: postgresql+asyncpg, imaps, redis, etc.)
# Form: proto://username:password@host:port
URL_CREDENTIALS_PATTERN = re.compile(
    r"((?:[a-zA-Z][a-zA-Z0-9+.-]*://)[^:\s/@]+:)([^@\s]+)(@)",
    re.IGNORECASE,
)

# 4. Proxy Colon-Delimited Credential Strings: IP:port:user:pass
PROXY_COLON_PATTERN = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}:[^:\s]+:)([^:\s]+)\b")

# 5. Sensitive Key-Value Pairs in JSON / Dict / Query-like strings
# Sensitive keys to inspect
SENSITIVE_KEY_NAMES = (
    r"password|passwd|pwd|"
    r"client_secret_key|client_secret|clientsecret|"
    r"secret|secret_key|secretkey|"
    r"access_token|accesstoken|refresh_token|"
    r"token|auth_token|authtoken|"
    r"api_key|apikey|"
    r"session_secret|session_token|session|"
    r"encryption_master_key|encryption_key|master_key|"
    r"private_key|privatekey|"
    r"ssh_password|vps_password|"
    r"credentials|credential|google_application_credentials|"
    r"passphrase|smtp_pass|webhook_secret"
)

# JSON format: "key": "value" or 'key': 'value' or key: "value"
JSON_KEY_VALUE_PATTERN = re.compile(
    rf"""(?i)(["']?(?:{SENSITIVE_KEY_NAMES})["']?\s*[:=]\s*)(["'])(?:(?=(\\?))\3.)*?\2""",
)

# Query String / Form format: key=value (supports start of string/line or & or ?)
QUERY_KEY_VALUE_PATTERN = re.compile(
    rf"""(?i)((?:\A|[?&]|^)(?:{SENSITIVE_KEY_NAMES})=)([^&\s]+)""",
    re.MULTILINE,
)

# 6. SSH / Interactive Password prompts
SSH_PASSWORD_PATTERN = re.compile(
    r"(?i)\b(Password:\s*)([^\r\n]+)",
)

# 7. PEM Private Keys (supports RSA, EC, DSA, OPENSSH, ENCRYPTED, ED25519, PGP, and truncated keys)
PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN\s+[A-Z0-9_\- ]*PRIVATE\s+KEY(?:\s+BLOCK)?-----"
    r"(?:"
    r"[\s\S]+?-----END\s+[A-Z0-9_\- ]*PRIVATE\s+KEY(?:\s+BLOCK)?-----"
    r"|"
    r"[\s\S]+$"
    r")",
    re.IGNORECASE,
)

# 8. Session Cookie Headers
SESSION_COOKIE_PATTERN = re.compile(
    r"(?i)\b(session(?:_id)?=)([A-Za-z0-9_\-]+)",
)

# Sensitive dictionary keys set (lowercased)
SENSITIVE_KEYS_SET = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "client_secret",
    "clientsecret",
    "client_secret_key",
    "secret_key",
    "secretkey",
    "token",
    "access_token",
    "accesstoken",
    "refresh_token",
    "refreshtoken",
    "api_key",
    "apikey",
    "auth_token",
    "authtoken",
    "authorization",
    "session",
    "session_token",
    "session_secret",
    "master_key",
    "encryption_master_key",
    "encryption_key",
    "private_key",
    "privatekey",
    "ssh_password",
    "vps_password",
    "credentials",
    "credential",
    "google_application_credentials",
    "passphrase",
    "smtp_pass",
    "webhook_secret",
}


# ==============================================================================
# Dynamic Secret Registry (Thread-Safe)
# ==============================================================================


class DynamicSecretRegistry:
    """Thread-safe registry for dynamically registered secrets at runtime."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def register(self, secret: str | None) -> None:
        """Register a sensitive string to be masked whenever encountered."""
        if not secret:
            return
        secret_clean = secret.strip()
        # Avoid masking trivial strings (e.g. 1-3 chars) to avoid false positives
        if len(secret_clean) < 4:
            return
        with self._lock:
            self._secrets.add(secret_clean)

    def register_many(self, secrets: Iterable[str | None]) -> None:
        """Register multiple sensitive strings."""
        for s in secrets:
            self.register(s)

    def unregister(self, secret: str | None) -> None:
        """Remove a registered secret from masking."""
        if not secret:
            return
        with self._lock:
            self._secrets.discard(secret.strip())

    def clear(self) -> None:
        """Clear all registered dynamic secrets."""
        with self._lock:
            self._secrets.clear()

    def get_secrets(self) -> list[str]:
        """Return a snapshot of registered secrets sorted by length descending."""
        with self._lock:
            # Sort longest first so substrings don't break larger matches
            return sorted(self._secrets, key=len, reverse=True)


# Global singleton instance
_registry = DynamicSecretRegistry()


def register_secret(secret: str | None) -> None:
    """Register a runtime secret in the global registry."""
    _registry.register(secret)


def register_secrets(secrets: Iterable[str | None]) -> None:
    """Register multiple runtime secrets in the global registry."""
    _registry.register_many(secrets)


def clear_registered_secrets() -> None:
    """Clear all dynamically registered secrets."""
    _registry.clear()


def get_registered_secrets() -> list[str]:
    """Get snapshot of currently registered secrets."""
    return _registry.get_secrets()


# ==============================================================================
# Core Redaction Functions
# ==============================================================================


def redact_text(text: str) -> str:
    """Redact all sensitive tokens, credentials, and registered secrets from a string.

    Args:
        text: Input string that may contain secrets.

    Returns:
        String with all detected secrets replaced by [REDACTED].
    """
    if not text:
        return text

    result = text

    # 1. Mask PEM Private Keys first (multiline block)
    result = PEM_PRIVATE_KEY_PATTERN.sub(REDACTED_KEY, result)

    # 2. Mask Dynamic Registered Secrets (exact matches, longest first)
    for dynamic_secret in _registry.get_secrets():
        if dynamic_secret in result:
            result = result.replace(dynamic_secret, REDACTED)

    # 3. Mask Shopify Credentials
    result = SHOPIFY_SECRET_PATTERN.sub(REDACTED, result)

    # 4. Mask Bearer Authorization tokens
    result = BEARER_TOKEN_PATTERN.sub(r"\1" + REDACTED, result)

    # 5. Mask URL credentials (protocol://user:pass@host)
    result = URL_CREDENTIALS_PATTERN.sub(r"\1" + REDACTED + r"\3", result)

    # 6. Mask Proxy IP:Port:User:Pass format
    result = PROXY_COLON_PATTERN.sub(r"\1" + REDACTED, result)

    # 7. Mask JSON key-value secrets
    def _replace_json_secret(match: re.Match[str]) -> str:
        prefix = match.group(1)
        quote = match.group(2)
        return f"{prefix}{quote}{REDACTED}{quote}"

    result = JSON_KEY_VALUE_PATTERN.sub(_replace_json_secret, result)

    # 8. Mask Query param secrets
    result = QUERY_KEY_VALUE_PATTERN.sub(r"\1" + REDACTED, result)

    # 9. Mask SSH Password lines
    result = SSH_PASSWORD_PATTERN.sub(r"\1" + REDACTED, result)

    # 10. Mask Session cookies
    result = SESSION_COOKIE_PATTERN.sub(r"\1" + REDACTED, result)

    return result


def redact_data(
    data: Any,
    visited_ids: set[int] | None = None,
    max_depth: int = 50000,
) -> Any:
    """Recursively redact secrets in nested dictionaries, lists, sets, and tuples.

    Uses an iterative heap stack to eliminate RecursionError even with arbitrarily
    deep nested structures (e.g. 1200+ levels).
    Tracks active ancestor IDs to handle cyclic references gracefully without infinite loops.

    Args:
        data: Arbitrary Python data structure.
        visited_ids: Optional set of ancestor object IDs for cyclic reference detection.
        max_depth: Maximum recursion/nesting depth threshold before stopping expansion.

    Returns:
        Data structure with all secrets redacted.
    """
    if isinstance(data, str):
        return redact_text(data)

    if not isinstance(data, (dict, list, tuple, set)):
        return data

    active_ancestors: set[int] = visited_ids.copy() if visited_ids is not None else set()

    # Determine root container
    if isinstance(data, dict):
        root_dest: Any = {}
        items = list(data.items())
        is_dict = True
    elif isinstance(data, (list, tuple)):
        root_dest = []
        items = list(enumerate(data))
        is_dict = False
    elif isinstance(data, set):
        root_dest = set()
        items = [(None, x) for x in data]
        is_dict = False

    active_ancestors.add(id(data))
    stack: list[list[Any]] = [[data, root_dest, items, 0, is_dict, isinstance(data, tuple)]]

    while stack:
        frame = stack[-1]
        src, dest, items, idx, is_d, is_tup = frame

        if idx >= len(items):
            active_ancestors.remove(id(src))
            stack.pop()
            if stack:
                parent_frame = stack[-1]
                p_dest = parent_frame[1]
                p_items = parent_frame[2]
                p_idx = parent_frame[3] - 1
                k = p_items[p_idx][0]
                final_val = tuple(dest) if is_tup else dest
                if parent_frame[4]:
                    p_dest[k] = final_val
                elif isinstance(p_dest, list):
                    p_dest.append(final_val)
                elif isinstance(p_dest, set):
                    p_dest.add(final_val)
            continue

        frame[3] += 1
        k, v = items[idx]

        # Sensitive dictionary key check
        if is_d:
            k_str = str(k).lower().strip()
            if any(sensitive in k_str for sensitive in SENSITIVE_KEYS_SET):
                dest[k] = REDACTED
                continue

        # Process value v
        if isinstance(v, str):
            processed_v = redact_text(v)
            if is_d:
                dest[k] = processed_v
            elif isinstance(dest, list):
                dest.append(processed_v)
            elif isinstance(dest, set):
                dest.add(processed_v)
        elif isinstance(v, (dict, list, tuple, set)):
            if id(v) in active_ancestors:
                # Cycle detected
                if is_d:
                    dest[k] = CIRCULAR_REFERENCE
                elif isinstance(dest, list):
                    dest.append(CIRCULAR_REFERENCE)
                elif isinstance(dest, set):
                    dest.add(CIRCULAR_REFERENCE)
            elif len(stack) >= max_depth:
                # Max depth threshold reached
                if is_d:
                    dest[k] = "[MAX_DEPTH_EXCEEDED]"
                elif isinstance(dest, list):
                    dest.append("[MAX_DEPTH_EXCEEDED]")
                elif isinstance(dest, set):
                    dest.add("[MAX_DEPTH_EXCEEDED]")
            else:
                if isinstance(v, dict):
                    child_dest: Any = {}
                    child_items = list(v.items())
                    child_is_d = True
                elif isinstance(v, (list, tuple)):
                    child_dest = []
                    child_items = list(enumerate(v))
                    child_is_d = False
                elif isinstance(v, set):
                    child_dest = set()
                    child_items = [(None, x) for x in v]
                    child_is_d = False

                active_ancestors.add(id(v))
                stack.append([v, child_dest, child_items, 0, child_is_d, isinstance(v, tuple)])
        else:
            if is_d:
                dest[k] = v
            elif isinstance(dest, list):
                dest.append(v)
            elif isinstance(dest, set):
                dest.add(v)

    return tuple(root_dest) if isinstance(data, tuple) else root_dest
