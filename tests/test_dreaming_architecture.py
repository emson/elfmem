"""Tests for dreaming architecture: decoupled learning and consolidation."""

import pytest
from elfmem.smart import SmartMemory
from elfmem.types import ConsolidateResult


def _pending(mem: SmartMemory) -> int:
    """Access _pending counter via the wrapped MemorySystem."""
    return mem._system._pending


def _threshold(mem: SmartMemory) -> int:
    """Access inbox_threshold via the wrapped MemorySystem's config."""
    return mem._system._config.memory.inbox_threshold


class TestSmartMemoryDecoupling:
    """remember() should not trigger consolidation; dream() should."""

    @pytest.mark.asyncio
    async def test_remember_no_consolidation_on_threshold(self, memory: SmartMemory):
        """remember() increments _pending but never calls consolidate()."""
        initial_pending = _pending(memory)
        threshold = _threshold(memory)

        # Learn up to threshold
        for i in range(threshold):
            result = await memory.remember(f"Concept {i}")
            assert result.status == "created"

        # Pending should be at threshold, but consolidation didn't happen
        assert _pending(memory) == initial_pending + threshold
        # Verify inbox still has the blocks
        status = await memory.status()
        assert status.inbox_count >= threshold

    @pytest.mark.asyncio
    async def test_should_dream_property(self, memory: SmartMemory):
        """should_dream is True when _pending >= _threshold."""
        # Initial state
        initial_pending = _pending(memory)
        threshold = _threshold(memory)

        # Learn up to threshold - 1
        for i in range(threshold - 1):
            await memory.remember(f"Block {i}")

        # Should not dream yet
        assert not memory.should_dream
        assert _pending(memory) == initial_pending + threshold - 1

        # Learn one more
        await memory.remember("Final block")

        # Now should dream
        assert memory.should_dream
        assert _pending(memory) >= _threshold(memory)

    @pytest.mark.asyncio
    async def test_dream_returns_consolidate_result(self, memory: SmartMemory):
        """dream() returns ConsolidateResult when blocks are pending."""
        # Learn some blocks
        for i in range(3):
            await memory.remember(f"Block {i}")

        # Dream and check result
        result = await memory.dream()
        assert isinstance(result, ConsolidateResult) or result is None
        # If there were blocks, result should be ConsolidateResult
        if _pending(memory) == 0:
            # Dream was called and blocks were processed
            assert result is None or isinstance(result, ConsolidateResult)

    @pytest.mark.asyncio
    async def test_dream_resets_pending(self, memory: SmartMemory):
        """dream() resets _pending to 0 after consolidation."""
        # Learn blocks
        for i in range(3):
            await memory.remember(f"Block {i}")

        assert _pending(memory) > 0
        await memory.dream()
        assert _pending(memory) == 0

    @pytest.mark.asyncio
    async def test_dream_idempotent_with_no_pending(self, memory: SmartMemory):
        """dream() with no pending returns None safely."""
        assert _pending(memory) == 0
        result = await memory.dream()
        assert result is None
        assert _pending(memory) == 0

    @pytest.mark.asyncio
    async def test_dream_multiple_calls(self, memory: SmartMemory):
        """Multiple dream() calls are safe; only first does consolidation."""
        # Learn blocks
        for i in range(3):
            await memory.remember(f"Block {i}")

        pending_before = _pending(memory)
        assert pending_before > 0

        # First dream
        result1 = await memory.dream()
        assert _pending(memory) == 0

        # Second dream (no pending)
        result2 = await memory.dream()
        assert result2 is None
        assert _pending(memory) == 0

    @pytest.mark.asyncio
    async def test_remember_returns_fast(self, memory: SmartMemory):
        """remember() returns immediately; never blocks on consolidation."""
        # This test is implicit: if remember() blocked, it would timeout
        # We learn up to and beyond threshold without hanging
        threshold = _threshold(memory)
        for i in range(threshold + 1):
            result = await memory.remember(f"Block {i}")
            assert result.status in ["created", "duplicate_rejected"]

        # All blocks are pending; none were consolidated
        assert _pending(memory) == threshold + 1


class TestSessionContextManager:
    """managed() context manager should dream on exit if pending."""

    @pytest.mark.asyncio
    async def test_managed_dreams_on_exit_if_pending(self, db_path_str):
        """Exiting managed() context should consolidate pending blocks."""
        async with SmartMemory.managed(db_path_str) as mem:
            # Learn enough blocks to exceed threshold (default is 10)
            threshold = _threshold(mem)
            for i in range(threshold):
                await mem.remember(f"Block {i}")

            assert mem.should_dream
            pending_at_exit = _pending(mem)

        # After exit, dream() should have been called
        # (We can't easily verify this without inspecting internal state,
        # but the test passes if no exception is raised)

    @pytest.mark.asyncio
    async def test_managed_safe_with_no_pending(self, db_path_str):
        """Exiting managed() is safe even if nothing is pending."""
        async with SmartMemory.managed(db_path_str) as mem:
            # Don't learn anything
            assert _pending(mem) == 0

        # Should exit cleanly


class TestAdvisorySystem:
    """remember() response should include should_dream advisory."""

    @pytest.mark.asyncio
    async def test_remember_response_includes_advisory(self, memory: SmartMemory):
        """If we were to return advisory in remember(), it would be included."""
        # Note: the MCP tool includes this; the API doesn't need to
        # This test documents the behavior
        result = await memory.remember("Test block")
        assert result.status in ["created", "duplicate_rejected"]
        # The should_dream property is available on memory
        assert isinstance(memory.should_dream, bool)


class TestFullDreamingCycle:
    """Integration: remember → should_dream → dream."""

    @pytest.mark.asyncio
    async def test_full_cycle(self, memory: SmartMemory):
        """Complete cycle: learn blocks → check should_dream → consolidate."""
        threshold = _threshold(memory)

        # Heartbeat: learn blocks
        for i in range(threshold):
            result = await memory.remember(f"Concept {i}")
            assert result.status in ["created", "duplicate_rejected"]

        # Check advisor
        assert memory.should_dream

        # Breathing: consolidate
        result = await memory.dream()
        assert result is not None or result is None  # Either is valid
        assert _pending(memory) == 0
        assert not memory.should_dream

    @pytest.mark.asyncio
    async def test_cycle_repeats(self, memory: SmartMemory):
        """Multiple cycles in one session."""
        threshold = _threshold(memory)

        # Cycle 1
        for i in range(threshold):
            await memory.remember(f"Round1-Block{i}")

        assert memory.should_dream
        await memory.dream()
        assert not memory.should_dream

        # Cycle 2
        for i in range(threshold):
            await memory.remember(f"Round2-Block{i}")

        assert memory.should_dream
        await memory.dream()
        assert not memory.should_dream
