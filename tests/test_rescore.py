"""Tests for deep-sleep rescoring (v0.13.3).

Covers:
- ``last_scored_at`` lifecycle: stamped on full-LLM consolidate; NULL on
  ``skip_llm=True`` or LLM timeout.
- Eligibility filter (categories, source_peer, tags, cooldown).
- Selection ordering (NULLs first, then oldest).
- Drift stats math and warning logic.
- ``rescore_blocks`` updates scoring fields and clears NULL.
- ``dream(rescore=True)`` integration with the inbox phase.
- Mutual-exclusion guard between rescore and skip_llm.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig, RescoreConfig
from elfmem.exceptions import ConfigError
from elfmem.operations.rescore import (
    DEFAULT_MAX_PER_RUN,
    DriftStats,
    RescoreFilter,
    compute_drift_stats,
    rescore_blocks,
    select_rescore_candidates,
)

# ── Pure DriftStats arithmetic (no DB) ─────────────────────────────────────


class TestDriftStatsArithmetic:
    def test_drift_sums_unscored_plus_stale(self):
        s = DriftStats(total_active=100, unscored=5, stale=10, target_max_age_days=90)
        assert s.drift == 15

    def test_percent_zero_when_no_active(self):
        s = DriftStats(total_active=0, unscored=0, stale=0, target_max_age_days=90)
        assert s.percent_drift_of_total() == 0.0

    def test_percent_correct(self):
        s = DriftStats(total_active=100, unscored=10, stale=15, target_max_age_days=90)
        assert s.percent_drift_of_total() == pytest.approx(25.0)

    def test_is_drifting_count_threshold(self):
        s = DriftStats(total_active=200, unscored=20, stale=10, target_max_age_days=90)
        # 30 drift / 200 = 15% — under percent threshold but over count threshold
        assert s.is_drifting(count_threshold=25, percent_threshold=25)

    def test_is_drifting_percent_threshold(self):
        s = DriftStats(total_active=10, unscored=2, stale=2, target_max_age_days=90)
        # 4 drift / 10 = 40% — over percent threshold even though count is small
        assert s.is_drifting(count_threshold=25, percent_threshold=25)

    def test_no_drift_when_healthy(self):
        s = DriftStats(total_active=100, unscored=2, stale=3, target_max_age_days=90)
        # 5 drift / 100 = 5% — under both thresholds
        assert not s.is_drifting(count_threshold=25, percent_threshold=25)

    def test_recommended_max_floor(self):
        s = DriftStats(total_active=100, unscored=0, stale=0, target_max_age_days=90)
        assert s.recommended_max(floor=20) == 20

    def test_recommended_max_rounds_up_to_50(self):
        s = DriftStats(total_active=100, unscored=47, stale=89, target_max_age_days=90)
        # drift = 136 → margin = 136 → ceil to 150
        assert s.recommended_max() == 150

    def test_recommended_max_uses_floor_when_drift_below(self):
        s = DriftStats(total_active=100, unscored=3, stale=2, target_max_age_days=90)
        # drift = 5 < floor=20 → margin = 20 → ceil to 50
        assert s.recommended_max() == 50


# ── DB-backed: selection, filter, drift stats ──────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding):
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


def _filter() -> RescoreFilter:
    return RescoreFilter(
        exclude_categories=("message", "mind", "decision", "prediction"),
        exclude_tags=("system/no-rescore",),
        min_age_hours=24,
        target_max_age_days=90,
    )


async def _stamp_last_scored_at(test_engine, block_id: str, value: str | None) -> None:
    """Force a block's last_scored_at to a known value for ordering tests."""
    from sqlalchemy import text
    async with test_engine.begin() as conn:
        await conn.execute(
            text("UPDATE blocks SET last_scored_at = :v WHERE id = :id"),
            {"v": value, "id": block_id},
        )


class TestLastScoredAtLifecycle:
    async def test_full_llm_consolidate_stamps_value(self, system, test_engine):
        await system.learn("a fact about cats")
        await system.learn("another fact about dogs")
        await system.learn("a third fact about birds")
        await system.consolidate()
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id, last_scored_at FROM blocks WHERE status='active'")
            )).fetchall()
        assert len(rows) >= 1
        # All consolidated blocks get a non-NULL last_scored_at.
        assert all(r[1] is not None for r in rows)

    async def test_skip_llm_promotes_with_null(self, system, test_engine):
        await system.learn("bulk-import block 1")
        await system.learn("bulk-import block 2")
        await system.learn("bulk-import block 3")
        await system.consolidate(skip_llm=True)
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT last_scored_at FROM blocks WHERE status='active'")
            )).fetchall()
        # All should be NULL (never LLM-scored).
        assert all(r[0] is None for r in rows)


