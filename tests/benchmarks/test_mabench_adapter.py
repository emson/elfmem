"""Tests for MemoryAgentBench adapter utilities — BM25 index and RRF merge."""

import pytest

pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")

from benchmarks.memoryagentbench.adapter import _BM25Index, _rrf_merge


class _FakeBlock:
    """Minimal stand-in for ScoredBlock in RRF merge tests."""

    def __init__(self, block_id: str, content: str) -> None:
        self.id = block_id
        self.content = content


class TestBM25Index:
    def test_search_returns_id_content_score_triples(self) -> None:
        idx = _BM25Index()
        idx.add("id1", "python async programming")
        idx.add("id2", "java synchronous code")
        idx.add("id3", "ruby sequential scripts")  # 3 docs: IDF for 'async' is positive
        idx.build()
        results = idx.search("async", top_k=3)
        block_id, content, score = results[0]
        assert block_id == "id1"
        assert content == "python async programming"
        assert score > 0

    def test_search_ranks_by_relevance(self) -> None:
        idx = _BM25Index()
        idx.add("a", "python async")
        idx.add("b", "java synchronous")
        idx.build()
        results = idx.search("python", top_k=2)
        assert results[0][0] == "a"

    def test_search_empty_index_returns_empty(self) -> None:
        idx = _BM25Index()
        idx.build()
        assert idx.search("anything") == []

    def test_search_respects_top_k(self) -> None:
        idx = _BM25Index()
        for i in range(10):
            idx.add(f"id{i}", f"content about topic {i}")
        idx.build()
        assert len(idx.search("content topic", top_k=3)) == 3


class TestRRFMerge:
    def test_matched_bm25_block_scores_higher(self) -> None:
        """A block present in both vector and BM25 results scores higher than one in vector only."""
        b1 = _FakeBlock("b1", "async python programming")
        b2 = _FakeBlock("b2", "java synchronous code")
        vector_blocks = [b1, b2]  # b1 ranked first by vector

        bm25_results = [("b1", "async python programming", 5.0)]  # b1 also in BM25

        _, context = _rrf_merge(vector_blocks, bm25_results, top_k=2)
        # b1 should appear first — boosted by both retrieval paths
        assert context.startswith("async python programming")

    def test_unmatched_bm25_id_ignored(self) -> None:
        """BM25 result for unknown block ID has no effect — no supplementary raw chunks."""
        b1 = _FakeBlock("b1", "block one")
        vector_blocks = [b1]
        bm25_results = [("unknown_id", "some raw text", 3.0)]

        trimmed, context = _rrf_merge(vector_blocks, bm25_results, top_k=1)
        assert len(trimmed) == 1
        assert "some raw text" not in context

    def test_context_is_newline_joined_block_content(self) -> None:
        b1 = _FakeBlock("b1", "first block")
        b2 = _FakeBlock("b2", "second block")
        _, context = _rrf_merge([b1, b2], [], top_k=2)
        assert "first block" in context
        assert "second block" in context
        assert "\n\n" in context

    def test_top_k_limits_output(self) -> None:
        blocks = [_FakeBlock(f"b{i}", f"block {i}") for i in range(5)]
        trimmed, _ = _rrf_merge(blocks, [], top_k=2)
        assert len(trimmed) == 2
