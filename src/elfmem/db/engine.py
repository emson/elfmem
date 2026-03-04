"""Async SQLAlchemy engine factory for elfmem."""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from elfmem.db.models import metadata

_PRODUCTION_PRAGMAS = [
    "PRAGMA journal_mode=WAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA cache_size=-32000",
    "PRAGMA temp_store=MEMORY",
]

_TEST_PRAGMAS = [
    "PRAGMA foreign_keys=ON",
]


def _register_pragma_listener(engine: AsyncEngine, pragmas: list[str]) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def set_pragmas(dbapi_conn: Any, connection_record: object) -> None:
        cursor = dbapi_conn.cursor()
        for pragma in pragmas:
            cursor.execute(pragma)
        cursor.close()


async def create_engine(db_path: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine for a file-based SQLite database.

    Args:
        db_path: Path to the SQLite database file.
        echo: If True, log all SQL statements.

    Returns:
        Configured AsyncEngine with WAL mode and foreign keys enabled.
    """
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
        echo=echo,
    )
    _register_pragma_listener(engine, _PRODUCTION_PRAGMAS)
    return engine


async def create_test_engine() -> AsyncEngine:
    """Create an in-memory async engine for tests.

    Uses StaticPool so all connections share one in-memory database.
    Creates all tables from metadata immediately.

    Returns:
        Configured AsyncEngine with tables created and foreign keys enabled.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    _register_pragma_listener(engine, _TEST_PRAGMAS)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return engine
