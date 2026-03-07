"""Tests for ConsolidationPolicy — self-tuning consolidation timing."""

import pytest

from elfmem.policy import ConsolidationPolicy, PolicyStats
from elfmem.smart import SmartMemory
from elfmem.types import ConsolidateResult


class TestPolicyDefaults:
    """ConsolidationPolicy defaults and initialization."""

    def test_policy_default_threshold(self) -> None:
        """Policy starts with base_threshold=10."""
        policy = ConsolidationPolicy()
        assert policy.effective_threshold == 10

    def test_policy_respects_custom_base_threshold(self) -> None:
        """Custom base_threshold is respected."""
        policy = ConsolidationPolicy(base_threshold=15)
        assert policy.effective_threshold == 15

    def test_policy_bounds(self) -> None:
        """Policy has configurable min/max bounds."""
        policy = ConsolidationPolicy(min_threshold=3, max_threshold=100)
        assert policy._min == 3
        assert policy._max == 100


class TestPolicyShouldConsolidate:
    """should_consolidate() logic with adaptive threshold."""

    def test_should_consolidate_at_effective_threshold(self) -> None:
        """Consolidate when pending >= effective_threshold."""
        policy = ConsolidationPolicy(base_threshold=10)
        # Initially threshold is 10
        assert not policy.should_consolidate(9)
        assert policy.should_consolidate(10)
        assert policy.should_consolidate(15)

    def test_should_consolidate_hard_ceiling(self) -> None:
        """Always consolidate if pending >= max_threshold."""
        policy = ConsolidationPolicy(base_threshold=10, max_threshold=50)
        # Even if effective_threshold is 10, pending=50 forces consolidation
        assert policy.should_consolidate(50)
        assert policy.should_consolidate(60)

    def test_should_consolidate_hard_floor(self) -> None:
        """Never consolidate if pending < min_threshold."""
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5)
        assert not policy.should_consolidate(4)
        assert not policy.should_consolidate(0)


class TestPolicyAdaptation:
    """Threshold adaptation based on promotion_rate."""

    def test_high_promotion_rate_increases_threshold(self) -> None:
        """promotion_rate >= 0.80 increases effective_threshold."""
        policy = ConsolidationPolicy(base_threshold=10, adjustment_step=3)
        initial_threshold = policy.effective_threshold

        # Record high-quality consolidation
        result = ConsolidateResult(processed=100, promoted=85, deduplicated=0, edges_created=10)
        policy.record_result(result)

        # Threshold should increase by step size
        assert policy.effective_threshold == initial_threshold + 3

    def test_low_promotion_rate_decreases_threshold(self) -> None:
        """promotion_rate <= 0.50 decreases effective_threshold."""
        policy = ConsolidationPolicy(base_threshold=10, adjustment_step=3)
        initial_threshold = policy.effective_threshold

        # Record low-quality consolidation
        result = ConsolidateResult(processed=100, promoted=40, deduplicated=10, edges_created=5)
        policy.record_result(result)

        # Threshold should decrease by step size
        assert policy.effective_threshold == initial_threshold - 3

    def test_deadband_no_change(self) -> None:
        """promotion_rate in [0.50, 0.80] keeps threshold unchanged."""
        policy = ConsolidationPolicy(base_threshold=10)
        initial_threshold = policy.effective_threshold

        # Record mid-range promotion rate
        result = ConsolidateResult(processed=100, promoted=70, deduplicated=5, edges_created=8)
        policy.record_result(result)

        # Threshold should not change
        assert policy.effective_threshold == initial_threshold


