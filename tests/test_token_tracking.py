"""Tests for token usage tracking: TokenUsage, TokenCounter, MemorySystem integration.

Sections:
  - TestTokenUsage            — public dataclass behaviour (no DB)
  - TestTokenCounter          — mutable accumulator (internal, tested because session
                                management depends on it directly)
  - TestStatusTokenFields     — status() surfaces correct token snapshot
  - TestTokenPersistence      — lifetime tokens survive across sessions
  - TestLiteLLMAdapterTokenRecording    — LLM adapter records tokens
  - TestLiteLLMEmbeddingAdapterTokenRecording — embedding adapter records tokens
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from elfmem.adapters.litellm import LiteLLMAdapter, LiteLLMEmbeddingAdapter
from elfmem.adapters.models import BlockAnalysisModel
from elfmem.api import MemorySystem
from elfmem.token_counter import TokenCounter
from elfmem.types import TokenUsage

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with mock adapters — no token counter."""
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
    )


# ── TokenUsage — public dataclass ─────────────────────────────────────────────


class TestTokenUsage:
    def test_llm_total_is_sum_of_input_output(self):
        u = TokenUsage(llm_input_tokens=400, llm_output_tokens=60, llm_calls=1)
        assert u.llm_total_tokens == 460

    def test_total_tokens_includes_embedding(self):
        u = TokenUsage(
            llm_input_tokens=100, llm_output_tokens=20,
            embedding_tokens=50, llm_calls=1, embedding_calls=1,
        )
        assert u.total_tokens == 170

    def test_add_combines_all_fields(self):
        a = TokenUsage(
            llm_input_tokens=100, llm_output_tokens=20, embedding_tokens=50,
            llm_calls=2, embedding_calls=3,
        )
        b = TokenUsage(
            llm_input_tokens=200, llm_output_tokens=40, embedding_tokens=100,
            llm_calls=1, embedding_calls=2,
        )
        c = a + b
        assert c.llm_input_tokens == 300
        assert c.llm_output_tokens == 60
        assert c.embedding_tokens == 150
        assert c.llm_calls == 3
        assert c.embedding_calls == 5

    def test_add_with_zero_is_identity(self):
        u = TokenUsage(llm_input_tokens=100, llm_output_tokens=20, llm_calls=1)
        assert u + TokenUsage() == u
        assert TokenUsage() + u == u

    def test_frozen_cannot_be_mutated(self):
        u = TokenUsage(llm_calls=1)
        with pytest.raises(AttributeError):
            u.llm_calls = 2  # type: ignore[misc]

    def test_to_dict_round_trips_via_constructor(self):
        u = TokenUsage(
            llm_input_tokens=100, llm_output_tokens=20, embedding_tokens=50,
            llm_calls=2, embedding_calls=3,
        )
        assert TokenUsage(**u.to_dict()) == u


# ── TokenCounter — mutable accumulator ────────────────────────────────────────


class TestTokenCounter:
    def test_initial_snapshot_is_all_zeros(self):
        assert TokenCounter().snapshot() == TokenUsage()

    def test_record_llm_accumulates_input_output_and_calls(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=20)
        snap = counter.snapshot()
        assert snap.llm_input_tokens == 100
        assert snap.llm_output_tokens == 20
        assert snap.llm_calls == 1

    def test_record_embedding_accumulates_tokens_and_calls(self):
        counter = TokenCounter()
        counter.record_embedding(tokens=50)
        snap = counter.snapshot()
        assert snap.embedding_tokens == 50
        assert snap.embedding_calls == 1

    def test_reset_returns_snapshot_and_zeros_counter(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=10)
        snap = counter.reset()
        assert snap.llm_input_tokens == 100
        assert counter.snapshot() == TokenUsage()


# ── status() token fields ──────────────────────────────────────────────────────


class TestStatusTokenFields:
    @pytest.mark.asyncio
    async def test_status_returns_token_usage_instances(self, system):
        result = await system.status()
        assert isinstance(result.session_tokens, TokenUsage)
        assert isinstance(result.lifetime_tokens, TokenUsage)

    @pytest.mark.asyncio
    async def test_status_session_tokens_zero_when_no_counter(self, system):
        result = await system.status()
        assert result.session_tokens == TokenUsage()

    @pytest.mark.asyncio
    async def test_status_lifetime_tokens_zero_on_fresh_db(self, system):
        result = await system.status()
        assert result.lifetime_tokens == TokenUsage()

    @pytest.mark.asyncio
    async def test_status_reads_session_tokens_from_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            token_counter=counter,
        )
        counter.record_llm(input_tokens=100, output_tokens=20)
        counter.record_embedding(tokens=50)
        result = await sys.status()
        assert result.session_tokens.llm_input_tokens == 100
        assert result.session_tokens.llm_output_tokens == 20
        assert result.session_tokens.embedding_tokens == 50

    @pytest.mark.asyncio
    async def test_status_snapshot_does_not_alter_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            token_counter=counter,
        )
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.status()
        assert counter.snapshot().llm_calls == 1  # not reset by status()


# ── Token persistence across sessions ─────────────────────────────────────────