class TestEligibilityFilter:
    async def test_excludes_message_category(self, system, test_engine):
        await system.learn("just a message", category="message")
        await system.learn("a real fact")
        await system.learn("filler 1")
        await system.consolidate(skip_llm=True)
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            ids = await select_rescore_candidates(
                conn, filt=_filter(), max_count=100,
            )
            if ids:
                params = {f"id_{i}": v for i, v in enumerate(ids)}
                placeholders = ", ".join(f":id_{i}" for i in range(len(ids)))
                cats = (await conn.execute(text(
                    f"SELECT category FROM blocks WHERE id IN ({placeholders})"
                ), params)).fetchall()
            else:
                cats = []
        # No message-category block should appear in candidates.
        assert all(c[0] != "message" for c in cats)

    async def test_excludes_source_peer(self, system, test_engine):
        await system.learn("our own fact")
        await system.learn("filler 1")
        await system.learn("filler 2")
        await system.consolidate(skip_llm=True)
        # Mark one block as peer-imported and verify exclusion.
        from sqlalchemy import text
        async with test_engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT id FROM blocks WHERE status='active' LIMIT 1"
            ))).fetchone()
            assert row is not None
            peer_id = row[0]
            await conn.execute(text(
                "UPDATE blocks SET source_peer = 'elf:other' WHERE id = :id"
            ), {"id": peer_id})
        async with test_engine.connect() as conn:
            ids = await select_rescore_candidates(
                conn, filt=_filter(), max_count=100,
            )
        assert peer_id not in ids

    async def test_excludes_no_rescore_tag(self, system, test_engine):
        await system.remember("opt-out block", tags=["system/no-rescore"])
        await system.learn("filler 1")
        await system.learn("filler 2")
        await system.consolidate(skip_llm=True)
        async with test_engine.connect() as conn:
            ids = await select_rescore_candidates(
                conn, filt=_filter(), max_count=100,
            )
            from sqlalchemy import text
            tags_for_ids = (await conn.execute(text(
                "SELECT block_id FROM block_tags WHERE tag = 'system/no-rescore'"
            ))).fetchall()
        excluded = {r[0] for r in tags_for_ids}
        assert not (excluded & set(ids))

    async def test_min_age_cooldown(self, system, test_engine):
        await system.learn("fresh fact")
        await system.learn("filler 1")
        await system.learn("filler 2")
        await system.consolidate()  # full LLM, sets last_scored_at = now
        async with test_engine.connect() as conn:
            ids = await select_rescore_candidates(
                conn, filt=_filter(), max_count=100,
            )
        # All blocks just-scored → none eligible (under 24h cooldown).
        assert ids == []


class TestSelectionOrdering:
    async def test_nulls_first_then_oldest(self, system, test_engine):
        # Promote three blocks with skip_llm to get NULLs.
        await system.learn("block A")
        await system.learn("block B")
        await system.learn("block C")
        await system.consolidate(skip_llm=True)
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id FROM blocks WHERE status='active' ORDER BY id")
            )).fetchall()
        ids = [r[0] for r in rows]
        # Stamp two blocks with old/old timestamps; leave one as NULL.
        old1 = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        old2 = (datetime.now(UTC) - timedelta(days=60)).isoformat()
        await _stamp_last_scored_at(test_engine, ids[0], old1)
        await _stamp_last_scored_at(test_engine, ids[1], old2)
        # ids[2] keeps NULL.

        async with test_engine.connect() as conn:
            selected = await select_rescore_candidates(
                conn, filt=_filter(), max_count=10,
            )
        # NULL-id should be first, then ids[1] (older), then ids[0] (newer).
        assert selected[0] == ids[2]
        assert selected[1] == ids[1]
        assert selected[2] == ids[0]

    async def test_progressive_rotation(self, system, test_engine):
        # After rescoring the front of the queue, the same blocks should
        # NOT come back at the front next time (progressive rotation).
        await system.learn("block A")
        await system.learn("block B")
        await system.learn("block C")
        await system.consolidate(skip_llm=True)
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id FROM blocks WHERE status='active'")
            )).fetchall()
        ids = sorted(r[0] for r in rows)
        # Manually stamp all to 24+ hours ago so they're eligible.
        old_iso = (datetime.now(UTC) - timedelta(days=2)).isoformat()
        for bid in ids:
            await _stamp_last_scored_at(test_engine, bid, old_iso)
        # Now stamp the "first" in queue with current time → should drop to
        # the back of the rotation.
        now_iso = datetime.now(UTC).isoformat()
        await _stamp_last_scored_at(test_engine, ids[0], now_iso)
        async with test_engine.connect() as conn:
            selected = await select_rescore_candidates(
                conn, filt=_filter(), max_count=10,
            )
        # ids[0] (just-stamped) should NOT be in the selection (under cooldown).
        assert ids[0] not in selected


