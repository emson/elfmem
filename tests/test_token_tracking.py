"""Tests for token usage tracking: TokenUsage, TokenCounter, MemorySystem integration.

Tests are organised into:
  - TestTokenUsage       — pure dataclass unit tests (no DB, no adapters)
  - TestTokenCounter     — mutable accumulator unit tests
  - TestStatusTokenFields — status() returns correct token snapshot
  - TestTokenPersistence  — lifetime tokens survive across sessions
  - TestLiteLLMAdapterTokenRecording    — adapter records tokens via mock
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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with mock adapters — no token counter."""
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
    )


@pytest.fixture
async def system_with_counter(test_engine, mock_llm, mock_embedding):
    """MemorySystem with a live TokenCounter injected."""
    counter = TokenCounter()
    sys = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        token_counter=counter,
    )
    return sys, counter


# ── TokenUsage unit tests ─────────────────────────────────────────────────────

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

    def test_str_all_zero_returns_no_usage_message(self):
        u = TokenUsage()
        s = str(u)
        assert "no" in s.lower()

    def test_str_with_llm_and_embed_shows_both(self):
        u = TokenUsage(
            llm_input_tokens=400, llm_output_tokens=60,
            embedding_tokens=300, llm_calls=2, embedding_calls=5,
        )
        s = str(u)
        assert "460" in s    # llm total (400+60)
        assert "2" in s      # llm call count
        assert "300" in s    # embedding tokens
        assert "5" in s      # embedding call count

    def test_str_llm_only_shows_dash_for_embed(self):
        u = TokenUsage(llm_input_tokens=100, llm_output_tokens=10, llm_calls=1)
        s = str(u)
        assert "LLM" in s
        assert "\u2014" in s  # em-dash: no embed calls

    def test_str_embed_only_shows_dash_for_llm(self):
        u = TokenUsage(embedding_tokens=200, embedding_calls=3)
        s = str(u)
        assert "Embed" in s
        assert "\u2014" in s  # em-dash: no LLM calls

    def test_str_formats_large_numbers_with_commas(self):
        u = TokenUsage(llm_input_tokens=10000, llm_output_tokens=500, llm_calls=1)
        assert "10,500" in str(u)

    def test_add_combines_all_fields(self):
        a = TokenUsage(
            llm_input_tokens=100, llm_output_tokens=20,
            embedding_tokens=50, llm_calls=2, embedding_calls=3,
        )
        b = TokenUsage(
            llm_input_tokens=200, llm_output_tokens=40,
            embedding_tokens=100, llm_calls=1, embedding_calls=2,
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

    def test_to_dict_has_all_keys(self):
        u = TokenUsage()
        assert set(u.to_dict().keys()) == {
            "llm_input_tokens", "llm_output_tokens", "embedding_tokens",
            "llm_calls", "embedding_calls",
        }

    def test_to_dict_round_trips_via_constructor(self):
        u = TokenUsage(
            llm_input_tokens=100, llm_output_tokens=20,
            embedding_tokens=50, llm_calls=2, embedding_calls=3,
        )
        assert TokenUsage(**u.to_dict()) == u

    def test_frozen_cannot_be_mutated(self):
        u = TokenUsage(llm_calls=1)
        with pytest.raises(AttributeError):
            u.llm_calls = 2  # type: ignore[misc]

    def test_summary_equals_str(self):
        u = TokenUsage(llm_input_tokens=100, llm_output_tokens=10, llm_calls=1)
        assert u.summary == str(u)


# ── TokenCounter unit tests ───────────────────────────────────────────────────

class TestTokenCounter:
    def test_initial_snapshot_is_all_zeros(self):
        counter = TokenCounter()
        assert counter.snapshot() == TokenUsage()

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

    def test_snapshot_does_not_reset_counter(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=10)
        counter.snapshot()
        assert counter.snapshot().llm_calls == 1  # still recorded

    def test_reset_returns_snapshot_and_zeros_counter(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=10)
        snap = counter.reset()
        assert snap.llm_input_tokens == 100
        assert counter.snapshot() == TokenUsage()  # zeroed

    def test_multiple_llm_records_accumulate(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=10)
        counter.record_llm(input_tokens=200, output_tokens=20)
        snap = counter.snapshot()
        assert snap.llm_input_tokens == 300
        assert snap.llm_output_tokens == 30
        assert snap.llm_calls == 2

    def test_multiple_embedding_records_accumulate(self):
        counter = TokenCounter()
        counter.record_embedding(tokens=50)
        counter.record_embedding(tokens=75)
        snap = counter.snapshot()
        assert snap.embedding_tokens == 125
        assert snap.embedding_calls == 2

    def test_reset_then_new_records_start_fresh(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=10)
        counter.reset()
        counter.record_embedding(tokens=75)
        snap = counter.snapshot()
        assert snap.llm_input_tokens == 0
        assert snap.llm_calls == 0
        assert snap.embedding_tokens == 75

    def test_mixed_records_accumulate_correctly(self):
        counter = TokenCounter()
        counter.record_llm(input_tokens=100, output_tokens=10)
        counter.record_embedding(tokens=50)
        counter.record_llm(input_tokens=200, output_tokens=20)
        counter.record_embedding(tokens=80)
        snap = counter.snapshot()
        assert snap.llm_input_tokens == 300
        assert snap.llm_calls == 2
        assert snap.embedding_tokens == 130
        assert snap.embedding_calls == 2


# ── MemorySystem status() token fields ────────────────────────────────────────

class TestStatusTokenFields:
    @pytest.mark.asyncio
    async def test_status_session_tokens_zero_when_no_counter(self, system):
        result = await system.status()
        assert result.session_tokens == TokenUsage()

    @pytest.mark.asyncio
    async def test_status_lifetime_tokens_zero_on_fresh_db(self, system):
        result = await system.status()
        assert result.lifetime_tokens == TokenUsage()

    @pytest.mark.asyncio
    async def test_status_returns_token_usage_instances(self, system):
        result = await system.status()
        assert isinstance(result.session_tokens, TokenUsage)
        assert isinstance(result.lifetime_tokens, TokenUsage)

    @pytest.mark.asyncio
    async def test_status_reads_session_tokens_from_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )
        counter.record_llm(input_tokens=100, output_tokens=20)
        counter.record_embedding(tokens=50)
        result = await sys.status()
        assert result.session_tokens.llm_input_tokens == 100
        assert result.session_tokens.llm_output_tokens == 20
        assert result.session_tokens.embedding_tokens == 50
        assert result.session_tokens.llm_calls == 1
        assert result.session_tokens.embedding_calls == 1

    @pytest.mark.asyncio
    async def test_status_snapshot_does_not_alter_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.status()
        # status() calls snapshot(), which must not reset the counter
        assert counter.snapshot().llm_calls == 1

    @pytest.mark.asyncio
    async def test_status_lifetime_tokens_zero_before_end_session(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=200, output_tokens=30)
        result = await sys.status()
        # Not yet persisted — only happens at end_session()
        assert result.lifetime_tokens == TokenUsage()
        await sys.end_session()

    @pytest.mark.asyncio
    async def test_status_str_includes_token_line(self, system):
        result = await system.status()
        s = str(result)
        assert "Tokens this session:" in s

    @pytest.mark.asyncio
    async def test_status_to_dict_session_tokens_is_dict(self, system):
        result = await system.status()
        d = result.to_dict()
        assert isinstance(d["session_tokens"], dict)
        assert "llm_calls" in d["session_tokens"]

    @pytest.mark.asyncio
    async def test_status_to_dict_lifetime_tokens_is_dict(self, system):
        result = await system.status()
        d = result.to_dict()
        assert isinstance(d["lifetime_tokens"], dict)
        assert "embedding_calls" in d["lifetime_tokens"]


