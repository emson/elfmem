"""ConsolidationPolicy — self-tuning consolidation timing based on promotion rate.

This policy learns when to consolidate by tracking promotion rate (promoted/processed).
Higher promotion rates indicate good timing; the policy adapts the threshold accordingly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from elfmem.types import ConsolidateResult


@dataclass
class PolicyStats:
    """Observable statistics from ConsolidationPolicy."""

    consolidation_count: int = 0
    """Number of consolidation cycles recorded."""

    promotion_rates: list[float] = field(default_factory=list)
    """Historical promotion rates (promoted/processed) for each cycle."""

    threshold_history: list[int] = field(default_factory=list)
    """Effective threshold used before each consolidation."""

    @property
    def avg_promotion_rate(self) -> float:
        """Average promotion rate across all cycles (0.0 if no history)."""
        if not self.promotion_rates:
            return 0.0
        return sum(self.promotion_rates) / len(self.promotion_rates)

    def to_dict(self) -> dict[str, Any]:
        """Exportable dict representation."""
        return {
            "consolidation_count": self.consolidation_count,
            "avg_promotion_rate": round(self.avg_promotion_rate, 3),
            "promotion_rates": [round(r, 3) for r in self.promotion_rates],
            "threshold_history": self.threshold_history,
        }


class ConsolidationPolicy:
    """Self-tuning consolidation policy based on promotion rate feedback.

    Maintains an adaptive threshold that adjusts based on the quality of each
    consolidation cycle (measured by promotion_rate = promoted/processed).

    Attributes:
        base_threshold: Starting threshold (default 10, matches current behavior).
        min_threshold: Never consolidate below this (default 5).
        max_threshold: Never consolidate above this (default 50).
        adjustment_step: How much to adjust threshold per cycle (default 3).
        high_rate_threshold: Minimum rate to increase threshold (default 0.80).
        low_rate_threshold: Maximum rate to decrease threshold (default 0.50).
    """

    def __init__(
        self,
        *,
        base_threshold: int = 10,
        min_threshold: int = 5,
        max_threshold: int = 50,
        adjustment_step: int = 3,
        high_rate_threshold: float = 0.80,
        low_rate_threshold: float = 0.50,
    ) -> None:
        self._base = base_threshold
        self._min = min_threshold
        self._max = max_threshold
        self._step = adjustment_step
        self._high_rate = high_rate_threshold
        self._low_rate = low_rate_threshold
        self._current = base_threshold
        self._stats = PolicyStats()

    def should_consolidate(
        self, pending: int, active_count: int = 0, session_blocks: int = 0
    ) -> bool:
        """Decide whether consolidation should happen now.

        Args:
            pending: Number of blocks in inbox awaiting consolidation.
            active_count: Number of active blocks (unused for simple policy,
                included for future expansion).
            session_blocks: Total blocks learned this session (unused for simple policy).

        Returns:
            True if consolidation should happen, False otherwise.
        """
        # Hard ceiling: always consolidate if inbox overflows
        if pending >= self._max:
            return True

        # Hard floor: never consolidate below minimum
        if pending < self._min:
            return False

        # Adaptive: consolidate when pending reaches effective threshold
        return pending >= self._current

    def record_result(self, result: ConsolidateResult) -> None:
        """Record the outcome of a consolidation cycle and adapt the threshold.

        Promotion rate = promoted/processed. Higher rate suggests good timing.
        - rate >= high_rate_threshold → increase threshold (larger batches)
        - rate <= low_rate_threshold → decrease threshold (consolidate sooner)
        - otherwise → no change (in "acceptable" dead-band)

        Args:
            result: ConsolidateResult from SmartMemory.dream().
        """
        if result.processed == 0:
            # Edge case: empty consolidation (no blocks to process)
            return

        # Calculate promotion rate and update stats
        rate = result.promoted / result.processed
        self._stats.promotion_rates.append(rate)
        self._stats.consolidation_count += 1
        self._stats.threshold_history.append(self._current)

        # Adapt threshold based on promotion rate
        if rate >= self._high_rate:
            # High-quality consolidation: increase threshold to batch more
            self._current = min(self._max, self._current + self._step)
        elif rate <= self._low_rate:
            # Low-quality consolidation: decrease threshold to consolidate sooner
            self._current = max(self._min, self._current - self._step)
        # else: in dead-band (acceptable range) → no change

    def restore_threshold(self, threshold: int) -> None:
        """Restore a previously persisted effective threshold.

        Called by MemorySystem during startup to resume adaptive learning
        from where it left off. Clamps to [min_threshold, max_threshold]
        so corrupted or out-of-range values degrade gracefully.

        Args:
            threshold: Previously saved effective_threshold value, typically
                loaded from the system_config database table.
        """
        self._current = max(self._min, min(self._max, threshold))

    @property
    def effective_threshold(self) -> int:
        """Current adaptive threshold for consolidation."""
        return self._current

    @property
    def stats(self) -> PolicyStats:
        """Observable statistics (cycles, promotion rates, threshold history)."""
        return self._stats