class TestTokenPersistence:
    @pytest.mark.asyncio
    async def test_end_session_persists_lifetime_tokens_to_db(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=200, output_tokens=30)
        counter.record_embedding(tokens=80)
        await sys.end_session()

        result = await sys.status()
        assert result.lifetime_tokens.llm_input_tokens == 200
        assert result.lifetime_tokens.embedding_tokens == 80
        assert result.lifetime_tokens.llm_calls == 1

    @pytest.mark.asyncio
    async def test_lifetime_tokens_accumulate_across_sessions(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.end_session()

        await sys.begin_session()
        counter.record_llm(input_tokens=200, output_tokens=20)
        await sys.end_session()

        result = await sys.status()
        assert result.lifetime_tokens.llm_input_tokens == 300
        assert result.lifetime_tokens.llm_calls == 2

    @pytest.mark.asyncio
    async def test_begin_session_resets_session_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.end_session()

        await sys.begin_session()
        result = await sys.status()
        assert result.session_tokens == TokenUsage()  # fresh for new session
        await sys.end_session()

    @pytest.mark.asyncio
    async def test_idempotent_begin_session_does_not_reset_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.begin_session()  # idempotent — must not reset

        result = await sys.status()
        assert result.session_tokens.llm_calls == 1  # still recorded
        await sys.end_session()


# ── LiteLLMAdapter token recording ────────────────────────────────────────────


class TestLiteLLMAdapterTokenRecording:
    @pytest.mark.asyncio
    async def test_records_llm_tokens_when_counter_provided(self):
        counter = TokenCounter()
        adapter = LiteLLMAdapter(model="gpt-4o-mini", token_counter=counter)

        mock_completion = MagicMock()
        mock_completion.usage.prompt_tokens = 100
        mock_completion.usage.completion_tokens = 20
        adapter._client.chat.completions.create_with_completion = AsyncMock(
            return_value=(
                BlockAnalysisModel(alignment_score=0.8, tags=[], summary="test"),
                mock_completion,
            )
        )

        await adapter.process_block("block content", "self context")
        snap = counter.snapshot()
        assert snap.llm_input_tokens == 100
        assert snap.llm_output_tokens == 20
        assert snap.llm_calls == 1

    @pytest.mark.asyncio
    async def test_skips_recording_when_no_counter(self):
        adapter = LiteLLMAdapter(model="gpt-4o-mini")
        mock_completion = MagicMock()
        mock_completion.usage.prompt_tokens = 100
        mock_completion.usage.completion_tokens = 20
        adapter._client.chat.completions.create_with_completion = AsyncMock(
            return_value=(
                BlockAnalysisModel(alignment_score=0.8, tags=[], summary="test"),
                mock_completion,
            )
        )
        await adapter.process_block("block content", "self context")  # must not raise

    @pytest.mark.asyncio
    async def test_handles_none_usage_gracefully(self):
        counter = TokenCounter()
        adapter = LiteLLMAdapter(model="gpt-4o-mini", token_counter=counter)
        mock_completion = MagicMock()
        mock_completion.usage = None
        adapter._client.chat.completions.create_with_completion = AsyncMock(
            return_value=(
                BlockAnalysisModel(alignment_score=0.8, tags=[], summary="test"),
                mock_completion,
            )
        )
        await adapter.process_block("block content", "self context")
        assert counter.snapshot() == TokenUsage()  # nothing recorded


# ── LiteLLMEmbeddingAdapter token recording ───────────────────────────────────


class TestLiteLLMEmbeddingAdapterTokenRecording:
    @pytest.mark.asyncio
    async def test_records_embedding_tokens_when_counter_provided(self):
        counter = TokenCounter()
        adapter = LiteLLMEmbeddingAdapter(model="text-embedding-3-small", token_counter=counter)
        mock_response = MagicMock()
        mock_response.usage.prompt_tokens = 15
        mock_response.data = [{"embedding": [0.1] * 1536}]
        with patch(
            "elfmem.adapters.litellm.litellm.aembedding",
            new=AsyncMock(return_value=mock_response),
        ):
            await adapter.embed("test text")
        snap = counter.snapshot()
        assert snap.embedding_tokens == 15
        assert snap.embedding_calls == 1

    @pytest.mark.asyncio
    async def test_skips_recording_when_no_counter(self):
        adapter = LiteLLMEmbeddingAdapter(model="text-embedding-3-small")
        mock_response = MagicMock()
        mock_response.usage.prompt_tokens = 15
        mock_response.data = [{"embedding": [0.1] * 1536}]
        with patch(
            "elfmem.adapters.litellm.litellm.aembedding",
            new=AsyncMock(return_value=mock_response),
        ):
            await adapter.embed("test text")  # must not raise

    @pytest.mark.asyncio
    async def test_handles_none_usage_gracefully(self):
        counter = TokenCounter()
        adapter = LiteLLMEmbeddingAdapter(model="text-embedding-3-small", token_counter=counter)
        mock_response = MagicMock()
        mock_response.usage = None
        mock_response.data = [{"embedding": [0.1] * 1536}]
        with patch(
            "elfmem.adapters.litellm.litellm.aembedding",
            new=AsyncMock(return_value=mock_response),
        ):
            await adapter.embed("test text")
        assert counter.snapshot() == TokenUsage()  # nothing recorded