# ── Token persistence across sessions ─────────────────────────────────────────

class TestTokenPersistence:
    @pytest.mark.asyncio
    async def test_end_session_persists_lifetime_tokens_to_db(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=200, output_tokens=30)
        counter.record_embedding(tokens=80)
        await sys.end_session()

        result = await sys.status()
        assert result.lifetime_tokens.llm_input_tokens == 200
        assert result.lifetime_tokens.llm_output_tokens == 30
        assert result.lifetime_tokens.embedding_tokens == 80
        assert result.lifetime_tokens.llm_calls == 1
        assert result.lifetime_tokens.embedding_calls == 1

    @pytest.mark.asyncio
    async def test_lifetime_tokens_accumulate_across_sessions(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )

        # Session 1
        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.end_session()

        # Session 2
        await sys.begin_session()
        counter.record_llm(input_tokens=200, output_tokens=20)
        await sys.end_session()

        result = await sys.status()
        assert result.lifetime_tokens.llm_input_tokens == 300
        assert result.lifetime_tokens.llm_output_tokens == 30
        assert result.lifetime_tokens.llm_calls == 2

    @pytest.mark.asyncio
    async def test_begin_session_resets_session_counter(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )

        # Session 1 — accumulate tokens
        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.end_session()

        # Session 2 — counter resets on begin
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
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )

        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.begin_session()  # idempotent — must not reset

        result = await sys.status()
        assert result.session_tokens.llm_calls == 1  # still present
        await sys.end_session()

    @pytest.mark.asyncio
    async def test_end_session_without_active_session_does_not_corrupt_lifetime(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )
        await sys.end_session()  # no active session — safe, returns 0.0
        result = await sys.status()
        assert result.lifetime_tokens == TokenUsage()

    @pytest.mark.asyncio
    async def test_session_tokens_zero_after_end_session(
        self, test_engine, mock_llm, mock_embedding
    ):
        counter = TokenCounter()
        sys = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter,
        )
        await sys.begin_session()
        counter.record_llm(input_tokens=100, output_tokens=10)
        await sys.end_session()

        # Counter was reset by end_session — session_tokens is now zero
        result = await sys.status()
        assert result.session_tokens == TokenUsage()

    @pytest.mark.asyncio
    async def test_lifetime_tokens_survived_across_independent_system_instances(
        self, test_engine, mock_llm, mock_embedding
    ):
        """Lifetime tokens persisted to DB are visible from a new MemorySystem."""
        counter1 = TokenCounter()
        sys1 = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter1,
        )
        await sys1.begin_session()
        counter1.record_llm(input_tokens=300, output_tokens=25)
        await sys1.end_session()

        # Second instance reads from same DB — same engine for test isolation
        counter2 = TokenCounter()
        sys2 = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, token_counter=counter2,
        )
        result = await sys2.status()
        assert result.lifetime_tokens.llm_input_tokens == 300


