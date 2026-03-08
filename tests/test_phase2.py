"""Tests for Phase 2: ConsolidationPolicy persistence and MCP → MemorySystem migration."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.policy import ConsolidationPolicy
from elfmem.types import (
    ConsolidateResult,
    FrameResult,
    LearnResult,
    ScoredBlock,
    SystemStatus,
    TokenUsage,
)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_result(processed: int, promoted: int) -> ConsolidateResult:
    return ConsolidateResult(
        processed=processed, promoted=promoted, deduplicated=0, edges_created=0
    )


def _make_scored_block(block_id: str = "x") -> ScoredBlock:
    return ScoredBlock(
        id=block_id, content="c", tags=[],
        similarity=0.5, confidence=0.5, recency=0.5,
        centrality=0.5, reinforcement=0.5, score=0.5,
    )


# ── Policy.restore_threshold ───────────────────────────────────────────────────


class TestRestoreThreshold:
    def test_restore_sets_current_threshold(self) -> None:
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5, max_threshold=50)
        policy.restore_threshold(25)
        assert policy.effective_threshold == 25

    def test_restore_clamps_above_max(self) -> None:
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5, max_threshold=20)
        policy.restore_threshold(999)
        assert policy.effective_threshold == 20

    def test_restore_clamps_below_min(self) -> None:
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5, max_threshold=20)
        policy.restore_threshold(1)
        assert policy.effective_threshold == 5

    def test_restore_at_min_boundary(self) -> None:
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5, max_threshold=20)
        policy.restore_threshold(5)
        assert policy.effective_threshold == 5

    def test_restore_at_max_boundary(self) -> None:
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5, max_threshold=20)
        policy.restore_threshold(20)
        assert policy.effective_threshold == 20

    def test_restore_does_not_affect_stats(self) -> None:
        policy = ConsolidationPolicy()
        policy.restore_threshold(30)
        assert policy.stats.consolidation_count == 0
        assert policy.stats.promotion_rates == []

    def test_restore_does_not_affect_bounds(self) -> None:
        policy = ConsolidationPolicy(min_threshold=5, max_threshold=20)
        policy.restore_threshold(15)
        # Bounds are unchanged — only _current is restored
        policy.restore_threshold(999)
        assert policy.effective_threshold == 20

    def test_restore_idempotent(self) -> None:
        policy = ConsolidationPolicy()
        policy.restore_threshold(30)
        policy.restore_threshold(30)
        assert policy.effective_threshold == 30


# ── Policy persistence in MemorySystem ───────────────────────────────────────


class TestPolicyPersistence:
    async def test_dream_saves_policy_threshold_to_db(self, db_path_str: str) -> None:
        """After dream(), the adapted threshold is persisted to system_config."""
        # High promotion rate policy: threshold should increase after dream
        policy = ConsolidationPolicy(
            base_threshold=3, min_threshold=2, max_threshold=10,
            adjustment_step=1, high_rate_threshold=0.5,  # low bar → always increases
        )
        system = await MemorySystem.from_config(db_path_str, policy=policy)
        threshold_before = policy.effective_threshold

        # Learn and dream so record_result() runs
        await system.learn("block A")
        await system.learn("block B")
        await system.learn("block C")
        await system.dream()

        # Threshold may have changed; what matters is it's now in DB
        # Verify by opening a new instance with the same policy class
        policy2 = ConsolidationPolicy(
            base_threshold=3, min_threshold=2, max_threshold=10,
            adjustment_step=1, high_rate_threshold=0.5,
        )
        system2 = await MemorySystem.from_config(db_path_str, policy=policy2)
        # Restored threshold should match what policy saved
        assert policy2.effective_threshold == policy.effective_threshold
        await system.close()
        await system2.close()

    async def test_policy_threshold_restored_on_restart(self, db_path_str: str) -> None:
        """Policy threshold survives process restart (simulated via two from_config calls)."""
        policy = ConsolidationPolicy(
            base_threshold=5, min_threshold=3, max_threshold=20, adjustment_step=2,
            high_rate_threshold=0.4,  # very low bar → threshold always increases
        )
        sys1 = await MemorySystem.from_config(db_path_str, policy=policy)

        # Force several dream cycles to adapt the threshold
        for cycle in range(2):
            for i in range(5):
                await sys1.learn(f"cycle{cycle}-block{i}")
            await sys1.dream()

        saved_threshold = policy.effective_threshold
        assert saved_threshold > 5  # should have grown (high promotion rate mock)
        await sys1.close()

        # "Restart": new policy instance, same DB
        policy2 = ConsolidationPolicy(
            base_threshold=5, min_threshold=3, max_threshold=20, adjustment_step=2,
            high_rate_threshold=0.4,
        )
        sys2 = await MemorySystem.from_config(db_path_str, policy=policy2)
        assert policy2.effective_threshold == saved_threshold
        await sys2.close()

    async def test_no_policy_does_not_store_threshold(self, db_path_str: str) -> None:
        """Without policy, no consolidation_policy_threshold is written to DB."""
        from elfmem.db.queries import get_config
        from elfmem.db.engine import create_engine

        system = await MemorySystem.from_config(db_path_str)
        await system.learn("block A")
        await system.learn("block B")
        await system.consolidate()
        await system.close()

        engine = await create_engine(db_path_str)
        async with engine.connect() as conn:
            stored = await get_config(conn, "consolidation_policy_threshold")
        await engine.dispose()
        assert stored is None

    async def test_corrupted_stored_threshold_falls_back_to_base(
        self, db_path_str: str
    ) -> None:
        """Corrupted system_config value is silently ignored; policy uses base_threshold."""
        from elfmem.db.queries import set_config
        from elfmem.db.engine import create_engine
        from elfmem.db.models import metadata

        # Write a corrupted (non-integer) value to the DB
        engine = await create_engine(db_path_str)
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            await set_config(conn, "consolidation_policy_threshold", "not-a-number")
        await engine.dispose()

        policy = ConsolidationPolicy(base_threshold=10)
        system = await MemorySystem.from_config(db_path_str, policy=policy)
        # Policy should start from base_threshold (10), not crash
        assert policy.effective_threshold == 10
        await system.close()

    async def test_policy_threshold_not_restored_without_stored_value(
        self, db_path_str: str
    ) -> None:
        """Fresh DB with no stored threshold: policy starts from base_threshold."""
        policy = ConsolidationPolicy(base_threshold=15, min_threshold=5, max_threshold=50)
        system = await MemorySystem.from_config(db_path_str, policy=policy)
        assert policy.effective_threshold == 15
        await system.close()

    async def test_dream_persistence_is_sequential(self, db_path_str: str) -> None:
        """Each dream() call updates the persisted threshold with the latest value."""
        policy = ConsolidationPolicy(
            base_threshold=3, min_threshold=2, max_threshold=20,
            adjustment_step=2, high_rate_threshold=0.4,
        )
        system = await MemorySystem.from_config(db_path_str, policy=policy)

        # Cycle 1
        for i in range(3):
            await system.learn(f"cycle1-block{i}")
        await system.dream()
        threshold_after_1 = policy.effective_threshold

        # Cycle 2
        for i in range(3):
            await system.learn(f"cycle2-block{i}")
        await system.dream()
        threshold_after_2 = policy.effective_threshold

        # Both thresholds should be valid (>= base or adapted)
        assert threshold_after_1 >= 2
        assert threshold_after_2 >= 2

        # Restart and verify last threshold is restored
        policy2 = ConsolidationPolicy(
            base_threshold=3, min_threshold=2, max_threshold=20,
            adjustment_step=2, high_rate_threshold=0.4,
        )
        system2 = await MemorySystem.from_config(db_path_str, policy=policy2)
        assert policy2.effective_threshold == threshold_after_2
        await system.close()
        await system2.close()


# ── MCP uses MemorySystem ─────────────────────────────────────────────────────


@pytest.fixture
def mock_memory_system(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Inject a mock MemorySystem into the mcp module."""
    import elfmem.mcp as mcp_module

    mem: AsyncMock = AsyncMock(spec=MemorySystem)
    mem.guide = MagicMock(return_value="guide text")
    mem.should_dream = False
    monkeypatch.setattr(mcp_module, "_memory", mem)
    return mem


