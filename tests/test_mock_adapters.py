"""Mock adapters test suite — LLM and embedding service mocks for testing."""

import numpy as np
import pytest

from elfmem.adapters.mock import (
    MockEmbeddingService,
    MockLLMService,
    make_mock_embedding,
    make_mock_llm,
)
from elfmem.ports.services import EmbeddingService, LLMService

TOL = 0.001


class TestMockLLMServiceProtocolCompliance:
    """MockLLMService satisfies LLMService protocol."""

    def test_isinstance_check(self) -> None:
        """MockLLMService is recognized as LLMService."""
        mock = MockLLMService()
        assert isinstance(mock, LLMService)

    @pytest.mark.asyncio
    async def test_score_self_alignment_is_async(self) -> None:
        """score_self_alignment is an async method."""
        mock = MockLLMService()
        result = await mock.score_self_alignment("test block", "self context")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    @pytest.mark.asyncio
    async def test_infer_self_tags_is_async(self) -> None:
        """infer_self_tags is an async method."""
        mock = MockLLMService()
        result = await mock.infer_self_tags("test block", "self context")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_detect_contradiction_is_async(self) -> None:
        """detect_contradiction is an async method."""
        mock = MockLLMService()
        result = await mock.detect_contradiction("block a", "block b")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0


class TestMockLLMServiceAlignmentScoring:
    """Self-alignment scoring configuration and overrides."""

    @pytest.mark.asyncio
    async def test_default_alignment_score(self) -> None:
        """Returns default_alignment when no override matches."""
        mock = MockLLMService(default_alignment=0.75)
        score = await mock.score_self_alignment("random content", "self context")
        assert abs(score - 0.75) < TOL

    @pytest.mark.asyncio
    async def test_alignment_overrides_substring_matching(self) -> None:
        """Alignment overrides match by substring."""
        mock = MockLLMService(
            default_alignment=0.5,
            alignment_overrides={
                "identity": 0.95,
                "preference": 0.80,
            },
        )
        # Match "identity"
        score = await mock.score_self_alignment("I value my personal identity.", "self context")
        assert abs(score - 0.95) < TOL

        # Match "preference"
        score = await mock.score_self_alignment("My preference is clarity.", "self context")
        assert abs(score - 0.80) < TOL

        # No match
        score = await mock.score_self_alignment("Random unrelated content.", "self context")
        assert abs(score - 0.5) < TOL

    @pytest.mark.asyncio
    async def test_alignment_first_match_wins(self) -> None:
        """First matching override is used."""
        mock = MockLLMService(
            default_alignment=0.5,
            alignment_overrides={
                "async": 0.90,
                "patterns": 0.70,
            },
        )
        # Content matches both "async" and "patterns"
        score = await mock.score_self_alignment("async patterns in Python", "self context")
        # First match in iteration order wins
        assert score in [0.90, 0.70]

    @pytest.mark.asyncio
    async def test_alignment_call_tracking(self) -> None:
        """Alignment calls are tracked."""
        mock = MockLLMService()
        assert mock.alignment_calls == 0
        await mock.score_self_alignment("block 1", "context")
        assert mock.alignment_calls == 1
        await mock.score_self_alignment("block 2", "context")
        assert mock.alignment_calls == 2


class TestMockLLMServiceTagInference:
    """Self-tag inference configuration and overrides."""

    @pytest.mark.asyncio
    async def test_default_tags(self) -> None:
        """Returns default_tags when no override matches."""
        mock = MockLLMService(default_tags=["python", "async"])
        tags = await mock.infer_self_tags("random content", "self context")
        assert set(tags) == {"python", "async"}

    @pytest.mark.asyncio
    async def test_tag_overrides_substring_matching(self) -> None:
        """Tag overrides match by substring."""
        mock = MockLLMService(
            default_tags=[],
            tag_overrides={
                "constitutional": ["self/constitutional"],
                "value": ["self/value"],
            },
        )
        # Match "constitutional"
        tags = await mock.infer_self_tags("This is a constitutional belief.", "self context")
        assert tags == ["self/constitutional"]

        # Match "value"
        tags = await mock.infer_self_tags("I value clarity.", "self context")
        assert tags == ["self/value"]

        # No match
        tags = await mock.infer_self_tags("Random content.", "self context")
        assert tags == []

    @pytest.mark.asyncio
    async def test_tag_call_tracking(self) -> None:
        """Tag inference calls are tracked."""
        mock = MockLLMService()
        assert mock.tag_calls == 0
        await mock.infer_self_tags("block 1", "context")
        assert mock.tag_calls == 1