# ── LiteLLMAdapter token recording ───────────────────────────────────────────

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
        adapter = LiteLLMAdapter(model="gpt-4o-mini")  # no token_counter

        mock_completion = MagicMock()
        mock_completion.usage.prompt_tokens = 100
        mock_completion.usage.completion_tokens = 20
        adapter._client.chat.completions.create_with_completion = AsyncMock(
            return_value=(
                BlockAnalysisModel(alignment_score=0.8, tags=[], summary="test"),
                mock_completion,
            )
        )

        # Must not raise — adapters work fine without a counter
        await adapter.process_block("block content", "self context")

    @pytest.mark.asyncio
    async def test_handles_none_usage_gracefully(self):
        counter = TokenCounter()
        adapter = LiteLLMAdapter(model="gpt-4o-mini", token_counter=counter)

        mock_completion = MagicMock()
        mock_completion.usage = None  # provider returns no usage object
        adapter._client.chat.completions.create_with_completion = AsyncMock(
            return_value=(
                BlockAnalysisModel(alignment_score=0.8, tags=[], summary="test"),
                mock_completion,
            )
        )

        await adapter.process_block("block content", "self context")
        assert counter.snapshot() == TokenUsage()  # nothing recorded

    @pytest.mark.asyncio
    async def test_handles_none_token_fields_records_zero_with_call_counted(self):
        counter = TokenCounter()
        adapter = LiteLLMAdapter(model="gpt-4o-mini", token_counter=counter)

        mock_completion = MagicMock()
        mock_completion.usage.prompt_tokens = None     # field is None, not missing
        mock_completion.usage.completion_tokens = None
        adapter._client.chat.completions.create_with_completion = AsyncMock(
            return_value=(
                BlockAnalysisModel(alignment_score=0.8, tags=[], summary="test"),
                mock_completion,
            )
        )

        await adapter.process_block("block content", "self context")
        snap = counter.snapshot()
        assert snap.llm_input_tokens == 0   # guarded to 0
        assert snap.llm_output_tokens == 0  # guarded to 0
        assert snap.llm_calls == 1          # call did happen

    @pytest.mark.asyncio
    async def test_both_call_sites_record_independently(self):
        """Both LLM methods (process_block + detect_contradiction) record to the same counter."""
        from elfmem.adapters.models import ContradictionScore

        counter = TokenCounter()
        adapter = LiteLLMAdapter(model="gpt-4o-mini", token_counter=counter)

        def _make_completion(prompt: int, completion: int) -> MagicMock:
            m = MagicMock()
            m.usage.prompt_tokens = prompt
            m.usage.completion_tokens = completion
            return m

        adapter._client.chat.completions.create_with_completion = AsyncMock(
            side_effect=[
                (
                    BlockAnalysisModel(alignment_score=0.8, tags=[], summary="summary"),
                    _make_completion(100, 10),
                ),
                (ContradictionScore(score=0.1), _make_completion(200, 5)),
            ]
        )

        await adapter.process_block("block", "ctx")
        await adapter.detect_contradiction("block_a", "block_b")

        snap = counter.snapshot()
        assert snap.llm_calls == 2
        assert snap.llm_input_tokens == 300   # 100+200
        assert snap.llm_output_tokens == 15   # 10+5


