"""Mock adapters — protocol compliance, determinism, and override behaviour.

These tests verify that the mock services satisfy their protocols and that
override configuration produces the deterministic behaviour that the rest
of the test suite depends on.
"""

from __future__ import annotations

import numpy as np
import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.types import BlockAnalysis

TOL = 0.001


# ── Protocol compliance ────────────────────────────────────────────────────────


class TestMockLLMServiceProtocol:
    """MockLLMService satisfies the LLMService protocol."""

    def test_isinstance_check(self) -> None:
        assert isinstance(MockLLMService(), LLMService)

    @pytest.mark.asyncio
    async def test_process_block_returns_block_analysis(self) -> None:
        result = await MockLLMService().process_block("test block", "context")
        assert isinstance(result, BlockAnalysis)

    @pytest.mark.asyncio
    async def test_detect_contradiction_returns_float_in_range(self) -> None:
        score = await MockLLMService().detect_contradiction("block a", "block b")
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0


class TestMockEmbeddingServiceProtocol:
    """MockEmbeddingService satisfies the EmbeddingService protocol."""

    def test_isinstance_check(self) -> None:
        assert isinstance(MockEmbeddingService(), EmbeddingService)

    @pytest.mark.asyncio
    async def test_embed_returns_numpy_array(self) -> None:
        vec = await MockEmbeddingService().embed("test text")
        assert isinstance(vec, np.ndarray)


# ── LLM override configuration ─────────────────────────────────────────────────


class TestMockLLMServiceConfiguration:
    """Override configuration drives deterministic test scenarios."""

    @pytest.mark.asyncio
    async def test_default_alignment_used_when_no_override_matches(self) -> None:
        mock = MockLLMService(default_alignment=0.75)
        result = await mock.process_block("random content", "context")
        assert abs(result.alignment_score - 0.75) < TOL

    @pytest.mark.asyncio
    async def test_alignment_override_matches_by_substring(self) -> None:
        mock = MockLLMService(default_alignment=0.5, alignment_overrides={"identity": 0.95})
        result = await mock.process_block("I value my personal identity.", "context")
        assert abs(result.alignment_score - 0.95) < TOL

    @pytest.mark.asyncio
    async def test_default_tags_used_when_no_override_matches(self) -> None:
        mock = MockLLMService(default_tags=["python", "async"])
        result = await mock.process_block("random content", "context")
        assert set(result.tags) == {"python", "async"}

    @pytest.mark.asyncio
    async def test_tag_override_matches_by_substring(self) -> None:
        mock = MockLLMService(tag_overrides={"constitutional": ["self/constitutional"]})
        result = await mock.process_block("This is a constitutional belief.", "context")
        assert result.tags == ["self/constitutional"]

    @pytest.mark.asyncio
    async def test_contradiction_override_matches_both_contents(self) -> None:
        mock = MockLLMService(
            default_contradiction=0.1,
            contradiction_overrides={("sync", "async"): 0.92},
        )
        score = await mock.detect_contradiction(
            "Always use synchronous calls.",
            "Never use synchronous calls — always async.",
        )
        assert abs(score - 0.92) < TOL

    @pytest.mark.asyncio
    async def test_default_contradiction_score(self) -> None:
        mock = MockLLMService(default_contradiction=0.15)
        score = await mock.detect_contradiction("block a", "block b")
        assert abs(score - 0.15) < TOL


# ── Embedding determinism ──────────────────────────────────────────────────────