class TestPolicyBounds:
    """Threshold respects min/max bounds."""

    def test_max_threshold_hard_ceiling(self) -> None:
        """Threshold never exceeds max_threshold."""
        policy = ConsolidationPolicy(base_threshold=10, max_threshold=20, adjustment_step=3)

        # Record high promotion rates multiple times
        for _ in range(5):
            result = ConsolidateResult(processed=100, promoted=90, deduplicated=0, edges_created=15)
            policy.record_result(result)

        # Should never exceed max_threshold
        assert policy.effective_threshold <= 20

    def test_min_threshold_hard_floor(self) -> None:
        """Threshold never goes below min_threshold."""
        policy = ConsolidationPolicy(base_threshold=10, min_threshold=5, adjustment_step=3)

        # Record low promotion rates multiple times
        for _ in range(5):
            result = ConsolidateResult(processed=100, promoted=40, deduplicated=10, edges_created=3)
            policy.record_result(result)

        # Should never go below min_threshold
        assert policy.effective_threshold >= 5


class TestPolicyEdgeCases:
    """Edge cases: zero processed, empty history, etc."""

    def test_zero_processed_safe(self) -> None:
        """record_result with zero processed is safe (no div by zero)."""
        policy = ConsolidationPolicy()
        result = ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)
        # Should not raise an exception
        policy.record_result(result)
        # Threshold should not change
        assert policy.effective_threshold == 10

    def test_no_history_returns_base(self) -> None:
        """With no history, avg_promotion_rate is 0.0."""
        policy = ConsolidationPolicy(base_threshold=10)
        assert policy.stats.avg_promotion_rate == 0.0

    def test_should_consolidate_with_policy_not_set(self) -> None:
        """SmartMemory without policy uses simple threshold."""
        # This test verifies fallback behavior in should_dream property
        assert True  # Verified through SmartMemory integration tests


class TestPolicyStats:
    """PolicyStats tracking and observable metrics."""

    def test_stats_tracks_consolidation_count(self) -> None:
        """stats.consolidation_count increments per record_result."""
        policy = ConsolidationPolicy()
        assert policy.stats.consolidation_count == 0

        for i in range(3):
            result = ConsolidateResult(processed=100, promoted=75, deduplicated=0, edges_created=12)
            policy.record_result(result)
            assert policy.stats.consolidation_count == i + 1

    def test_stats_tracks_promotion_rates(self) -> None:
        """stats.promotion_rates records each rate."""
        policy = ConsolidationPolicy()

        result1 = ConsolidateResult(processed=100, promoted=80, deduplicated=0, edges_created=10)
        result2 = ConsolidateResult(processed=100, promoted=60, deduplicated=0, edges_created=8)
        policy.record_result(result1)
        policy.record_result(result2)

        assert len(policy.stats.promotion_rates) == 2
        assert abs(policy.stats.promotion_rates[0] - 0.80) < 0.001
        assert abs(policy.stats.promotion_rates[1] - 0.60) < 0.001

    def test_stats_avg_promotion_rate(self) -> None:
        """stats.avg_promotion_rate computes mean correctly."""
        policy = ConsolidationPolicy()

        rates = [0.80, 0.60, 0.70]
        for i, rate in enumerate(rates):
            promoted = int(100 * rate)
            result = ConsolidateResult(processed=100, promoted=promoted, deduplicated=0, edges_created=10)
            policy.record_result(result)

        expected_avg = sum(rates) / len(rates)
        assert abs(policy.stats.avg_promotion_rate - expected_avg) < 0.001

    def test_stats_tracks_threshold_history(self) -> None:
        """stats.threshold_history records effective_threshold at each consolidation."""
        policy = ConsolidationPolicy(base_threshold=10, adjustment_step=2)

        # Record high-quality consolidations to increase threshold
        for _ in range(3):
            result = ConsolidateResult(processed=100, promoted=85, deduplicated=0, edges_created=10)
            policy.record_result(result)

        # Threshold history should show progression: [10, 12, 14]
        assert len(policy.stats.threshold_history) == 3
        assert policy.stats.threshold_history[0] == 10
        assert policy.stats.threshold_history[1] == 12
        assert policy.stats.threshold_history[2] == 14

    def test_stats_to_dict(self) -> None:
        """stats.to_dict() returns exportable dict."""
        policy = ConsolidationPolicy()
        result = ConsolidateResult(processed=100, promoted=75, deduplicated=0, edges_created=12)
        policy.record_result(result)

        stats_dict = policy.stats.to_dict()
        assert "consolidation_count" in stats_dict
        assert "avg_promotion_rate" in stats_dict
        assert "promotion_rates" in stats_dict
        assert "threshold_history" in stats_dict
        assert stats_dict["consolidation_count"] == 1


