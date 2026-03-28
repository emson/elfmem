"""Tests for elfmem logging infrastructure (Phase 1).

Validates:
- Logging disabled by default (zero noise in tests)
- Structured formatters work (JSON, compact, text)
- Context variables propagate correctly (operation_id, session_id)
- Configuration loads and applies correctly
"""

from __future__ import annotations

import json
import logging
import os
from io import StringIO

from elfmem.config import ElfmemConfig, LoggingConfig
from elfmem.logging_config import (
    CompactFormatter,
    StructuredFormatter,
    TextFormatter,
    configure_logging,
    get_operation_context,
    set_operation_context,
)


class TestLoggingDisabledByDefault:
    """Logging should not produce output unless explicitly enabled."""

    def test_logging_critical_by_default(self):
        """CRITICAL is the default level (only errors from other libraries)."""
        cfg = ElfmemConfig()
        assert cfg.logging.level == "CRITICAL"

    def test_environment_default_is_critical(self):
        """ELFMEM_LOG_LEVEL should default to CRITICAL in tests."""
        assert os.getenv("ELFMEM_LOG_LEVEL") == "CRITICAL"

    def test_text_format_default(self):
        """Default format is text (human-readable)."""
        cfg = ElfmemConfig()
        assert cfg.logging.format == "text"

    def test_no_file_by_default(self):
        """Logs go to stderr, not a file."""
        cfg = ElfmemConfig()
        assert cfg.logging.file is None


class TestLoggingConfig:
    """Configuration loading and validation."""

    def test_logging_config_from_yaml(self):
        """Load logging config from YAML."""
        # This would require a test YAML file; for now, test programmatic creation
        cfg = LoggingConfig(
            level="INFO",
            format="json",
            file="/tmp/test.log",
            modules={"elfmem.operations": "DEBUG"},
        )
        assert cfg.level == "INFO"
        assert cfg.format == "json"
        assert cfg.file == "/tmp/test.log"
        assert cfg.modules["elfmem.operations"] == "DEBUG"

    def test_logging_config_defaults(self):
        """All LoggingConfig fields have sensible defaults."""
        cfg = LoggingConfig()
        assert cfg.level == "CRITICAL"
        assert cfg.format == "text"
        assert cfg.file is None
        assert cfg.modules is None
        assert cfg.slow_query_threshold_ms == 100
        assert cfg.lock_contention_threshold_ms == 50
        assert cfg.sample_rate == 1.0

    def test_elfmem_config_includes_logging(self):
        """ElfmemConfig has a logging section."""
        cfg = ElfmemConfig()
        assert hasattr(cfg, "logging")
        assert isinstance(cfg.logging, LoggingConfig)


class TestFormatters:
    """Test the three formatter implementations."""

    @staticmethod
    def _make_log_record(message: str, level: str = "INFO") -> logging.LogRecord:
        """Create a test log record."""
        record = logging.LogRecord(
            name="elfmem.test",
            level=getattr(logging, level),
            pathname="test.py",
            lineno=1,
            msg=message,
            args=(),
            exc_info=None,
        )
        record.event = "test_event"
        record.operation_id = "o-abc123def456"
        record.session_id = "s-xyz789abc012"
        return record

    def test_text_formatter(self):
        """TextFormatter produces readable output with context."""
        record = self._make_log_record("Test message")
        formatter = TextFormatter()
        output = formatter.format(record)

        assert "Test message" in output
        assert "INFO" in output
        assert "elfmem.test" in output
        assert "op_id=o-abc1" in output
        assert "session_id=s-xyz7" in output

    def test_json_formatter(self):
        """StructuredFormatter produces valid JSON with all fields."""
        record = self._make_log_record("Test message")
        formatter = StructuredFormatter()
        output = formatter.format(record)

        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["source"] == "elfmem.test"
        assert data["event"] == "test_event"
        assert data["operation_id"] == "o-abc123def456"
        assert data["session_id"] == "s-xyz789abc012"
        assert data["message"] == "Test message"
        assert "timestamp" in data

    def test_compact_formatter(self):
        """CompactFormatter produces single-line output."""
        record = self._make_log_record("Test message")
        formatter = CompactFormatter()
        output = formatter.format(record)

        assert "\n" not in output
        assert "INFO" in output
        assert "elfmem.test" in output
        assert "event=test_event" in output

    def test_formatters_with_no_context(self):
        """Formatters handle missing context gracefully."""
        record = logging.LogRecord(
            name="elfmem.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Message without context",
            args=(),
            exc_info=None,
        )
        # No operation_id or session_id set

        # Should not raise
        text_output = TextFormatter().format(record)
        json_output = StructuredFormatter().format(record)
        compact_output = CompactFormatter().format(record)

        assert text_output
        assert json_output
        assert compact_output


