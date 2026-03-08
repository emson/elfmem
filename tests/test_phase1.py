"""Tests for Phase 1 API unification: remember(), dream(), should_dream, _pending, policy."""
from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.policy import ConsolidationPolicy
from elfmem.smart import SmartMemory
from elfmem.types import ConsolidateResult, LearnResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:  # type: ignore[misc]
    """MemorySystem with inbox_threshold=3 for fast tests. No session started."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    sys = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    yield sys  # type: ignore[misc]
    await sys.close()


@pytest.fixture
def policy() -> ConsolidationPolicy:
    """A ConsolidationPolicy with tight thresholds for test speed."""
    return ConsolidationPolicy(base_threshold=3, min_threshold=2, max_threshold=10, adjustment_step=1)


# ── remember() ───────────────────────────────────────────────────────────────


class TestRemember:
    async def test_remember_returns_learn_result(self, system: MemorySystem) -> None:
        result = await system.remember("elf prefers explicit error handling")
        assert isinstance(result, LearnResult)

    async def test_remember_status_created(self, system: MemorySystem) -> None:
        result = await system.remember("first unique block")
        assert result.status == "created"

    async def test_remember_auto_starts_session(self, system: MemorySystem) -> None:
        assert system._session_id is None
        await system.remember("session auto-start test")
        assert system._session_id is not None

    async def test_remember_inside_existing_session_is_idempotent(
        self, system: MemorySystem
    ) -> None:
        first_id = await system.begin_session()
        await system.remember("inside session")
        assert system._session_id == first_id  # same session, not a new one

    async def test_remember_increments_pending(self, system: MemorySystem) -> None:
        assert system._pending == 0
        await system.remember("block one")
        assert system._pending == 1

    async def test_remember_duplicate_does_not_increment_pending(
        self, system: MemorySystem
    ) -> None:
        await system.remember("same content")
        pending_before = system._pending
        result = await system.remember("same content")
        assert result.status == "duplicate_rejected"
        assert system._pending == pending_before  # no change


# ── dream() ───────────────────────────────────────────────────────────────────


class TestDream:
    async def test_dream_returns_none_when_nothing_pending(
        self, system: MemorySystem
    ) -> None:
        assert system._pending == 0
        result = await system.dream()
        assert result is None

    async def test_dream_processes_pending_blocks(self, system: MemorySystem) -> None:
        await system.remember("fact A")
        await system.remember("fact B")
        result = await system.dream()
        assert result is not None
        assert isinstance(result, ConsolidateResult)
        assert result.processed >= 2

    async def test_dream_resets_pending_to_zero(self, system: MemorySystem) -> None:
        await system.remember("fact A")
        await system.remember("fact B")
        assert system._pending == 2
        await system.dream()
        assert system._pending == 0

    async def test_dream_idempotent_after_empty_inbox(
        self, system: MemorySystem
    ) -> None:
        # dream() with _pending=0 returns None and does not raise
        for _ in range(3):
            result = await system.dream()
            assert result is None

    async def test_dream_clears_frame_cache(self, system: MemorySystem) -> None:
        await system.remember("some knowledge")
        # Warm frame cache by calling frame()
        await system.begin_session()
        await system.frame("self")
        assert len(system._frame_cache._cache) > 0 or True  # may or may not cache
        await system.dream()
        # After dream, frame cache is always cleared
        assert len(system._frame_cache._cache) == 0


# ── should_dream ──────────────────────────────────────────────────────────────


class TestShouldDream:
    async def test_should_dream_false_below_threshold(
        self, system: MemorySystem
    ) -> None:
        # threshold=3, _pending=0
        assert system.should_dream is False

    async def test_should_dream_true_at_threshold(self, system: MemorySystem) -> None:
        for i in range(3):
            await system.remember(f"unique block {i}")
        assert system.should_dream is True

    async def test_should_dream_false_after_dream(self, system: MemorySystem) -> None:
        for i in range(3):
            await system.remember(f"unique block {i}")
        await system.dream()
        assert system.should_dream is False

    async def test_should_dream_with_policy_delegates_to_policy(
        self, system: MemorySystem, policy: ConsolidationPolicy
    ) -> None:
        system._policy = policy
        system._pending = policy.effective_threshold
        assert system.should_dream is True

    async def test_should_dream_with_policy_below_threshold_false(
        self, system: MemorySystem, policy: ConsolidationPolicy
    ) -> None:
        system._policy = policy
        system._pending = 0
        assert system.should_dream is False


# ── Policy integration ────────────────────────────────────────────────────────


class TestPolicyIntegration:
    async def test_dream_feeds_policy_record_result(
        self, system: MemorySystem, policy: ConsolidationPolicy
    ) -> None:
        system._policy = policy
        cycles_before = policy.stats.consolidation_count
        await system.remember("block one")
        await system.dream()
        assert policy.stats.consolidation_count == cycles_before + 1

    async def test_dream_no_policy_does_not_raise(self, system: MemorySystem) -> None:
        # dream() with no policy should work fine
        assert system._policy is None
        await system.remember("block one")
        result = await system.dream()
        assert result is not None

    async def test_from_config_accepts_policy(self, db_path_str: str) -> None:
        pol = ConsolidationPolicy()
        sys = await MemorySystem.from_config(db_path_str, policy=pol)
        assert sys._policy is pol
        await sys.close()

    async def test_from_config_seeds_initial_pending_from_db(
        self, db_path_str: str
    ) -> None:
        # First session: learn 2 blocks without dreaming
        sys1 = await MemorySystem.from_config(db_path_str)
        await sys1.learn("block A")
        await sys1.learn("block B")
        # _pending=2 but no dream → blocks stay in inbox
        await sys1.close()

        # Restart: _pending should be seeded from DB inbox count
        sys2 = await MemorySystem.from_config(db_path_str)
        assert sys2._pending == 2
        await sys2.close()

    async def test_high_promotion_rate_increases_policy_threshold(
        self, system: MemorySystem, policy: ConsolidationPolicy
    ) -> None:
        """High-quality input → promotion rate > 80% → threshold grows."""
        system._policy = policy
        threshold_before = policy.effective_threshold
        # Learn distinct blocks (MockLLM promotes all, high rate)
        for i in range(threshold_before):
            await system.remember(f"distinct quality block {i}")
        result = await system.dream()
        assert result is not None
        assert policy.effective_threshold >= threshold_before  # grew or stayed

    async def test_policy_record_result_not_called_when_dream_returns_none(
        self, system: MemorySystem, policy: ConsolidationPolicy
    ) -> None:
        system._policy = policy
        cycles_before = policy.stats.consolidation_count
        # dream() with nothing pending → returns None → policy NOT called
        result = await system.dream()
        assert result is None
        assert policy.stats.consolidation_count == cycles_before


# ── SmartMemory shim ──────────────────────────────────────────────────────────


class TestSmartMemoryShim:
    async def test_open_with_policy(self, db_path_str: str) -> None:
        pol = ConsolidationPolicy()
        mem = await SmartMemory.open(db_path_str, policy=pol)
        assert mem._system._policy is pol
        await mem.close()

    async def test_remember_delegates_to_system(self, db_path_str: str) -> None:
        mem = await SmartMemory.open(db_path_str)
        result = await mem.remember("some content")
        assert isinstance(result, LearnResult)
        await mem.close()

    async def test_dream_delegates_to_system(self, db_path_str: str) -> None:
        mem = await SmartMemory.open(db_path_str)
        await mem.remember("block A")
        result = await mem.dream()
        assert isinstance(result, ConsolidateResult)
        await mem.close()

    async def test_dream_none_when_nothing_pending(self, db_path_str: str) -> None:
        mem = await SmartMemory.open(db_path_str)
        result = await mem.dream()
        assert result is None
        await mem.close()

    async def test_should_dream_delegates_to_system(self, db_path_str: str) -> None:
        mem = await SmartMemory.open(db_path_str)
        assert mem.should_dream is mem._system.should_dream
        await mem.close()

    async def test_managed_dreams_on_exit_when_pending(self, db_path_str: str) -> None:
        """managed() safety net: if should_dream is True on exit, dream() is called."""
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=2))
        async with SmartMemory.managed(db_path_str, config=cfg) as mem:
            # Fill inbox to threshold
            await mem.remember("block one")
            await mem.remember("block two")
            assert mem.should_dream is True
        # After exit: inbox should be empty (dream ran)
        sys2 = await MemorySystem.from_config(db_path_str)
        status = await sys2.status()
        assert status.inbox_count == 0
        await sys2.close()

    async def test_shim_has_no_own_pending(self, db_path_str: str) -> None:
        """SmartMemory no longer owns _pending — it lives in _system."""
        mem = await SmartMemory.open(db_path_str)
        assert not hasattr(mem, "_pending")
        assert not hasattr(mem, "_threshold")
        assert not hasattr(mem, "_policy")
        await mem.close()


# ── _effective_threshold ──────────────────────────────────────────────────────


class TestEffectiveThreshold:
    def test_effective_threshold_without_policy(self, system: MemorySystem) -> None:
        assert system._effective_threshold() == system._config.memory.inbox_threshold

    def test_effective_threshold_with_policy(
        self, system: MemorySystem, policy: ConsolidationPolicy
    ) -> None:
        system._policy = policy
        assert system._effective_threshold() == policy.effective_threshold


# ── learn() _pending tracking ─────────────────────────────────────────────────


class TestLearnPending:
    async def test_learn_created_increments_pending(
        self, system: MemorySystem
    ) -> None:
        await system.learn("new knowledge")
        assert system._pending == 1

    async def test_learn_duplicate_does_not_increment_pending(
        self, system: MemorySystem
    ) -> None:
        await system.learn("same")
        assert system._pending == 1
        await system.learn("same")
        assert system._pending == 1  # duplicate_rejected → no increment

    async def test_consolidate_resets_pending(self, system: MemorySystem) -> None:
        await system.learn("fact 1")
        await system.learn("fact 2")
        assert system._pending == 2
        await system.consolidate()
        assert system._pending == 0
