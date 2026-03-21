"""Tests for dreaming architecture: decoupled learning and consolidation.

Uses MemorySystem directly with mock services and in-memory SQLite.
No real LLM calls, no file I/O.

Key invariants:
  - learn() is fast — never triggers consolidation
  - should_dream is True when pending_count >= effective_threshold
  - dream() consolidates and resets pending
  - dream() is idempotent when nothing is pending
"""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.types import ConsolidateResult

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with a low inbox_threshold (3) for fast cycle tests.

    Uses in-memory SQLite and mock services — no LLM calls, no file I/O.
    """
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    sys = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    yield sys
    await sys.close()


# ── learn() / pending counter ──────────────────────────────────────────────────


class TestLearnDecoupling:
    """learn() stores to inbox without triggering consolidation."""

    @pytest.mark.asyncio
    async def test_learn_does_not_consolidate(self, system):
        """Calling learn() up to and beyond threshold never consolidates."""
        threshold = (await system.status()).inbox_threshold

        for i in range(threshold):
            result = await system.learn(f"Concept {i}")
            assert result.status == "created"

        status = await system.status()
        # All blocks still in inbox — no consolidation happened
        assert status.inbox_count >= threshold
        assert status.pending_count == threshold

    @pytest.mark.asyncio
    async def test_learn_increments_pending(self, system):
        """Each learn() increments pending_count by 1."""
        await system.learn("Block A")
        await system.learn("Block B")
        assert (await system.status()).pending_count == 2


# ── should_dream advisory ──────────────────────────────────────────────────────


class TestShouldDream:
    """should_dream reflects the pending vs threshold comparison."""

    @pytest.mark.asyncio
    async def test_should_dream_false_below_threshold(self, system):
        threshold = (await system.status()).inbox_threshold
        for i in range(threshold - 1):
            await system.learn(f"Block {i}")
        assert not system.should_dream

    @pytest.mark.asyncio
    async def test_should_dream_true_at_threshold(self, system):
        threshold = (await system.status()).inbox_threshold
        for i in range(threshold):
            await system.learn(f"Block {i}")
        assert system.should_dream

    @pytest.mark.asyncio
    async def test_should_dream_false_after_dream(self, system):
        threshold = (await system.status()).inbox_threshold
        for i in range(threshold):
            await system.learn(f"Block {i}")
        assert system.should_dream
        await system.dream()
        assert not system.should_dream


# ── dream() ────────────────────────────────────────────────────────────────────


class TestDream:
    """dream() consolidates pending blocks and resets the counter."""

    @pytest.mark.asyncio
    async def test_dream_returns_none_when_nothing_pending(self, system):
        assert (await system.status()).pending_count == 0
        result = await system.dream()
        assert result is None

    @pytest.mark.asyncio
    async def test_dream_returns_consolidate_result_when_pending(self, system):
        for i in range(3):
            await system.learn(f"Block {i}")
        result = await system.dream()
        assert isinstance(result, ConsolidateResult)

    @pytest.mark.asyncio
    async def test_dream_resets_pending_to_zero(self, system):
        for i in range(3):
            await system.learn(f"Block {i}")
        assert (await system.status()).pending_count > 0
        await system.dream()
        assert (await system.status()).pending_count == 0

    @pytest.mark.asyncio
    async def test_dream_idempotent_second_call_returns_none(self, system):
        """First dream consolidates; second call with no pending returns None."""
        for i in range(3):
            await system.learn(f"Block {i}")
        await system.dream()
        result = await system.dream()
        assert result is None

    @pytest.mark.asyncio
    async def test_dream_processed_count_matches_blocks_learned(self, system):
        for i in range(3):
            await system.learn(f"Unique block content {i}")
        result = await system.dream()
        assert result is not None
        assert result.processed == 3


# ── Full cycle ─────────────────────────────────────────────────────────────────


class TestDreamingCycle:
    """Integration: learn → should_dream → dream, repeated."""

    @pytest.mark.asyncio
    async def test_single_cycle(self, system):
        threshold = (await system.status()).inbox_threshold
        for i in range(threshold):
            await system.learn(f"Concept {i}")
        assert system.should_dream
        result = await system.dream()
        assert isinstance(result, ConsolidateResult)
        assert (await system.status()).pending_count == 0
        assert not system.should_dream

    @pytest.mark.asyncio
    async def test_two_cycles(self, system):
        """Two complete learn→dream cycles work correctly."""
        for cycle in range(2):
            for i in range(3):
                await system.learn(f"Cycle{cycle}-Block{i}")
            assert system.should_dream
            await system.dream()
            assert not system.should_dream
            assert (await system.status()).pending_count == 0