class TestMockEmbeddingServiceDeterminism:
    """Embedding vectors are deterministic and normalized."""

    @pytest.mark.asyncio
    async def test_same_text_same_embedding(self) -> None:
        mock = MockEmbeddingService()
        vec1 = await mock.embed("hello world")
        vec2 = await mock.embed("hello world")
        np.testing.assert_array_equal(vec1, vec2)

    @pytest.mark.asyncio
    async def test_different_text_different_embedding(self) -> None:
        mock = MockEmbeddingService()
        vec1 = await mock.embed("hello world")
        vec2 = await mock.embed("goodbye world")
        assert not np.allclose(vec1, vec2)

    @pytest.mark.asyncio
    async def test_embedding_is_normalized(self) -> None:
        vec = await MockEmbeddingService().embed("test text")
        assert abs(np.linalg.norm(vec) - 1.0) < TOL

    @pytest.mark.asyncio
    async def test_embedding_dimensions_configurable(self) -> None:
        vec = await MockEmbeddingService(dimensions=128).embed("test")
        assert vec.shape == (128,)


# ── Similarity overrides ───────────────────────────────────────────────────────


class TestMockEmbeddingServiceSimilarityOverrides:
    """Similarity overrides control cosine similarity between text pairs."""

    @pytest.mark.asyncio
    async def test_similarity_override_achieves_target(self) -> None:
        overrides = {frozenset({"cats are great", "dogs are great"}): 0.85}
        mock = MockEmbeddingService(similarity_overrides=overrides)
        vec1 = await mock.embed("cats are great")
        vec2 = await mock.embed("dogs are great")
        assert abs(float(np.dot(vec1, vec2)) - 0.85) < 0.01

    @pytest.mark.asyncio
    async def test_similarity_zero_produces_orthogonal_vectors(self) -> None:
        overrides = {frozenset({"text a", "text b"}): 0.0}
        mock = MockEmbeddingService(similarity_overrides=overrides)
        vec1 = await mock.embed("text a")
        vec2 = await mock.embed("text b")
        assert abs(float(np.dot(vec1, vec2))) < 0.01

    @pytest.mark.asyncio
    async def test_multiple_overrides_coexist(self) -> None:
        overrides = {
            frozenset({"cats", "dogs"}): 0.9,
            frozenset({"birds", "fish"}): 0.3,
        }
        mock = MockEmbeddingService(similarity_overrides=overrides)
        vc, vd = await mock.embed("cats"), await mock.embed("dogs")
        vb, vf = await mock.embed("birds"), await mock.embed("fish")
        assert abs(float(np.dot(vc, vd)) - 0.9) < 0.01
        assert abs(float(np.dot(vb, vf)) - 0.3) < 0.01

    @pytest.mark.asyncio
    async def test_retrieval_scenario_discriminates_match_from_noise(self) -> None:
        """Real test scenario: one block matches query, one does not."""
        mock = MockEmbeddingService(
            similarity_overrides={
                frozenset({"query", "matching_block"}): 0.9,
                frozenset({"query", "unrelated_block"}): 0.1,
            }
        )
        query_vec = await mock.embed("query")
        match_vec = await mock.embed("matching_block")
        unmatch_vec = await mock.embed("unrelated_block")
        assert abs(float(np.dot(query_vec, match_vec)) - 0.9) < 0.01
        assert abs(float(np.dot(query_vec, unmatch_vec)) - 0.1) < 0.01


# ── Batch embedding ────────────────────────────────────────────────────────────


class TestMockEmbeddingServiceBatch:
    """Batch embedding produces correct and consistent results."""

    @pytest.mark.asyncio
    async def test_embed_batch_returns_normalized_vectors(self) -> None:
        vecs = await MockEmbeddingService().embed_batch(["text1", "text2", "text3"])
        assert len(vecs) == 3
        assert all(abs(np.linalg.norm(v) - 1.0) < TOL for v in vecs)

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list(self) -> None:
        assert await MockEmbeddingService().embed_batch([]) == []

    @pytest.mark.asyncio
    async def test_embed_batch_applies_similarity_overrides(self) -> None:
        mock = MockEmbeddingService(
            similarity_overrides={frozenset({"text1", "text2"}): 0.9}
        )
        vecs = await mock.embed_batch(["text1", "text2"])
        assert abs(float(np.dot(vecs[0], vecs[1])) - 0.9) < 0.01
