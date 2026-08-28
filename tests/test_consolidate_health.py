"""Tests for ConsolidationHealthMetrics on ConsolidateResult.health.

Issue #73, ADR 0006. The metrics are observability-only — no policy or
runtime behaviour reads them. These tests verify that we *measure*, not
what the values are. Specific threshold values would ossify in tests and
prevent the very kind of tuning the metrics are designed to inform.

In-memory SQLite + mock services per ``tests/CLAUDE.md``. No real LLM
calls, no file I/O.
"""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.types import ConsolidateResult, ConsolidationHealthMetrics


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding):
    """MemorySystem with low inbox_threshold for fast learn→dream cycles."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    sys = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    yield sys
    await sys.close()


class TestHealthShape:
    """The dataclass exists, exports cleanly, and serialises to dict."""

    def test_health_dataclass_constructs(self) -> None:
        h = ConsolidationHealthMetrics(
            edge_creation_rate=1.5,
            promotion_rate=0.8,
            deduplication_rate=0.2,
        )
        assert h.promotion_rate == 0.8

    def test_health_to_dict_rounds_to_three_places(self) -> None:
        h = ConsolidationHealthMetrics(
            edge_creation_rate=1.23456,
            promotion_rate=0.66667,
            deduplication_rate=0.33333,
        )
        d = h.to_dict()
        assert d["edge_creation_rate"] == 1.235
        assert d["promotion_rate"] == 0.667
        assert d["deduplication_rate"] == 0.333

    def test_health_importable_from_package_root(self) -> None:
        # Agent-friendly principle 8: minimal import surface.
        from elfmem import ConsolidationHealthMetrics as ImportedFromRoot

        assert ImportedFromRoot is ConsolidationHealthMetrics


class TestConsolidateResultBackwardsCompat:
    """All 20+ existing constructor sites continue to work unchanged."""

    def test_consolidate_result_constructs_without_health(self) -> None:
        # Mirrors the pattern in test_consolidation_policy.py and test_mcp.py.
        r = ConsolidateResult(processed=100, promoted=85, deduplicated=0, edges_created=10)
        assert r.health is None

    def test_to_dict_includes_health_key_as_none_when_absent(self) -> None:
        r = ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)
        assert r.to_dict()["health"] is None


class TestHealthOnRealConsolidation:
    """The full path: learn → dream → assert .health is populated."""

    @pytest.mark.asyncio
    async def test_dream_populates_health_with_in_range_ratios(self, system) -> None:
        """After a real consolidation, .health is non-None and every ratio
        is finite and within its declared range."""
        for i in range(3):
            await system.learn(f"unique observation number {i}")

        result = await system.dream()
        assert result is not None
        assert result.health is not None

        h = result.health
        # promotion_rate and deduplication_rate ∈ [0.0, 1.0] by construction.
        assert 0.0 <= h.promotion_rate <= 1.0
        assert 0.0 <= h.deduplication_rate <= 1.0
        # edge_creation_rate is an unbounded non-negative ratio (cap is
        # EDGE_DEGREE_CAP=5 edges per block).
        assert h.edge_creation_rate >= 0.0

    @pytest.mark.asyncio
    async def test_promotion_and_dedup_rates_sum_at_most_one(self, system) -> None:
        """A processed block is either promoted or deduplicated — never both,
        never neither. So promotion_rate + deduplication_rate ≤ 1.0
        (slack only if some blocks were rejected by means we don't surface)."""
        for i in range(3):
            await system.learn(f"distinct content {i}")

        result = await system.dream()
        assert result is not None and result.health is not None
        assert result.health.promotion_rate + result.health.deduplication_rate <= 1.0 + 1e-9

    @pytest.mark.asyncio
    async def test_to_dict_round_trips_health(self, system) -> None:
        """to_dict() exposes health as a sub-dict for JSON consumers (MCP, logs)."""
        for i in range(3):
            await system.learn(f"item {i}")

        result = await system.dream()
        assert result is not None
        d = result.to_dict()
        assert d["health"] is not None
        assert set(d["health"].keys()) == {
            "edge_creation_rate",
            "promotion_rate",
            "deduplication_rate",
        }


class TestEmptyConsolidate:
    """Empty inbox is the documented "nothing to do" path."""

    @pytest.mark.asyncio
    async def test_empty_dream_returns_none_or_unmetric_result(self, system) -> None:
        """No learn calls → dream returns None (no work) OR a zero-processed
        ConsolidateResult with .health is None.

        Either is correct per agent_friendly_principles §6 (idempotency);
        what matters is that .health is never a misleading all-zeros object."""
        result = await system.dream()
        if result is not None:
            assert result.processed == 0
            assert result.health is None
