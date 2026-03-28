"""Structured logging infrastructure for elfmem.

USE WHEN: Logging is enabled via ELFMEM_LOG_LEVEL or config
DON'T USE WHEN: Testing without explicit log assertions
COST: Zero overhead when disabled (logging.CRITICAL level)
RETURNS: Configured logger; context vars for operation_id, session_id
NEXT: Call configure_logging() once at system init; use contextvars in operations

Design: Minimal-by-default, structured JSON events, composable formatters.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Literal

# Context variables for operation tracing (thread-safe + async-safe)
operation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "operation_id", default=None
)
session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "session_id", default=None
)


class StructuredFormatter(logging.Formatter):
    """Emit structured JSON events (one JSON object per log line).

    USE WHEN: format="json"
    RETURNS: JSON string with timestamp, level, source, event, **kwargs
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a single JSON object."""
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "source": record.name,
        }

        # Add custom fields from record (via logger.info(..., extra={...}))
        if hasattr(record, "event"):
            event["event"] = record.event
        if hasattr(record, "operation_id") and record.operation_id:
            event["operation_id"] = record.operation_id
        if hasattr(record, "session_id") and record.session_id:
            event["session_id"] = record.session_id

        # Add any other extra fields
        excluded_keys = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs", "message",
            "pathname", "process", "processName", "relativeCreated", "thread",
            "threadName", "exc_info", "exc_text", "stack_info", "event",
            "operation_id", "session_id",
        }
        for key, value in record.__dict__.items():
            if key not in excluded_keys and not key.startswith("_"):
                event[key] = value

        # Add message if present
        if record.getMessage():
            event["message"] = record.getMessage()

        return json.dumps(event)


class CompactFormatter(logging.Formatter):
    """Emit compact one-line logs (no newlines, minimal overhead).

    USE WHEN: format="compact"
    RETURNS: "TIMESTAMP [LEVEL] module: event=value key=value..."
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a compact single-line string."""
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        base = f"{timestamp} [{record.levelname}] {record.name}"

        if hasattr(record, "event"):
            base += f": event={record.event}"

        # Add key=value for extra fields
        excluded_keys = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs", "message",
            "pathname", "process", "processName", "relativeCreated", "thread",
            "threadName", "exc_info", "exc_text", "stack_info", "event",
            "operation_id", "session_id",
        }
        for key, value in record.__dict__.items():
            if (key not in excluded_keys and not key.startswith("_") and
                    isinstance(value, (str, int, float))):
                base += f" {key}={value}"

        return base


class TextFormatter(logging.Formatter):
    """Emit human-readable logs with context (readable + parseable).

    USE WHEN: format="text" (default)
    RETURNS: "TIMESTAMP [LEVEL] module: message [op_id:xxx session_id:yyy]"
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as human-readable text."""
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds")
        base = f"{timestamp} [{record.levelname}] {record.name}: {record.getMessage()}"

        # Append context (operation_id, session_id) if present
        ctx_parts = []
        if hasattr(record, "operation_id") and record.operation_id:
            ctx_parts.append(f"op_id={record.operation_id[:8]}")
        if hasattr(record, "session_id") and record.session_id:
            ctx_parts.append(f"session_id={record.session_id[:8]}")
        if ctx_parts:
            base += f" [{' '.join(ctx_parts)}]"

        return base


def configure_logging(
    level: str | None = None,
    format_type: Literal["text", "json", "compact"] | None = None,
    file_path: str | None = None,
    module_overrides: dict[str, str] | None = None,
) -> logging.Logger:
    """Configure elfmem logging (one-time initialization).

    USE WHEN: System startup (called in MemorySystem.from_config)
    DON'T USE WHEN: Already configured; call once
    COST: Sets up handlers and formatters (~10ms)
    RETURNS: Configured root elfmem logger
    NEXT: Use logging.getLogger("elfmem.modulename") to get per-module loggers

    Args:
        level: Root log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
               If None, reads ELFMEM_LOG_LEVEL env var or defaults to CRITICAL.
        format_type: Log format (text, json, compact).
                     If None, reads ELFMEM_LOG_FORMAT or defaults to "text".
        file_path: Write logs to file (instead of stderr).
                   If None, logs to stderr.
        module_overrides: Per-module level overrides, e.g.
                         {"elfmem.operations.consolidate": "DEBUG"}

    Example::

        configure_logging(level="INFO", format_type="json")
        logger = logging.getLogger("elfmem.operations")
        logger.info("Started", extra={"operation_id": "o-abc123", "block_count": 5})
    """
    # Resolve level
    if level is None:
        level_str = os.getenv("ELFMEM_LOG_LEVEL", "CRITICAL").upper()
        level = level_str
    else:
        level = level.upper()

    # Resolve format
    if format_type is None:
        format_str = os.getenv("ELFMEM_LOG_FORMAT", "text").lower()
        format_type = format_str  # type: ignore[assignment]

    # Choose formatter
    formatter: logging.Formatter
    if format_type == "json":
        formatter = StructuredFormatter()
    elif format_type == "compact":
        formatter = CompactFormatter()
    else:
        formatter = TextFormatter()

    # Configure root elfmem logger
    root_logger = logging.getLogger("elfmem")
    root_logger.setLevel(level)
    root_logger.propagate = False

    # Remove any existing handlers (idempotent)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create handler (file or stderr)
    if file_path:
        handler = logging.FileHandler(file_path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Apply per-module level overrides
    if module_overrides:
        for module_name, module_level in module_overrides.items():
            logging.getLogger(module_name).setLevel(module_level.upper())

    return root_logger


def set_operation_context(operation_id: str | None, session_id: str | None) -> None:
    """Set context variables for the current operation (async-safe).

    USE WHEN: Starting an operation (consolidate, learn, recall, etc.)
    COST: Microseconds (context var assignment)
    RETURNS: None (modifies context)
    NEXT: All logs emitted in this context will include operation_id, session_id

    Args:
        operation_id: Unique ID for this operation (e.g., "o-abc123def456")
        session_id: Unique ID for the session (e.g., "s-xyz789abc012")

    Example::

        set_operation_context("o-con123def456", "s-sess789abc")
        logger.info("Processing", extra={"event": "started", "block_count": 5})
        # Emitted log will include operation_id and session_id
    """
    operation_id_var.set(operation_id)
    session_id_var.set(session_id)


def get_operation_context() -> tuple[str | None, str | None]:
    """Get current context variables (for log record enrichment).

    USE WHEN: Enriching log records with context
    RETURNS: (operation_id, session_id) tuple
    """
    return operation_id_var.get(), session_id_var.get()


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a module with automatic context enrichment.

    USE WHEN: Logging from any elfmem module
    RETURNS: Logger that auto-injects operation_id, session_id into records
    NEXT: Call logger.info(), logger.debug(), etc. with extra={}

    Args:
        name: Module name (e.g., "elfmem.operations.consolidate")

    Example::

        logger = get_logger(__name__)
        logger.info("Block promoted", extra={"block_id": "abc123de", "confidence": 0.92})
    """
    logger = logging.getLogger(name)

    # Add automatic context injection via filter
    class ContextInjector(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            op_id, sess_id = get_operation_context()
            record.operation_id = op_id
            record.session_id = sess_id
            return True

    # Add filter if not already added (idempotent)
    if not any(isinstance(f, ContextInjector) for f in logger.filters):
        logger.addFilter(ContextInjector())

    return logger
