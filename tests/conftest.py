"""Shared test fixtures for elfmem test suite."""

import logging
import logging.handlers
import os

import pytest

from elfmem.adapters.mock import (
    MockEmbeddingService,
    MockLLMService,
    make_mock_embedding,
    make_mock_llm,
)
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import seed_builtin_data
from elfmem.logging_config import configure_logging, set_operation_context

# Suppress logging in tests by default (zero noise)
os.environ.setdefault("ELFMEM_LOG_LEVEL", "CRITICAL")


@pytest.fixture(autouse=True)
def _reset_logging_between_tests():
    """Reset logging to CRITICAL before each test (test isolation).

    This ensures:
    - No log noise in test output
    - Tests don't interfere with each other's logging state
    - Each test starts with a clean logging configuration
    """
    # Reset to CRITICAL (suppresses all logs in tests)
    configure_logging(level="CRITICAL")

    # Clear context variables
    set_operation_context(None, None)

    yield

    # Cleanup after test
    configure_logging(level="CRITICAL")
    set_operation_context(None, None)


@pytest.fixture
def mock_llm() -> MockLLMService:
    """A MockLLMService with sensible defaults for general testing."""
    return make_mock_llm()


@pytest.fixture
def mock_embedding() -> MockEmbeddingService:
    """A MockEmbeddingService with 64-dim vectors, no overrides."""
    return make_mock_embedding()


@pytest.fixture
async def test_engine():
    """An in-memory async SQLite engine with all tables created and seeded.

    Yields the engine; disposes after the test.
    """
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_conn(test_engine):
    """An async connection to the test database within a transaction."""
    async with test_engine.begin() as conn:
        yield conn


@pytest.fixture
def db_path_str(tmp_path):
    """A temporary database file path as a string."""
    return str(tmp_path / "test.db")


@pytest.fixture
def log_capture():
    """Capture logs emitted during test with proper isolation.

    USE WHEN: Test needs to verify logging behavior
    RETURNS: Handler with buffer list of log records

    Example::

        async def test_learn_logs_event(system, log_capture):
            configure_logging(level="INFO")
            await system.learn("test")
            # log_capture.buffer contains emitted records
    """
    # Save initial state
    root_logger = logging.getLogger("elfmem")
    initial_level = root_logger.level
    initial_handlers = root_logger.handlers[:]

    # Create capture handler
    handler = logging.handlers.MemoryHandler(capacity=1000)
    root_logger.addHandler(handler)

    yield handler

    # Restore initial state (cleanup)
    root_logger.removeHandler(handler)
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
    for h in initial_handlers:
        root_logger.addHandler(h)
    root_logger.setLevel(initial_level)

    # Reset env var to CRITICAL for next test
    os.environ["ELFMEM_LOG_LEVEL"] = "CRITICAL"
