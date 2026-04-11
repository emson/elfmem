"""Tests for BM25 hybrid retrieval — tested through the public recall() API."""

import pytest

from elfmem import ElfmemConfig, MemorySystem
from elfmem.config import MemoryConfig


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with inbox_threshold=3 for fast consolidation."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


class TestBM25Retrieval:
    """BM25 integration tested through recall() — the public API."""

    async def test_recall_returns_results(self, system):
        """recall() with query returns relevant blocks."""
        for i in range(4):
            await system.learn(f"Knowledge item number {i} about topic alpha")
        await system.consolidate()

        blocks = await system.recall(query="alpha", top_k=5)
        assert len(blocks) > 0

    async def test_recall_no_duplicate_blocks(self, system):
        """Blocks appear at most once in recall results."""
        for i in range(4):
            await system.learn(f"Unique fact {i} about gamma topic")
        await system.consolidate()

        blocks = await system.recall(query="gamma", top_k=10)
        block_ids = [b.id for b in blocks]
        assert len(block_ids) == len(set(block_ids))

    async def test_recall_empty_database_returns_empty(self, system):
        """recall() on empty database returns empty list, not error."""
        blocks = await system.recall(query="anything", top_k=5)
        assert blocks == []
