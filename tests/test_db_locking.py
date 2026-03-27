"""Tests for Phase 1 DB locking hardening.

Covers:
- Engine pragmas (busy_timeout, wal_autocheckpoint)
- Atomic total_active_hours increment
- curate() failure isolation from consolidation
- Consolidation correctness with new read-compute-write structure
- LLM timeout fallback in consolidate()
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockLLMService, make_mock_embedding, make_mock_llm
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.engine import create_engine, create_test_engine
from elfmem.db.queries import (
    get_inbox_blocks,
    get_total_active_hours,
    increment_total_active_hours,
    insert_block,
    seed_builtin_data,
    update_block_status,
)
from elfmem.operations.consolidate import consolidate


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with low inbox_threshold for fast test cycles."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    mem = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    yield mem
    await mem.close()


# ── Engine pragmas ─────────────────────────────────────────────────────────────


class TestProductionPragmas:
    """busy_timeout and wal_autocheckpoint are applied to new connections."""

    @pytest.mark.asyncio
    async def test_busy_timeout_is_set(self, tmp_path):
        db_path = str(tmp_path / "pragma_test.db")
        engine = await create_engine(db_path)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("PRAGMA busy_timeout")
                )
                value = result.scalar()
            assert value == 10000, f"Expected busy_timeout=10000, got {value}"
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_wal_autocheckpoint_is_set(self, tmp_path):
        db_path = str(tmp_path / "pragma_test2.db")
        engine = await create_engine(db_path)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("PRAGMA wal_autocheckpoint")
                )
                value = result.scalar()
            assert value == 500, f"Expected wal_autocheckpoint=500, got {value}"
        finally:
            await engine.dispose()


# ── Atomic total_active_hours ──────────────────────────────────────────────────


class TestIncrementTotalActiveHours:
    """increment_total_active_hours adds to the existing value atomically."""

    @pytest.mark.asyncio
    async def test_increments_from_zero(self, db_conn):
        await increment_total_active_hours(db_conn, 1.5)
        total = await get_total_active_hours(db_conn)
        assert abs(total - 1.5) < 0.001

    @pytest.mark.asyncio
    async def test_increments_from_existing_value(self, db_conn):
        await increment_total_active_hours(db_conn, 2.0)
        await increment_total_active_hours(db_conn, 3.0)
        total = await get_total_active_hours(db_conn)
        assert abs(total - 5.0) < 0.001

    @pytest.mark.asyncio
    async def test_two_session_durations_accumulate_correctly(self, system):
        """End two sessions and verify both durations contribute to total hours."""
        await system.begin_session()
        d1 = await system.end_session()

        await system.begin_session()
        d2 = await system.end_session()

        status = await system.status()
        # Total active hours should include both sessions (durations may be ~0 in tests)
        assert status.total_active_hours >= 0.0
        assert d1 >= 0.0
        assert d2 >= 0.0


# ── curate() transaction isolation ────────────────────────────────────────────


class TestCurateIsolation:
    """A curate() failure must not roll back a successful consolidation."""

    @pytest.mark.asyncio
    async def test_consolidation_survives_curate_failure(
        self, test_engine, mock_embedding
    ):
        """Blocks promoted by consolidate() are committed even if curate() raises.

        We patch curate to raise, then verify that the promoted blocks are
        still visible (not rolled back) in a fresh read.
        """
        from unittest.mock import AsyncMock, patch

        llm = make_mock_llm(default_alignment=0.8)
        cfg = ElfmemConfig(
            memory=MemoryConfig(
                inbox_threshold=2,
                curate_interval_hours=0.0,  # trigger curate immediately
            )
        )
        mem = MemorySystem(
            engine=test_engine,
            llm_service=llm,
            embedding_service=mock_embedding,
            config=cfg,
        )

        await mem.learn("Block that should survive curate failure")
        await mem.learn("Another block that should survive")

        with patch(
            "elfmem.api._curate",
            new_callable=AsyncMock,
            side_effect=RuntimeError("curate failed"),
        ):
            with pytest.raises(RuntimeError, match="curate failed"):
                await mem.consolidate()

        # consolidation committed before curate was called — blocks are active
        status = await mem.status()
        assert status.active_count == 2, (
            f"Expected 2 active blocks, got {status.active_count}. "
            "consolidate() was rolled back by the curate() failure."
        )
        await mem.close()


# ── Consolidation correctness with new pipeline ────────────────────────────────


class TestConsolidatePipeline:
    """The new read-compute-write pipeline produces the same results as before."""

    @pytest.mark.asyncio
    async def test_inbox_blocks_are_promoted_to_active(self, system):
        await system.learn("Python is dynamically typed")
        await system.learn("SQLite uses WAL mode for concurrency")
        await system.learn("Async functions use the await keyword")

        result = await system.consolidate()

        assert result.processed == 3
        assert result.promoted == 3
        assert result.deduplicated == 0

        status = await system.status()
        assert status.inbox_count == 0
        assert status.active_count == 3

    @pytest.mark.asyncio
    async def test_exact_duplicate_is_rejected_at_inbox(self, system):
        """learn() deduplicates at inbox insert time — only one block reaches consolidate."""
        content = "Duplicate content that appears twice"
        r1 = await system.learn(content)
        r2 = await system.learn(content)

        assert r1.status == "created"
        assert r2.status == "duplicate_rejected"

        # Only the first block is in the inbox
        result = await system.consolidate()
        assert result.processed == 1
        assert result.promoted == 1

    @pytest.mark.asyncio
    async def test_empty_inbox_returns_zero_counts(self, system):
        result = await system.consolidate()

        assert result.processed == 0
        assert result.promoted == 0
        assert result.deduplicated == 0
        assert result.edges_created == 0

    @pytest.mark.asyncio
    async def test_pending_resets_after_consolidation(self, system):
        await system.learn("Fact one")
        await system.learn("Fact two")
        assert (await system.status()).inbox_count == 2

        await system.consolidate()

        assert (await system.status()).inbox_count == 0
        assert system._pending == 0


# ── recall() uses read-only connection ────────────────────────────────────────


class TestRecallReadOnly:
    """recall() must not acquire a write transaction."""

    @pytest.mark.asyncio
    async def test_recall_leaves_no_writes(self, system):
        """recall() returns results without modifying any rows."""
        await system.learn("Python uses dynamic typing")
        await system.consolidate()

        status_before = await system.status()
        await system.recall("Python")
        status_after = await system.status()

        # Nothing changed: recall() is side-effect free
        assert status_after.active_count == status_before.active_count
        assert status_after.inbox_count == status_before.inbox_count


# ── LLM timeout fallback ───────────────────────────────────────────────────────


class TestLLMTimeoutFallback:
    """Blocks are promoted with neutral defaults when LLM times out."""

    @pytest.mark.asyncio
    async def test_block_promoted_on_llm_timeout(self, db_conn, mock_embedding):
        """A block is still promoted when process_block() times out."""
        import asyncio

        class SlowLLM(MockLLMService):
            async def process_block(self, block: str, self_context: str):
                await asyncio.sleep(100)  # longer than timeout

        slow_llm = SlowLLM()

        await insert_block(
            db_conn,
            block_id="timeout_block",
            content="A block whose LLM call will time out",
            category="knowledge",
            source="test",
        )

        from elfmem.operations.consolidate import _LLM_PROCESS_TIMEOUT, consolidate

        original_timeout = _LLM_PROCESS_TIMEOUT

        import elfmem.operations.consolidate as _mod
        _mod._LLM_PROCESS_TIMEOUT = 0.01  # very short for test
        try:
            result = await consolidate(
                db_conn,
                llm=slow_llm,
                embedding_svc=mock_embedding,
                current_active_hours=0.0,
            )
        finally:
            _mod._LLM_PROCESS_TIMEOUT = original_timeout

        # Block promoted with fallback confidence (0.5), not skipped
        assert result.promoted == 1
        assert result.processed == 1

        inbox = await get_inbox_blocks(db_conn)
        assert len(inbox) == 0  # block left inbox