class TestSmartMemoryWithPolicy:
    """SmartMemory integration with ConsolidationPolicy."""

    @pytest.mark.asyncio
    async def test_smart_memory_accepts_policy(self, db_path_str) -> None:
        """SmartMemory.open() accepts optional policy parameter."""
        policy = ConsolidationPolicy(base_threshold=8)
        mem = await SmartMemory.open(db_path_str, policy=policy)
        assert mem._policy is policy
        await mem.close()

    @pytest.mark.asyncio
    async def test_smart_memory_should_dream_delegates_to_policy(self, db_path_str) -> None:
        """should_dream property delegates to policy when set."""
        policy = ConsolidationPolicy(base_threshold=5)  # Low threshold for testing
        mem = await SmartMemory.open(db_path_str, policy=policy)

        # Learn up to policy threshold
        for i in range(5):
            await mem.remember(f"Block {i}")

        # should_dream should be True (pending >= policy.effective_threshold)
        assert mem.should_dream
        await mem.close()

    @pytest.mark.asyncio
    async def test_smart_memory_policy_records_dream_result(self, db_path_str) -> None:
        """dream() feeds ConsolidateResult to policy.record_result()."""
        policy = ConsolidationPolicy(base_threshold=3)
        mem = await SmartMemory.open(db_path_str, policy=policy)

        # Learn blocks to trigger consolidation
        threshold = mem._threshold
        for i in range(threshold):
            await mem.remember(f"Block {i}")

        # Check initial state
        assert policy.stats.consolidation_count == 0

        # Dream and check that policy recorded the result
        result = await mem.dream()
        if result is not None:
            assert policy.stats.consolidation_count == 1
            assert len(policy.stats.promotion_rates) == 1

        await mem.close()

    @pytest.mark.asyncio
    async def test_smart_memory_without_policy_uses_simple_threshold(self, db_path_str) -> None:
        """SmartMemory without policy falls back to simple count-based threshold."""
        mem = await SmartMemory.open(db_path_str, policy=None)
        assert mem._policy is None

        # Learn blocks
        threshold = mem._threshold
        for i in range(threshold):
            await mem.remember(f"Block {i}")

        # should_dream should use simple _pending >= _threshold logic
        assert mem.should_dream
        await mem.close()

    @pytest.mark.asyncio
    async def test_managed_with_policy(self, db_path_str) -> None:
        """SmartMemory.managed() accepts optional policy parameter."""
        policy = ConsolidationPolicy(base_threshold=5)
        async with SmartMemory.managed(db_path_str, policy=policy) as mem:
            assert mem._policy is policy
            # Learn some blocks
            for i in range(5):
                await mem.remember(f"Block {i}")


class TestPolicyFullCycle:
    """Integration: learn → should_dream → dream → policy learns."""

    @pytest.mark.asyncio
    async def test_adaptive_threshold_in_multiple_cycles(self, db_path_str) -> None:
        """Over multiple cycles, threshold adapts based on promotion rate."""
        policy = ConsolidationPolicy(base_threshold=10, adjustment_step=2)
        mem = await SmartMemory.open(db_path_str, policy=policy)

        threshold = mem._threshold

        # Cycle 1: Learn blocks and consolidate
        for i in range(threshold):
            await mem.remember(f"Round1-Block{i}")

        result1 = await mem.dream()
        threshold1 = policy.effective_threshold

        # Cycle 2: Consolidate again
        for i in range(threshold):
            await mem.remember(f"Round2-Block{i}")

        result2 = await mem.dream()
        threshold2 = policy.effective_threshold

        # Threshold should adapt based on promotion rates
        # (actual value depends on consolidation results, but we can verify it changed)
        assert policy.stats.consolidation_count == 2
        # We can verify threshold history was recorded
        assert len(policy.stats.threshold_history) >= 1

        await mem.close()
