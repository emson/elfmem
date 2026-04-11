"""Tests for BM25 integration in hybrid_retrieve (stage 2b)."""

import unittest.mock

import pytest

from elfmem import ElfmemConfig, MemorySystem
from elfmem.config import MemoryConfig
from elfmem.memory import retrieval

# BM25 stage tests require rank_bm25; skip gracefully in CI.
_has_bm25 = pytest.importorskip("rank_bm25", reason="rank_bm25 not installed")


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


class TestBM25StageFunction:
    """Direct tests on _stage_2b_bm25_search."""

    def test_returns_empty_when_no_candidates(self):
        result = retrieval._stage_2b_bm25_search([], "test query", n_seeds=10)
        assert result == []

    def test_returns_ranked_results(self):
        candidates = [
            {"id": "a", "content": "the quick brown fox jumps"},
            {"id": "b", "content": "lazy dog sleeps all day"},
            {"id": "c", "content": "the fox is quick and clever"},
        ]
        result = retrieval._stage_2b_bm25_search(candidates, "quick fox", n_seeds=10)
        assert len(result) > 0
        # Blocks mentioning "quick" and "fox" should rank higher
        ids = [b["id"] for b, _ in result]
        # "a" and "c" both contain "quick" and "fox", "b" doesn't
        assert ids[0] in ("a", "c")

    def test_respects_n_seeds_limit(self):
        candidates = [{"id": f"b{i}", "content": f"word{i} content"} for i in range(20)]
        result = retrieval._stage_2b_bm25_search(candidates, "word5", n_seeds=3)
        assert len(result) == 3

    def test_uses_summary_over_content(self):
        candidates = [
            {"id": "a", "content": "irrelevant", "summary": "the target keyword here"},
            {"id": "b", "content": "the target keyword here"},
        ]
        result = retrieval._stage_2b_bm25_search(candidates, "target keyword", n_seeds=10)
        # Both should match — "a" via summary, "b" via content
        ids = [b["id"] for b, _ in result]
        assert "a" in ids
        assert "b" in ids

    def test_returns_empty_when_bm25_unavailable(self):
        """When _HAS_BM25 is False, stage 2b is a no-op."""
        candidates = [{"id": "a", "content": "some content"}]
        with unittest.mock.patch.object(retrieval, "_HAS_BM25", False):
            result = retrieval._stage_2b_bm25_search(candidates, "content", n_seeds=10)
        assert result == []


class TestBM25Integration:
    """Integration tests: BM25 results appear in recall() output."""

    async def test_recall_returns_results_with_bm25(self, system):
        """Basic recall with BM25 available should return results."""
        for i in range(4):
            await system.learn(f"Knowledge item number {i} about topic alpha")
        await system.consolidate()

        blocks = await system.recall(query="alpha", top_k=5)
        assert len(blocks) > 0

    async def test_recall_without_bm25_still_works(self, system):
        """When BM25 is disabled, recall still works (vector-only)."""
        for i in range(4):
            await system.learn(f"Fact {i} about beta topic")
        await system.consolidate()

        with unittest.mock.patch.object(retrieval, "_HAS_BM25", False):
            blocks = await system.recall(query="beta", top_k=5)
        assert len(blocks) > 0

    async def test_bm25_no_duplicate_blocks(self, system):
        """Blocks found by both vector and BM25 should appear only once."""
        for i in range(4):
            await system.learn(f"Unique fact {i} about gamma topic")
        await system.consolidate()

        blocks = await system.recall(query="gamma", top_k=10)
        block_ids = [b.id for b in blocks]
        assert len(block_ids) == len(set(block_ids))  # no duplicates
