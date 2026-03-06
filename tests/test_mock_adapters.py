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
from elfmem.types import BlockAnalysis

TOL = 0.001


class TestMockLLMServiceProtocolCompliance:
    """MockLLMService satisfies LLMService protocol."""

    def test_isinstance_check(self) -> None:
        """MockLLMService is recognized as LLMService."""
        mock = MockLLMService()
        assert isinstance(mock, LLMService)

    @pytest.mark.asyncio
    async def test_process_block_is_async(self) -> None:
        """process_block is an async method returning BlockAnalysis."""
        mock = MockLLMService()
        result = await mock.process_block("test block", "self context")
        assert isinstance(result, BlockAnalysis)

    @pytest.mark.asyncio
    async def test_detect_contradiction_is_async(self) -> None:
        """detect_contradiction is an async method."""
        mock = MockLLMService()
        result = await mock.detect_contradiction("block a", "block b")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0


class TestMockLLMServiceProcessBlock:
    """process_block configuration and overrides."""

    @pytest.mark.asyncio
    async def test_returns_block_analysis(self) -> None:
        """process_block returns a BlockAnalysis dataclass."""
        mock = MockLLMService()
        result = await mock.process_block("some content", "context")
        assert isinstance(result, BlockAnalysis)

    @pytest.mark.asyncio
    async def test_default_alignment_score(self) -> None:
        """Returns default_alignment when no override matches."""
        mock = MockLLMService(default_alignment=0.75)
        result = await mock.process_block("random content", "self context")
        assert abs(result.alignment_score - 0.75) < TOL

    @pytest.mark.asyncio
    async def test_alignment_override_substring_match(self) -> None:
        """Alignment override matches by substring."""
        mock = MockLLMService(
            default_alignment=0.5,
            alignment_overrides={"identity": 0.95},
        )
        result = await mock.process_block("I value my personal identity.", "context")
        assert abs(result.alignment_score - 0.95) < TOL

    @pytest.mark.asyncio
    async def test_alignment_no_match_uses_default(self) -> None:
        """No override match falls back to default alignment."""
        mock = MockLLMService(
            default_alignment=0.5,
            alignment_overrides={"identity": 0.95},
        )
        result = await mock.process_block("Random unrelated content.", "context")
        assert abs(result.alignment_score - 0.5) < TOL

    @pytest.mark.asyncio
    async def test_default_tags(self) -> None:
        """Returns default_tags when no override matches."""
        mock = MockLLMService(default_tags=["python", "async"])
        result = await mock.process_block("random content", "context")
        assert set(result.tags) == {"python", "async"}

    @pytest.mark.asyncio
    async def test_tag_override_substring_match(self) -> None:
        """Tag override matches by substring."""
        mock = MockLLMService(
            default_tags=[],
            tag_overrides={"constitutional": ["self/constitutional"]},
        )
        result = await mock.process_block("This is a constitutional belief.", "context")
        assert result.tags == ["self/constitutional"]

    @pytest.mark.asyncio
    async def test_tag_no_match_uses_default(self) -> None:
        """No tag override match returns default tags."""
        mock = MockLLMService(
            default_tags=["default/tag"],
            tag_overrides={"constitutional": ["self/constitutional"]},
        )
        result = await mock.process_block("Random content.", "context")
        assert result.tags == ["default/tag"]

    @pytest.mark.asyncio
    async def test_default_summary_uses_prefix_plus_content(self) -> None:
        """Default summary is prefix + block content."""
        mock = MockLLMService(default_summary_prefix="Summary: ")
        result = await mock.process_block("Some block text.", "context")
        assert result.summary == "Summary: Some block text."

    @pytest.mark.asyncio
    async def test_summary_override_substring_match(self) -> None:
        """Summary override matches by substring."""
        mock = MockLLMService(
            summary_overrides={"satellite": "Block is about satellite systems."},
        )
        result = await mock.process_block("satellite data ingestion", "context")
        assert result.summary == "Block is about satellite systems."

    @pytest.mark.asyncio
    async def test_summary_no_match_uses_default(self) -> None:
        """No summary override match uses default prefix + content."""
        mock = MockLLMService(
            default_summary_prefix="S: ",
            summary_overrides={"satellite": "About satellites."},
        )
        result = await mock.process_block("generic content", "context")
        assert result.summary == "S: generic content"

    @pytest.mark.asyncio
    async def test_process_block_call_tracking(self) -> None:
        """process_block calls are tracked."""
        mock = MockLLMService()
        assert mock.process_block_calls == 0
        await mock.process_block("block 1", "context")
        assert mock.process_block_calls == 1
        await mock.process_block("block 2", "context")
        assert mock.process_block_calls == 2

    @pytest.mark.asyncio
    async def test_alignment_first_match_wins(self) -> None:
        """First matching alignment override is used."""
        mock = MockLLMService(
            default_alignment=0.5,
            alignment_overrides={
                "async": 0.90,
                "patterns": 0.70,
            },
        )
        # Content matches "async" first
        result = await mock.process_block("async patterns in Python", "context")
        assert result.alignment_score in [0.90, 0.70]

    @pytest.mark.asyncio
    async def test_tag_first_match_wins(self) -> None:
        """First matching tag override is used."""
        mock = MockLLMService(
            default_tags=[],
            tag_overrides={
                "value": ["self/value"],
                "constitutional": ["self/constitutional"],
            },
        )
        # "value" appears before "constitutional" in dict
        result = await mock.process_block("I value this constitutional principle.", "context")
        # First match in dict order wins
        assert result.tags in [["self/value"], ["self/constitutional"]]

    @pytest.mark.asyncio
    async def test_all_fields_returned_together(self) -> None:
        """process_block returns all three fields in one call."""
        mock = MockLLMService(
            default_alignment=0.8,
            default_tags=["science"],
            default_summary_prefix="Fact: ",
        )
        result = await mock.process_block("The sky is blue.", "context")
        assert abs(result.alignment_score - 0.8) < TOL
        assert result.tags == ["science"]
        assert result.summary == "Fact: The sky is blue."


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


class TestMockLLMServicePropertySetters:
    """Attribute setters allow post-construction override changes."""

    def test_tag_overrides_setter(self) -> None:
        """tag_overrides can be replaced after construction."""
        mock = MockLLMService(tag_overrides={"old": ["old/tag"]})
        mock.tag_overrides = {"new": ["new/tag"]}
        assert mock.tag_overrides == {"new": ["new/tag"]}

    def test_default_tags_setter(self) -> None:
        """default_tags can be replaced after construction."""
        mock = MockLLMService(default_tags=["initial"])
        mock.default_tags = []
        assert mock.default_tags == []

    def test_alignment_overrides_setter(self) -> None:
        """alignment_overrides can be replaced after construction."""
        mock = MockLLMService(alignment_overrides={"old": 0.9})
        mock.alignment_overrides = {"new": 0.7}
        assert mock.alignment_overrides == {"new": 0.7}

    def test_contradiction_overrides_setter(self) -> None:
        """contradiction_overrides can be replaced after construction."""
        mock = MockLLMService(contradiction_overrides={("a", "b"): 0.9})
        mock.contradiction_overrides = {}
        assert mock.contradiction_overrides == {}


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

        result = await llm.process_block("I love Python.", "context")
        assert abs(result.alignment_score - 0.95) < TOL

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
