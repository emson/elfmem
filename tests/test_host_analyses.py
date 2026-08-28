"""Tests for host-agent-reasoning mode in consolidate()/dream() — letting a
host agent session (e.g. this Claude Code session) supply its own
alignment_score/tags/summary for inbox blocks instead of a configured LLM
adapter. See MemorySystem.inbox() (the read half) and
consolidate()/dream(host_analyses=...) (the write half).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.exceptions import ElfmemError, HostAnalysisError
from elfmem.types import InboxBlockSummary

pytestmark = pytest.mark.asyncio


@pytest.fixture
def system(test_engine, mock_llm, mock_embedding):
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    return MemorySystem(
        engine=test_engine, llm_service=mock_llm,
        embedding_service=mock_embedding, config=cfg,
    )


async def _active_row(test_engine, block_id):
    async with test_engine.connect() as conn:
        row = (await conn.execute(
            text("SELECT status, confidence, last_scored_at FROM blocks WHERE id = :id"),
            {"id": block_id},
        )).mappings().first()
    return row


class TestInboxRead:
    async def test_empty_inbox_returns_empty_list(self, system):
        assert await system.inbox() == []

    async def test_lists_pending_blocks_fifo(self, system):
        r1 = await system.learn("first fact")
        r2 = await system.learn("second fact")
        results = await system.inbox()
        assert [r.id for r in results] == [r1.block_id, r2.block_id]
        assert all(isinstance(r, InboxBlockSummary) for r in results)

    async def test_includes_tags_set_at_learn_time(self, system):
        result = await system.learn("a goal-tagged fact", tags=["self/goal"])
        [entry] = await system.inbox()
        assert entry.id == result.block_id
        assert entry.tags == ["self/goal"]

    async def test_max_count_caps_results(self, system):
        await system.learn("a")
        await system.learn("b")
        await system.learn("c")
        results = await system.inbox(max_count=2)
        assert len(results) == 2

    async def test_does_not_include_active_blocks(self, system):
        await system.learn("will be consolidated")
        await system.consolidate()
        assert await system.inbox() == []


class TestConsolidateHostAnalyses:
    async def test_host_covered_block_never_calls_the_llm(
        self, system, mock_llm, test_engine,
    ):
        result = await system.learn("a fact the host will analyse")
        assert mock_llm.process_block_calls == 0

        await system.consolidate(host_analyses={
            result.block_id: {
                "alignment_score": 0.9,
                "tags": ["self/value"],
                "summary": "Host-supplied summary.",
            },
        })

        assert mock_llm.process_block_calls == 0  # never called for this block
        row = await _active_row(test_engine, result.block_id)
        assert row["status"] == "active"
        assert row["confidence"] == pytest.approx(0.9)
        assert row["last_scored_at"] is not None  # real analysis, not a fallback

    async def test_host_analysis_tags_are_persisted(self, system, test_engine):
        result = await system.learn("host will tag this")
        await system.consolidate(host_analyses={
            result.block_id: {
                "alignment_score": 0.7, "tags": ["self/goal"], "summary": "s",
            },
        })
        async with test_engine.connect() as conn:
            tags = [r[0] for r in (await conn.execute(
                text("SELECT tag FROM block_tags WHERE block_id = :id"),
                {"id": result.block_id},
            )).fetchall()]
        assert "self/goal" in tags

    async def test_invalid_tag_is_silently_filtered(self, system, test_engine):
        result = await system.learn("host supplies a bogus tag")
        await system.consolidate(host_analyses={
            result.block_id: {
                "alignment_score": 0.5,
                "tags": ["self/goal", "not-a-real-tag"],
                "summary": "s",
            },
        })
        async with test_engine.connect() as conn:
            tags = [r[0] for r in (await conn.execute(
                text("SELECT tag FROM block_tags WHERE block_id = :id"),
                {"id": result.block_id},
            )).fetchall()]
        assert "self/goal" in tags
        assert "not-a-real-tag" not in tags

    async def test_uncovered_block_still_uses_configured_llm(
        self, system, mock_llm,
    ):
        covered = await system.learn("host covers this one")
        await system.learn("adapter covers this one")
        await system.consolidate(host_analyses={
            covered.block_id: {
                "alignment_score": 0.8, "tags": [], "summary": "s",
            },
        })
        # Only the uncovered block went through the real adapter.
        assert mock_llm.process_block_calls == 1

    async def test_malformed_host_analysis_raises(self, system):
        result = await system.learn("bad input from the host")
        with pytest.raises(HostAnalysisError) as exc_info:
            await system.consolidate(host_analyses={
                result.block_id: {"alignment_score": "not-a-number", "tags": [], "summary": "s"},
            })
        assert exc_info.value.recovery
        assert result.block_id in str(exc_info.value)

    async def test_alignment_score_out_of_range_raises(self, system):
        result = await system.learn("out of range score")
        with pytest.raises(HostAnalysisError):
            await system.consolidate(host_analyses={
                result.block_id: {"alignment_score": 1.5, "tags": [], "summary": "s"},
            })

    async def test_dedup_still_applies_to_host_analysed_blocks(
        self, system, test_engine,
    ):
        """Host-supplied analysis substitutes only the LLM step — the
        deterministic near-duplicate check upstream of it is unaffected."""
        first = await system.learn("Nature wastes nothing, apply minimum force.")
        await system.consolidate(host_analyses={
            first.block_id: {"alignment_score": 0.9, "tags": [], "summary": "s"},
        })
        # Exact duplicate content — still rejected regardless of host input.
        dup = await system.learn("Nature wastes nothing, apply minimum force.")
        await system.consolidate(host_analyses={
            dup.block_id: {"alignment_score": 0.9, "tags": [], "summary": "s"},
        })
        row = await _active_row(test_engine, dup.block_id)
        assert row["status"] == "archived"

    async def test_empty_host_analyses_behaves_like_none(self, system, mock_llm):
        """{} and None must be equivalent — an empty dict must not somehow
        suppress the real LLM path for every block."""
        await system.learn("normal block")
        await system.consolidate(host_analyses={})
        assert mock_llm.process_block_calls == 1


class TestDreamHostAnalyses:
    async def test_dream_threads_host_analyses_to_consolidate(
        self, system, mock_llm, test_engine,
    ):
        result = await system.learn("dream will apply host analysis")
        await system.dream(host_analyses={
            result.block_id: {
                "alignment_score": 0.85, "tags": ["self/style"], "summary": "s",
            },
        })
        assert mock_llm.process_block_calls == 0
        row = await _active_row(test_engine, result.block_id)
        assert row["status"] == "active"
        assert row["confidence"] == pytest.approx(0.85)


class TestConsolidateHostAnalysesErrorContract:
    async def test_missing_field_raises_host_analysis_error_with_recovery(self, system):
        """Every elfmem exception carries .recovery (Agent-First Contract) —
        host_analyses validation failures are no exception to that, even
        though the underlying cause is a pydantic ValidationError."""
        result = await system.learn("bad input")
        with pytest.raises(HostAnalysisError) as exc_info:
            await system.consolidate(host_analyses={
                result.block_id: {"tags": [], "summary": "s"},  # missing alignment_score
            })
        assert isinstance(exc_info.value, ElfmemError)
        assert exc_info.value.recovery