class TestDriftStats:
    async def test_unscored_count(self, system, test_engine):
        await system.learn("a")
        await system.learn("b")
        await system.learn("c")
        await system.consolidate(skip_llm=True)
        async with test_engine.connect() as conn:
            stats = await compute_drift_stats(conn, filt=_filter())
        assert stats.unscored >= 1
        assert stats.stale == 0

    async def test_stale_count(self, system, test_engine):
        await system.learn("a")
        await system.learn("b")
        await system.learn("c")
        await system.consolidate()  # full LLM
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id FROM blocks WHERE status='active'")
            )).fetchall()
        # Backdate one block to 100 days ago.
        old_iso = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        await _stamp_last_scored_at(test_engine, rows[0][0], old_iso)
        async with test_engine.connect() as conn:
            stats = await compute_drift_stats(conn, filt=_filter())
        assert stats.stale == 1


class TestRescoreExecution:
    async def test_rescore_clears_null_and_updates_scoring(
        self, system, test_engine,
    ):
        await system.learn("rescore me 1")
        await system.learn("rescore me 2")
        await system.learn("rescore me 3")
        await system.consolidate(skip_llm=True)  # → NULLs
        # NULLs are unconditionally eligible (no cooldown applies); no
        # backdating needed — they're already in the rescore queue.
        from sqlalchemy import text
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id FROM blocks WHERE status='active'")
            )).fetchall()
        # Leave them NULL (debt scenario) — already eligible.
        ids = [r[0] for r in rows]

        async with test_engine.begin() as conn:
            result = await rescore_blocks(
                conn, block_ids=ids,
                llm=system._llm, embedding_svc=system._embedding,
            )
        assert result["rescored"] >= 1
        # All NULLs cleared.
        async with test_engine.connect() as conn:
            null_count = (await conn.execute(text(
                "SELECT COUNT(*) FROM blocks "
                "WHERE status='active' AND last_scored_at IS NULL"
            ))).fetchone()[0]
        assert null_count == 0

    async def test_dream_with_rescore_processes_both_phases(
        self, system, test_engine,
    ):
        # First, create some unscored blocks.
        await system.learn("unscored 1")
        await system.learn("unscored 2")
        await system.learn("unscored 3")
        await system.consolidate(skip_llm=True)
        # Now add fresh inbox blocks.
        await system.learn("fresh 1")
        await system.learn("fresh 2")
        # dream(rescore=True): processes inbox AND rescore queue.
        result = await system.dream(rescore=True, rescore_max=10)
        assert result is not None
        assert result.processed > 0  # inbox phase happened
        assert result.rescored > 0  # rescore phase happened


class TestRescoreSkipLlmExclusion:
    async def test_dream_rescore_with_skip_llm_raises(self, system):
        with pytest.raises(ConfigError) as ei:
            await system.dream(rescore=True, skip_llm=True)
        assert "rescore" in str(ei.value).lower()

    async def test_disabled_config_returns_zeros(
        self, test_engine, mock_llm, mock_embedding,
    ):
        cfg = ElfmemConfig(
            memory=MemoryConfig(inbox_threshold=3),
            rescore=RescoreConfig(enabled=False),
        )
        sys2 = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        result = await sys2.rescore()
        assert result == {"rescored": 0, "failed": 0, "attempted": 0}


class TestDefaultsAreReasonable:
    def test_defaults_match_constants(self):
        cfg = RescoreConfig()
        assert cfg.max_per_run == DEFAULT_MAX_PER_RUN
        assert cfg.enabled is True
        assert "message" in cfg.exclude_categories
        assert "mind" in cfg.exclude_categories
        assert "system/no-rescore" in cfg.exclude_tags