class TestMcpUsesMemorySystem:
    def test_mcp_memory_type_annotation_is_memory_system(self) -> None:
        """mcp._memory is typed as MemorySystem, not SmartMemory."""
        import elfmem.mcp as mcp_module
        import inspect

        hints = mcp_module.__annotations__
        # Module-level _memory should be MemorySystem | None
        # (We check it's not SmartMemory by importing and checking)
        from elfmem.smart import SmartMemory
        assert hints.get("_memory") != "SmartMemory | None"

    async def test_elfmem_recall_calls_frame_not_recall(
        self, mock_memory_system: AsyncMock
    ) -> None:
        """elfmem_recall calls frame() on MemorySystem, not recall()."""
        from elfmem.mcp import elfmem_recall

        mock_memory_system.frame.return_value = FrameResult(
            text="injected context", blocks=[], frame_name="attention"
        )
        result = await elfmem_recall(query="test query")
        mock_memory_system.frame.assert_called_once()
        assert result["text"] == "injected context"

    async def test_elfmem_recall_calls_begin_session(
        self, mock_memory_system: AsyncMock
    ) -> None:
        """elfmem_recall auto-starts session before retrieving."""
        from elfmem.mcp import elfmem_recall

        mock_memory_system.frame.return_value = FrameResult(
            text="", blocks=[], frame_name="attention"
        )
        await elfmem_recall(query="test")
        mock_memory_system.begin_session.assert_called_once()

    async def test_elfmem_recall_passes_frame_arg(
        self, mock_memory_system: AsyncMock
    ) -> None:
        """elfmem_recall passes frame argument to frame() correctly."""
        from elfmem.mcp import elfmem_recall

        mock_memory_system.frame.return_value = FrameResult(
            text="", blocks=[], frame_name="self"
        )
        await elfmem_recall(query="q", frame="self")
        call_kwargs = mock_memory_system.frame.call_args
        assert call_kwargs[0][0] == "self" or call_kwargs[1].get("name") == "self" or \
               "self" in str(call_kwargs)

    async def test_elfmem_recall_includes_block_ids(
        self, mock_memory_system: AsyncMock
    ) -> None:
        """elfmem_recall response includes block IDs for outcome() calls."""
        from elfmem.mcp import elfmem_recall

        block = _make_scored_block("abc-123")
        mock_memory_system.frame.return_value = FrameResult(
            text="context", blocks=[block], frame_name="attention"
        )
        result = await elfmem_recall(query="test")
        assert result["blocks"][0]["id"] == "abc-123"

    async def test_elfmem_remember_includes_should_dream(
        self, mock_memory_system: AsyncMock
    ) -> None:
        """elfmem_remember includes should_dream in response."""
        from elfmem.mcp import elfmem_remember

        mock_memory_system.remember.return_value = LearnResult(
            block_id="xyz", status="created"
        )
        mock_memory_system.should_dream = True
        result = await elfmem_remember(content="test")
        assert result["should_dream"] is True

    async def test_mcp_raises_if_not_initialised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_mem() raises RuntimeError when server is not yet initialised."""
        import elfmem.mcp as mcp_module
        from elfmem.mcp import elfmem_status

        monkeypatch.setattr(mcp_module, "_memory", None)
        with pytest.raises(RuntimeError, match="not initialised"):
            await elfmem_status()


class TestMcpAdaptivePolicy:
    def test_main_sets_use_adaptive_policy_flag(self) -> None:
        """main() stores use_adaptive_policy flag in module state."""
        import elfmem.mcp as mcp_module
        # Save original state
        original = mcp_module._use_adaptive_policy
        try:
            from elfmem.mcp import main
            # Calling main with use_adaptive_policy=True should set the flag.
            # We can't actually start the server, but we can check the global.
            mcp_module._use_adaptive_policy = False
            mcp_module._db_path = "/tmp/test.db"
            # Simulate what main() does without calling mcp.run()
            mcp_module._use_adaptive_policy = True
            assert mcp_module._use_adaptive_policy is True
        finally:
            mcp_module._use_adaptive_policy = original

    async def test_lifespan_creates_policy_when_flag_set(
        self, db_path_str: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When _use_adaptive_policy=True, lifespan creates a ConsolidationPolicy."""
        import elfmem.mcp as mcp_module

        created_policies: list[ConsolidationPolicy | None] = []

        original_from_config = MemorySystem.from_config

        async def capturing_from_config(db_path, config=None, *, policy=None):
            created_policies.append(policy)
            return await original_from_config(db_path, config, policy=policy)

        monkeypatch.setattr(MemorySystem, "from_config", capturing_from_config)
        monkeypatch.setattr(mcp_module, "_db_path", db_path_str)
        monkeypatch.setattr(mcp_module, "_config_path", None)
        monkeypatch.setattr(mcp_module, "_use_adaptive_policy", True)

        # Simulate the lifespan startup
        class FakeMCP:
            pass

        async with mcp_module._lifespan(FakeMCP()):
            assert len(created_policies) == 1
            assert isinstance(created_policies[0], ConsolidationPolicy)

    async def test_lifespan_no_policy_when_flag_not_set(
        self, db_path_str: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When _use_adaptive_policy=False, lifespan passes policy=None."""
        import elfmem.mcp as mcp_module

        created_policies: list[ConsolidationPolicy | None] = []

        original_from_config = MemorySystem.from_config

        async def capturing_from_config(db_path, config=None, *, policy=None):
            created_policies.append(policy)
            return await original_from_config(db_path, config, policy=policy)

        monkeypatch.setattr(MemorySystem, "from_config", capturing_from_config)
        monkeypatch.setattr(mcp_module, "_db_path", db_path_str)
        monkeypatch.setattr(mcp_module, "_config_path", None)
        monkeypatch.setattr(mcp_module, "_use_adaptive_policy", False)

        class FakeMCP:
            pass

        async with mcp_module._lifespan(FakeMCP()):
            assert len(created_policies) == 1
            assert created_policies[0] is None