# ── LiteLLMEmbeddingAdapter token recording ───────────────────────────────────

class TestLiteLLMEmbeddingAdapterTokenRecording:
    @pytest.mark.asyncio
    async def test_records_embedding_tokens_when_counter_provided(self):
        counter = TokenCounter()
        adapter = LiteLLMEmbeddingAdapter(
            model="text-embedding-3-small", token_counter=counter
        )

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
            await adapter.embed("test text")  # no counter, must not raise

    @pytest.mark.asyncio
    async def test_handles_none_usage_gracefully(self):
        counter = TokenCounter()
        adapter = LiteLLMEmbeddingAdapter(
            model="text-embedding-3-small", token_counter=counter
        )

        mock_response = MagicMock()
        mock_response.usage = None
        mock_response.data = [{"embedding": [0.1] * 1536}]

        with patch(
            "elfmem.adapters.litellm.litellm.aembedding",
            new=AsyncMock(return_value=mock_response),
        ):
            await adapter.embed("test text")

        assert counter.snapshot() == TokenUsage()  # nothing recorded

    @pytest.mark.asyncio
    async def test_handles_none_prompt_tokens_gracefully(self):
        counter = TokenCounter()
        adapter = LiteLLMEmbeddingAdapter(
            model="text-embedding-3-small", token_counter=counter
        )

        mock_response = MagicMock()
        mock_response.usage.prompt_tokens = None   # Ollama-style: field is None
        mock_response.data = [{"embedding": [0.1] * 1536}]

        with patch(
            "elfmem.adapters.litellm.litellm.aembedding",
            new=AsyncMock(return_value=mock_response),
        ):
            await adapter.embed("test text")

        snap = counter.snapshot()
        assert snap.embedding_tokens == 0   # guarded to 0
        assert snap.embedding_calls == 1    # call still counted

    @pytest.mark.asyncio
    async def test_multiple_embeds_accumulate(self):
        counter = TokenCounter()
        adapter = LiteLLMEmbeddingAdapter(
            model="text-embedding-3-small", token_counter=counter
        )

        def _make_response(tokens: int) -> MagicMock:
            m = MagicMock()
            m.usage.prompt_tokens = tokens
            m.data = [{"embedding": [0.1] * 1536}]
            return m

        with patch(
            "elfmem.adapters.litellm.litellm.aembedding",
            new=AsyncMock(side_effect=[_make_response(10), _make_response(15)]),
        ):
            await adapter.embed("first text")
            await adapter.embed("second text")

        snap = counter.snapshot()
        assert snap.embedding_tokens == 25
        assert snap.embedding_calls == 2