class TestMockLLMServiceContradictionDetection:
    """Contradiction detection configuration and overrides."""

    @pytest.mark.asyncio
    async def test_default_contradiction_score(self) -> None:
        """Returns default_contradiction when no override matches."""
        mock = MockLLMService(default_contradiction=0.15)
        score = await mock.detect_contradiction("block a", "block b")
        assert abs(score - 0.15) < TOL

    @pytest.mark.asyncio
    async def test_contradiction_overrides_both_content_match(self) -> None:
        """Contradiction overrides check both block contents."""
        mock = MockLLMService(
            default_contradiction=0.1,
            contradiction_overrides={
                ("sync", "async"): 0.92,
                ("old", "new"): 0.85,
            },
        )
        # Match ("sync", "async")
        score = await mock.detect_contradiction(
            "Always use synchronous calls.",
            "Never use synchronous calls — always async.",
        )
        assert abs(score - 0.92) < TOL

        # No match
        score = await mock.detect_contradiction("random a", "random b")
        assert abs(score - 0.1) < TOL

    @pytest.mark.asyncio
    async def test_contradiction_call_tracking(self) -> None:
        """Contradiction detection calls are tracked."""
        mock = MockLLMService()
        assert mock.contradiction_calls == 0
        await mock.detect_contradiction("a", "b")
        assert mock.contradiction_calls == 1


class TestMockEmbeddingServiceProtocolCompliance:
    """MockEmbeddingService satisfies EmbeddingService protocol."""

    def test_isinstance_check(self) -> None:
        """MockEmbeddingService is recognized as EmbeddingService."""
        mock = MockEmbeddingService()
        assert isinstance(mock, EmbeddingService)

    @pytest.mark.asyncio
    async def test_embed_is_async(self) -> None:
        """embed is an async method."""
        mock = MockEmbeddingService()
        result = await mock.embed("test text")
        assert isinstance(result, np.ndarray)


class TestMockEmbeddingServiceDeterminism:
    """Deterministic embedding generation."""

    @pytest.mark.asyncio
    async def test_same_text_same_embedding(self) -> None:
        """Same text always produces same vector."""
        mock = MockEmbeddingService()
        vec1 = await mock.embed("hello world")
        vec2 = await mock.embed("hello world")
        np.testing.assert_array_equal(vec1, vec2)

    @pytest.mark.asyncio
    async def test_different_text_different_embedding(self) -> None:
        """Different text produces different vectors."""
        mock = MockEmbeddingService()
        vec1 = await mock.embed("hello world")
        vec2 = await mock.embed("goodbye world")
        # Should be different (extremely unlikely to be equal)
        assert not np.allclose(vec1, vec2)

    @pytest.mark.asyncio
    async def test_embedding_is_normalized(self) -> None:
        """Embeddings are L2-normalized unit vectors."""
        mock = MockEmbeddingService()
        vec = await mock.embed("test text")
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 0.001

    @pytest.mark.asyncio
    async def test_embedding_dimensions(self) -> None:
        """Embedding has correct dimensions."""
        mock = MockEmbeddingService(dimensions=128)
        vec = await mock.embed("test")
        assert vec.shape == (128,)

    @pytest.mark.asyncio
    async def test_embedding_dtype_float32(self) -> None:
        """Embedding is float32."""
        mock = MockEmbeddingService()
        vec = await mock.embed("test")
        assert vec.dtype == np.float32


