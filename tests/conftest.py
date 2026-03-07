"""Shared test fixtures for elfmem test suite."""

import pytest

from elfmem.adapters.mock import (
    MockEmbeddingService,
    MockLLMService,
    make_mock_embedding,
    make_mock_llm,
)
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import seed_builtin_data
from elfmem.smart import SmartMemory


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
async def memory(db_path_str):
    """A SmartMemory instance for testing. Auto-opens and closes."""
    mem = await SmartMemory.open(db_path_str)
    yield mem
    await mem.close()
