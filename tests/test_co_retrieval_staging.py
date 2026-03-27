"""Tests for co-retrieval staging persistence (Step 6 of DB locking plan).

Covers:
- Staging counts are written to DB during frame() calls
- Promoted pairs are deleted from DB
- FK CASCADE removes staging rows when a block is archived
- Staging is restored on restart (simulated via load_co_retrieval_staging)
- In-memory dict stays consistent with DB after curate()
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.queries import (
    delete_co_retrieval_pair,
    insert_block,
    load_co_retrieval_staging,
    prune_stale_co_retrieval_staging,
    update_block_status,
    upsert_co_retrieval_count,
)
from elfmem.memory.graph import stage_and_promote_co_retrievals

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(
    test_engine: AsyncEngine, mock_llm: MockLLMService, mock_embedding: MockEmbeddingService
) -> AsyncIterator[MemorySystem]:
    """MemorySystem with threshold=2 so promotion is easy to trigger."""
    cfg = ElfmemConfig(
        memory=MemoryConfig(
            inbox_threshold=3,
            co_retrieval_edge_threshold=2,
        )
    )
    mem = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    yield mem
    await mem.close()


# ── Query-layer unit tests ──────────────────────────────────────────────────────


class TestCoRetrievalQueries:
    """upsert / load / delete round-trip correctly."""

    @pytest.mark.asyncio
    async def test_upsert_inserts_new_pair(self, db_conn: AsyncConnection) -> None:
        await insert_block(db_conn, block_id="a1", content="A", category="knowledge", source="t")
        await insert_block(db_conn, block_id="b1", content="B", category="knowledge", source="t")

        await upsert_co_retrieval_count(db_conn, ("a1", "b1"), 1)

        staging = await load_co_retrieval_staging(db_conn)
        assert staging[("a1", "b1")] == 1

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_count(self, db_conn: AsyncConnection) -> None:
        await insert_block(db_conn, block_id="a2", content="A2", category="knowledge", source="t")
        await insert_block(db_conn, block_id="b2", content="B2", category="knowledge", source="t")

        await upsert_co_retrieval_count(db_conn, ("a2", "b2"), 1)
        await upsert_co_retrieval_count(db_conn, ("a2", "b2"), 2)

        staging = await load_co_retrieval_staging(db_conn)
        assert staging[("a2", "b2")] == 2

    @pytest.mark.asyncio
    async def test_delete_removes_pair(self, db_conn: AsyncConnection) -> None:
        await insert_block(db_conn, block_id="a3", content="A3", category="knowledge", source="t")
        await insert_block(db_conn, block_id="b3", content="B3", category="knowledge", source="t")

        await upsert_co_retrieval_count(db_conn, ("a3", "b3"), 1)
        await delete_co_retrieval_pair(db_conn, ("a3", "b3"))

        staging = await load_co_retrieval_staging(db_conn)
        assert ("a3", "b3") not in staging

    @pytest.mark.asyncio
    async def test_load_returns_empty_dict_when_no_rows(self, db_conn: AsyncConnection) -> None:
        staging = await load_co_retrieval_staging(db_conn)
        assert staging == {}


# ── Stale-staging prune tests ──────────────────────────────────────────────────


class TestPruneStaleStaging:
    """prune_stale_co_retrieval_staging() removes rows for non-active blocks.

    FK CASCADE handles physical deletions; prune handles the normal archival
    case where the block row stays but its status changes to 'archived'.
    """

    @pytest.mark.asyncio
    async def test_prune_removes_row_when_from_block_archived(self, db_conn: AsyncConnection) -> None:
        await insert_block(db_conn, block_id="x1", content="X1", category="knowledge", source="t")
        await insert_block(db_conn, block_id="y1", content="Y1", category="knowledge", source="t")
        await upsert_co_retrieval_count(db_conn, ("x1", "y1"), 1)

        await update_block_status(db_conn, "x1", "archived", archive_reason="decayed")
        await prune_stale_co_retrieval_staging(db_conn)

        staging = await load_co_retrieval_staging(db_conn)
        assert ("x1", "y1") not in staging

    @pytest.mark.asyncio
    async def test_prune_removes_row_when_to_block_archived(self, db_conn: AsyncConnection) -> None:
        await insert_block(db_conn, block_id="x2", content="X2", category="knowledge", source="t")
        await insert_block(db_conn, block_id="y2", content="Y2", category="knowledge", source="t")
        await upsert_co_retrieval_count(db_conn, ("x2", "y2"), 1)

        await update_block_status(db_conn, "y2", "archived", archive_reason="decayed")
        await prune_stale_co_retrieval_staging(db_conn)

        staging = await load_co_retrieval_staging(db_conn)
        assert ("x2", "y2") not in staging

    @pytest.mark.asyncio
    async def test_prune_leaves_active_pair_untouched(self, db_conn: AsyncConnection) -> None:
        await insert_block(
            db_conn, block_id="x3", content="X3", category="knowledge", source="t", status="active"
        )
        await insert_block(
            db_conn, block_id="y3", content="Y3", category="knowledge", source="t", status="active"
        )
        await upsert_co_retrieval_count(db_conn, ("x3", "y3"), 2)

        await prune_stale_co_retrieval_staging(db_conn)

        staging = await load_co_retrieval_staging(db_conn)
        assert staging[("x3", "y3")] == 2


# ── Graph-layer integration tests ──────────────────────────────────────────────


class TestStageAndPromote:
    """stage_and_promote_co_retrievals() persists counts and cleans up on promotion."""

    @pytest.mark.asyncio
    async def test_staging_count_written_to_db(self, db_conn: AsyncConnection, mock_embedding: MockEmbeddingService) -> None:
        await insert_block(
            db_conn, block_id="p1", content="P1", category="knowledge", source="t", status="active"
        )
        await insert_block(
            db_conn, block_id="q1", content="Q1", category="knowledge", source="t", status="active"
        )

        in_memory: dict[tuple[str, str], int] = {}
        await stage_and_promote_co_retrievals(
            db_conn,
            ["p1", "q1"],
            in_memory,
            threshold=3,
            edge_weight=0.55,
            current_active_hours=1.0,
        )

        staging = await load_co_retrieval_staging(db_conn)
        assert staging.get(("p1", "q1"), 0) == 1

    @pytest.mark.asyncio
    async def test_promotion_deletes_staging_row(self, db_conn: AsyncConnection, mock_embedding: MockEmbeddingService) -> None:
        await insert_block(
            db_conn, block_id="p2", content="P2", category="knowledge", source="t", status="active"
        )
        await insert_block(
            db_conn, block_id="q2", content="Q2", category="knowledge", source="t", status="active"
        )

        in_memory: dict[tuple[str, str], int] = {}
        # Two calls with threshold=2 → second call promotes the pair
        for _ in range(2):
            await stage_and_promote_co_retrievals(
                db_conn,
                ["p2", "q2"],
                in_memory,
                threshold=2,
                edge_weight=0.55,
                current_active_hours=1.0,
            )

        staging = await load_co_retrieval_staging(db_conn)
        assert ("p2", "q2") not in staging
        assert in_memory == {}


# ── Restart-persistence simulation ─────────────────────────────────────────────


class TestRestartPersistence:
    """MemorySystem initialised with pre-loaded staging reflects DB state."""

    @pytest.mark.asyncio
    async def test_staging_restored_into_memory_system(self, test_engine: AsyncEngine, mock_llm: MockLLMService, mock_embedding: MockEmbeddingService) -> None:
        """Staging loaded at startup is reflected in status()."""
        async with test_engine.begin() as conn:
            await insert_block(
                conn, block_id="r1", content="R1", category="knowledge", source="t", status="active"
            )
            await insert_block(
                conn, block_id="s1", content="S1", category="knowledge", source="t", status="active"
            )
            await upsert_co_retrieval_count(conn, ("r1", "s1"), 2)

        async with test_engine.connect() as conn:
            restored = await load_co_retrieval_staging(conn)

        cfg = ElfmemConfig(memory=MemoryConfig(co_retrieval_edge_threshold=3))
        mem = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
            initial_co_retrieval_staging=restored,
        )
        try:
            status = await mem.status()
            assert status.co_retrieval_staging_count == 1
        finally:
            await mem.close()

    @pytest.mark.asyncio
    async def test_empty_staging_on_fresh_start(self, test_engine: AsyncEngine, mock_llm: MockLLMService, mock_embedding: MockEmbeddingService) -> None:
        """No staging rows → in-memory dict starts empty."""
        async with test_engine.connect() as conn:
            restored = await load_co_retrieval_staging(conn)

        mem = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            initial_co_retrieval_staging=restored,
        )
        try:
            status = await mem.status()
            assert status.co_retrieval_staging_count == 0
        finally:
            await mem.close()


# ── Curate() sync tests ────────────────────────────────────────────────────────


class TestCurateStaginSync:
    """After curate(), in-memory staging matches DB state."""

    @pytest.mark.asyncio
    async def test_curate_syncs_staging_after_archival(self, test_engine: AsyncEngine, mock_llm: MockLLMService, mock_embedding: MockEmbeddingService) -> None:
        """Blocks archived by curate() have their staging rows removed via CASCADE.
        curate() then reloads staging so _co_retrieval_staging is consistent.
        """
        cfg = ElfmemConfig(
            memory=MemoryConfig(
                inbox_threshold=3,
                co_retrieval_edge_threshold=5,
                curate_interval_hours=0.0,  # run curate on every consolidate()
            )
        )
        mem = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )

        # Seed staging with a pair that points to a soon-to-be-archived block.
        # last_reinforced_at is far in the past so recency drops below prune_threshold
        # even when current_active_hours=0 (hours_since = 0 - (-1e6) = 1_000_000).
        async with test_engine.begin() as conn:
            await insert_block(
                conn,
                block_id="stale1",
                content="stale block",
                category="knowledge",
                source="t",
                status="active",
                last_reinforced_at=-1_000_000.0,
            )
            await insert_block(
                conn,
                block_id="stale2",
                content="stale partner",
                category="knowledge",
                source="t",
                status="active",
                last_reinforced_at=-1_000_000.0,
            )
            await upsert_co_retrieval_count(conn, ("stale1", "stale2"), 2)

        # Pre-load staging into memory system
        async with test_engine.connect() as conn:
            mem._co_retrieval_staging = await load_co_retrieval_staging(conn)

        assert mem._co_retrieval_staging.get(("stale1", "stale2")) == 2

        # curate() archives stale blocks; prune_stale_co_retrieval_staging removes
        # their staging rows; curate() then reloads the staging dict
        await mem.curate()

        assert ("stale1", "stale2") not in mem._co_retrieval_staging

        await mem.close()