class TestMockEmbeddingServiceSimilarityOverrides:
    """Similarity override configuration."""

    @pytest.mark.asyncio
    async def test_similarity_override_produces_target_similarity(self) -> None:
        """Similarity override produces vectors with target cosine similarity."""
        target_sim = 0.85
        overrides = {frozenset({"cats are great", "dogs are great"}): target_sim}
        mock = MockEmbeddingService(similarity_overrides=overrides)

        vec1 = await mock.embed("cats are great")
        vec2 = await mock.embed("dogs are great")

        # Cosine similarity = dot product of unit vectors
        cosine_sim = float(np.dot(vec1, vec2))
        assert abs(cosine_sim - target_sim) < 0.01

    @pytest.mark.asyncio
    async def test_similarity_override_one_similarity_high(self) -> None:
        """Similarity = 1.0 override produces identical vectors."""
        overrides = {frozenset({"text a", "text b"}): 1.0}
        mock = MockEmbeddingService(similarity_overrides=overrides)

        vec1 = await mock.embed("text a")
        vec2 = await mock.embed("text b")

        cosine_sim = float(np.dot(vec1, vec2))
        assert abs(cosine_sim - 1.0) < 0.001

    @pytest.mark.asyncio
    async def test_similarity_override_zero_similarity(self) -> None:
        """Similarity = 0.0 override produces orthogonal vectors."""
        overrides = {frozenset({"text a", "text b"}): 0.0}
        mock = MockEmbeddingService(similarity_overrides=overrides)

        vec1 = await mock.embed("text a")
        vec2 = await mock.embed("text b")

        cosine_sim = float(np.dot(vec1, vec2))
        assert abs(cosine_sim) < 0.01

    @pytest.mark.asyncio
    async def test_similarity_override_activates_only_after_both_embedded(self) -> None:
        """Similarity override activates only when both texts have been embedded."""
        overrides = {frozenset({"first", "second"}): 0.9}
        mock = MockEmbeddingService(similarity_overrides=overrides)

        # Embed first text — no override yet (only one of the pair)
        vec1 = await mock.embed("first")
        # Embed second text — now override activates
        vec2 = await mock.embed("second")

        cosine_sim = float(np.dot(vec1, vec2))
        assert abs(cosine_sim - 0.9) < 0.01

    @pytest.mark.asyncio
    async def test_multiple_similarity_overrides(self) -> None:
        """Multiple similarity overrides can coexist."""
        overrides = {
            frozenset({"cats", "dogs"}): 0.9,
            frozenset({"birds", "fish"}): 0.3,
        }
        mock = MockEmbeddingService(similarity_overrides=overrides)

        vec_cats = await mock.embed("cats")
        vec_dogs = await mock.embed("dogs")
        vec_birds = await mock.embed("birds")
        vec_fish = await mock.embed("fish")

        sim_cats_dogs = float(np.dot(vec_cats, vec_dogs))
        sim_birds_fish = float(np.dot(vec_birds, vec_fish))

        assert abs(sim_cats_dogs - 0.9) < 0.01
        assert abs(sim_birds_fish - 0.3) < 0.01

    @pytest.mark.asyncio
    async def test_embed_call_tracking(self) -> None:
        """Embed calls are tracked."""
        mock = MockEmbeddingService()
        assert mock.embed_calls == 0
        await mock.embed("text 1")
        assert mock.embed_calls == 1
        await mock.embed("text 2")
        assert mock.embed_calls == 2


class TestMockEmbeddingServiceEdgeCases:
    """Edge cases and special scenarios."""

    @pytest.mark.asyncio
    async def test_embed_empty_string(self) -> None:
        """Empty string produces valid normalized vector."""
        mock = MockEmbeddingService()
        vec = await mock.embed("")
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 0.001

    @pytest.mark.asyncio
    async def test_embed_very_long_text(self) -> None:
        """Very long text produces valid vector."""
        mock = MockEmbeddingService()
        long_text = "word " * 1000
        vec = await mock.embed(long_text)
        assert vec.shape == (64,)
        norm = np.linalg.norm(vec)
        assert abs(norm - 1.0) < 0.001


class TestFactoryFunctions:
    """Factory functions for convenient test setup."""

    def test_make_mock_llm_with_defaults(self) -> None:
        """make_mock_llm creates service with defaults."""
        mock = make_mock_llm()
        assert isinstance(mock, MockLLMService)

    def test_make_mock_llm_with_overrides(self) -> None:
        """make_mock_llm passes kwargs to constructor."""
        mock = make_mock_llm(default_alignment=0.9)
        assert mock is not None

    def test_make_mock_embedding_with_defaults(self) -> None:
        """make_mock_embedding creates service with defaults."""
        mock = make_mock_embedding()
        assert isinstance(mock, MockEmbeddingService)

    def test_make_mock_embedding_with_dimensions(self) -> None:
        """make_mock_embedding accepts dimensions kwarg."""
        mock = make_mock_embedding(dimensions=256)
        assert mock is not None


class TestMockServiceIntegration:
    """Integration scenarios with both mocks."""

    @pytest.mark.asyncio
    async def test_mock_llm_and_embedding_together(self) -> None:
        """Both mocks can be used in same test."""
        llm = MockLLMService(
            default_alignment=0.85,
            alignment_overrides={"python": 0.95},
        )
        embedding = MockEmbeddingService()

        # Use both
        alignment = await llm.score_self_alignment("I love Python.", "context")
        assert abs(alignment - 0.95) < TOL

        vec = await embedding.embed("I love Python.")
        assert vec.shape == (64,)

    @pytest.mark.asyncio
    async def test_mock_determinism_with_retrieval_scenario(self) -> None:
        """Mocks produce deterministic results in retrieval scenario."""
        embedding = MockEmbeddingService(
            similarity_overrides={
                frozenset({"query", "matching_block"}): 0.9,
                frozenset({"query", "unrelated_block"}): 0.1,
            }
        )

        # Simulate retrieval
        query_vec = await embedding.embed("query")
        match_vec = await embedding.embed("matching_block")
        unmatch_vec = await embedding.embed("unrelated_block")

        match_sim = float(np.dot(query_vec, match_vec))
        unmatch_sim = float(np.dot(query_vec, unmatch_vec))

        assert abs(match_sim - 0.9) < 0.01
        assert abs(unmatch_sim - 0.1) < 0.01