class TestContextVariables:
    """Context variables for operation tracing."""

    def test_set_and_get_context(self):
        """Context variables can be set and retrieved."""
        set_operation_context("o-test123", "s-sess456")
        op_id, sess_id = get_operation_context()
        assert op_id == "o-test123"
        assert sess_id == "s-sess456"

    def test_context_defaults_to_none(self):
        """Context variables default to None."""
        set_operation_context(None, None)
        op_id, sess_id = get_operation_context()
        assert op_id is None
        assert sess_id is None

    def test_context_propagates_to_log_record(self):
        """When context is set, formatters inject it into log records."""
        from elfmem.logging_config import get_logger

        set_operation_context("o-op123abc", "s-sess789xyz")

        # Create logger with context injection (use get_logger, not logging.getLogger)
        logger = get_logger("elfmem.context_test")
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        formatter = TextFormatter()
        handler.setFormatter(formatter)

        # Clear and set handlers (proper way)
        for h in logger.handlers[:]:
            logger.removeHandler(h)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

        # Log a message
        logger.info("Test message")
        handler.flush()

        # Get the output
        output = stream.getvalue()
        assert "op_id=o-op1" in output, f"Context not found in: {output}"
        assert "session_id=s-ses" in output, f"Context not found in: {output}"


class TestConfigureLogging:
    """Test configure_logging() entry point."""

    def test_configure_logging_sets_level(self):
        """configure_logging() sets the root level."""
        configure_logging(level="DEBUG")
        logger = logging.getLogger("elfmem")
        assert logger.level == logging.DEBUG

    def test_configure_logging_respects_env_var(self):
        """If level is None, reads ELFMEM_LOG_LEVEL."""
        os.environ["ELFMEM_LOG_LEVEL"] = "WARNING"
        configure_logging(level=None)
        logger = logging.getLogger("elfmem")
        assert logger.level == logging.WARNING

    def test_configure_logging_with_json_format(self):
        """configure_logging(format_type="json") sets StructuredFormatter."""
        configure_logging(level="INFO", format_type="json")

        logger = logging.getLogger("elfmem")
        # Capture from the configured handler
        assert len(logger.handlers) > 0, "configure_logging should add handler"

        # Get the actual handler's stream
        handler = logger.handlers[0]
        if hasattr(handler, "stream"):
            stream = handler.stream
        else:
            # Fall back to capturing stderr
            from io import StringIO as SIO
            stream = SIO()
            handler = logging.StreamHandler(stream)
            logger.handlers = [handler]

        logger.info("Test", extra={"test_field": "value"})
        handler.flush()

        output = stream.getvalue()
        assert output, f"No output captured; logger has {len(logger.handlers)} handlers"

        data = json.loads(output.strip())
        assert data["level"] == "INFO"
        assert data["message"] == "Test"

    def test_configure_logging_with_module_overrides(self):
        """configure_logging() applies per-module level overrides."""
        configure_logging(
            level="INFO",
            module_overrides={
                "elfmem.operations.consolidate": "DEBUG",
                "elfmem.adapters": "WARNING",
            },
        )
        assert logging.getLogger("elfmem.operations.consolidate").level == logging.DEBUG
        assert logging.getLogger("elfmem.adapters").level == logging.WARNING


class TestLoggingIntegration:
    """Integration tests with the full system."""

    def test_logging_idempotent(self):
        """Calling configure_logging() twice is safe (idempotent)."""
        configure_logging(level="INFO", format_type="text")
        configure_logging(level="DEBUG", format_type="json")
        # Should not raise or have side effects

        logger = logging.getLogger("elfmem")
        assert logger.level == logging.DEBUG

    def test_logging_disabled_in_tests(self):
        """Tests can reset to CRITICAL to suppress noise."""
        # Reset to CRITICAL (conftest default for test isolation)
        configure_logging(level="CRITICAL")
        logger = logging.getLogger("elfmem")
        # After configure_logging(CRITICAL), level should be CRITICAL
        assert logger.level == logging.CRITICAL
