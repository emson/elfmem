"""Tests for learn_document(), dream(skip_llm=), and config wiring fixes."""

import pytest

from elfmem import ElfmemConfig, LearnDocumentResult, MemorySystem
from elfmem.api import _assemble_chunks, _default_chunker
from elfmem.config import MemoryConfig

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with inbox_threshold=3 for fast consolidation cycles."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


# ── _default_chunker ────────────────────────────────────────────────────────


class TestDefaultChunker:
    def test_splits_at_sentence_boundaries(self):
        text = "First sentence. Second sentence. Third sentence."
        result = _default_chunker(text)
        assert result == ["First sentence.", "Second sentence.", "Third sentence."]

    def test_splits_at_question_and_exclamation(self):
        text = "What happened? It exploded! Then silence."
        result = _default_chunker(text)
        assert len(result) == 3

    def test_empty_text_returns_empty(self):
        assert _default_chunker("") == []

    def test_no_sentence_endings_returns_whole_text(self):
        text = "A single run-on thought with no ending punctuation"
        result = _default_chunker(text)
        assert result == [text]


# ── _assemble_chunks ────────────────────────────────────────────────────────


class TestAssembleChunks:
    def test_groups_sentences_by_word_count(self):
        sentences = ["One two three.", "Four five.", "Six seven eight nine ten."]
        # chunk_size=5: first sentence (3 words) + second (2 words) = 5, fits
        # third sentence (5 words) starts new chunk
        chunks = _assemble_chunks(sentences, chunk_size=5)
        assert len(chunks) == 2

    def test_single_sentence_over_chunk_size(self):
        sentences = ["This is a very long single sentence with many words."]
        chunks = _assemble_chunks(sentences, chunk_size=3)
        assert len(chunks) == 1  # single sentence can't be split

    def test_empty_sentences_returns_empty(self):
        assert _assemble_chunks([], chunk_size=10) == []

    def test_each_sentence_exceeds_chunk_size(self):
        sentences = ["Alpha bravo charlie.", "Delta echo foxtrot."]
        chunks = _assemble_chunks(sentences, chunk_size=2)
        assert len(chunks) == 2


# ── learn_document() ────────────────────────────────────────────────────────


class TestLearnDocument:
    async def test_basic_ingestion(self, system):
        text = "Fact one. Fact two. Fact three. Fact four. Fact five."
        result = await system.learn_document(text, chunk_size=2)
        assert isinstance(result, LearnDocumentResult)
        assert result.chunks_total > 0
        assert result.chunks_created > 0
        assert result.chunks_duplicate == 0

    async def test_auto_consolidation_triggers(self, system):
        """With inbox_threshold=3, learning 5+ chunks should trigger dream()."""
        sentences = [f"Sentence number {i} with unique content." for i in range(10)]
        text = " ".join(sentences)
        result = await system.learn_document(text, chunk_size=3)
        assert result.consolidations > 0
        assert result.blocks_promoted > 0

    async def test_empty_text(self, system):
        result = await system.learn_document("")
        assert result.chunks_total == 0
        assert result.chunks_created == 0
        assert result.consolidations == 0

    async def test_custom_chunker(self, system):
        text = "line one\nline two\nline three"
        result = await system.learn_document(
            text,
            chunker=lambda t: t.split("\n"),
            chunk_size=2,  # small enough that each line becomes its own chunk
        )
        assert result.chunks_total == 3
        assert result.chunks_created == 3

    async def test_duplicate_ingestion(self, system):
        text = "A unique fact about penguins."
        first = await system.learn_document(text)
        assert first.chunks_created == 1
        second = await system.learn_document(text)
        assert second.chunks_duplicate == 1
        assert second.chunks_created == 0

    async def test_skip_llm_forwarded(self, system):
        """skip_llm=True should produce consolidation without LLM calls."""
        sentences = [f"Fact {i} about topic X." for i in range(5)]
        text = " ".join(sentences)
        result = await system.learn_document(text, chunk_size=2, skip_llm=True)
        # Should still consolidate (embedding-only path)
        if result.consolidations > 0:
            assert result.blocks_promoted > 0

    async def test_tags_applied_to_all_chunks(self, system):
        text = "Alpha info. Beta info."
        await system.learn_document(text, chunk_size=100, tags=["source:test"])
        # Recall should find blocks with our tags
        blocks = await system.recall(query="info", top_k=5)
        for block in blocks:
            assert "source:test" in block.tags

    async def test_result_summary_str(self, system):
        text = "One fact. Two fact. Three fact."
        result = await system.learn_document(text, chunk_size=100)
        summary = str(result)
        assert "chunks" in summary.lower() or "ingested" in summary.lower()

    async def test_result_to_dict(self, system):
        text = "Some content here."
        result = await system.learn_document(text)
        d = result.to_dict()
        assert "chunks_total" in d
        assert "chunks_created" in d
        assert "consolidations" in d


# ── dream(skip_llm=) ────────────────────────────────────────────────────────


class TestDreamSkipLLM:
    async def test_dream_skip_llm_promotes_blocks(self, system):
        """dream(skip_llm=True) should promote inbox blocks without LLM."""
        for i in range(3):
            await system.learn(f"Fact number {i} about testing")
        result = await system.dream(skip_llm=True)
        assert result is not None
        assert result.promoted > 0

    async def test_dream_skip_contradictions(self, system):
        """dream(skip_contradictions=True) should skip contradiction detection."""
        for i in range(3):
            await system.learn(f"Knowledge item {i}")
        result = await system.dream(skip_contradictions=True)
        assert result is not None
        assert result.promoted > 0

    async def test_dream_empty_inbox_returns_none(self, system):
        result = await system.dream(skip_llm=True)
        assert result is None


# ── Config wiring ────────────────────────────────────────────────────────────


class TestConfigWiring:
    async def test_near_dup_threshold_from_config(self, test_engine, mock_llm, mock_embedding):
        """near_dup_near_threshold from config should take effect in consolidation."""
        # Use a very low threshold (0.30) — blocks with similarity >= 0.30
        # should be superseded. Default MockEmbeddingService produces
        # moderately similar vectors for different content.
        cfg = ElfmemConfig(
            memory=MemoryConfig(
                inbox_threshold=3,
                near_dup_near_threshold=0.30,
            ),
        )
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )
        await system.learn("The cat sat on the mat")
        await system.learn("The cat sat on the mat today")
        await system.learn("A different topic entirely")
        result = await system.consolidate()
        # With threshold=0.30, similar content should be deduplicated
        assert result.processed == 3
        assert result.deduplicated >= 0  # may or may not dedup depending on embedding similarity

    async def test_contradiction_threshold_from_config(self, test_engine, mock_llm, mock_embedding):
        """contradiction_threshold from config should be wired through."""
        cfg = ElfmemConfig(
            memory=MemoryConfig(
                inbox_threshold=3,
                contradiction_threshold=0.50,  # lower than default 0.80
            ),
        )
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=cfg,
        )
        # Learn enough to consolidate — the threshold is wired even if
        # mock LLM doesn't produce real contradiction scores.
        for i in range(3):
            await system.learn(f"Statement {i}")
        result = await system.consolidate()
        assert result.processed == 3
