"""Secret Redaction Logging Engine.

Configures structured logging and console logging with automatic
redaction of all sensitive tokens, credentials, and error tracebacks.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from app.core.redaction import (
    redact_data,
    redact_text,
    register_secrets,
)

# ContextVar for distributed correlation ID tracing
correlation_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def set_correlation_id(correlation_id: str | None) -> None:
    """Set correlation ID for current async/execution context."""
    correlation_id_ctx.set(correlation_id)


def get_correlation_id() -> str | None:
    """Retrieve correlation ID for current execution context."""
    return correlation_id_ctx.get()


class RedactingFilter(logging.Filter):
    """Logging filter that redacts sensitive strings from all log records.

    Handles record.msg, record.args, exception information, and stack traces.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # 1. Attach current correlation ID if present
            curr_corr_id = get_correlation_id()
            if curr_corr_id and not hasattr(record, "correlation_id"):
                record.correlation_id = curr_corr_id

            # 2. Redact record message if it's a string
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg)
            elif isinstance(record.msg, (dict, list)):
                record.msg = redact_data(record.msg)

            # 3. Redact record arguments (tuple or dict)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact_data(record.args)
                elif isinstance(record.args, tuple):
                    record.args = tuple(redact_data(arg) for arg in record.args)
                elif isinstance(record.args, list):
                    record.args = [redact_data(arg) for arg in record.args]

            # 4. Redact exception info if present
            if record.exc_info and record.exc_info[1] is not None:
                # If exc_text hasn't been formatted yet, format and redact
                if not record.exc_text:
                    formatted_tb = "".join(traceback.format_exception(*record.exc_info))
                    record.exc_text = redact_text(formatted_tb)
                else:
                    record.exc_text = redact_text(record.exc_text)

            # 5. Redact stack info if present
            stack_info = getattr(record, "stack_info", None)
            if isinstance(stack_info, str):
                record.stack_info = redact_text(stack_info)
        except Exception:  # noqa: S110
            # Fallback fail-safe: Never allow logging filters to crash caller thread
            pass

        return True


class RedactingTextFormatter(logging.Formatter):
    """Standard console formatter with guaranteed redaction pass."""

    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        return redact_text(s)


class RedactingJsonFormatter(logging.Formatter):
    """Structured JSON formatter with complete secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat()

        log_entry: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }

        # Add correlation ID if present
        corr_id = getattr(record, "correlation_id", None) or get_correlation_id()
        if corr_id:
            log_entry["correlation_id"] = corr_id

        # Add code location
        log_entry["source"] = f"{record.filename}:{record.lineno}"

        # Handle exceptions
        if record.exc_info and record.exc_info[1] is not None:
            if record.exc_text:
                log_entry["exception"] = redact_text(record.exc_text)
            else:
                formatted_tb = "".join(traceback.format_exception(*record.exc_info))
                log_entry["exception"] = redact_text(formatted_tb)

        # Redact entire payload structure
        cleaned_entry = redact_data(log_entry)
        return json.dumps(cleaned_entry, ensure_ascii=False)


def setup_logging(
    level: str = "INFO",
    json_format: bool = False,
    extra_secrets: list[str] | None = None,
) -> logging.Logger:
    """Initialize root logging with secret redaction and proper format.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_format: Whether to format logs as structured JSON.
        extra_secrets: Optional list of dynamic secrets to register immediately.

    Returns:
        Configured root logger.
    """
    if extra_secrets:
        register_secrets(extra_secrets)

    root_logger = logging.getLogger()
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger.setLevel(log_level)

    # Clear existing handlers to prevent duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    # Setup stream handler
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(log_level)

    # Add redacting filter
    redacting_filter = RedactingFilter()
    stream_handler.addFilter(redacting_filter)

    # Choose formatter
    if json_format:
        formatter: logging.Formatter = RedactingJsonFormatter()
    else:
        fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
        formatter = RedactingTextFormatter(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S")

    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Obtain a named logger that inherits redacting root configuration."""
    return logging.getLogger(name)
