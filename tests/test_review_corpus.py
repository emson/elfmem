"""Tests for review_corpus() — deterministic staleness detection (v2 step 6a).

Corpus review's own signal (reinforcement_count / last_reinforced_at /
outcome_evidence) is independent of the decay-tier system — no decay_lambda
involved. Pure-function tests need no DB or LLM at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.api import MemorySystem
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_block, seed_builtin_data
from elfmem.operations.corpus_review import _stale_candidates
from elfmem.types import ArchiveReason, CorpusReviewResult


def _block(
    block_id: str = "blk1",
    *,
    last_reinforced_at: float = 0.0,
    reinforcement_count: int = 0,
    outcome_evidence: float = 0.0,
    content: str = "some content",
) -> dict:
    return {
        "id": block_id,
        "content": content,
        "last_reinforced_at": last_reinforced_at,
        "reinforcement_count": reinforcement_count,
        "outcome_evidence": outcome_evidence,
    }


class TestStaleCandidatesPure:
    """Pure-function tests — no DB, no LLM."""

    def test_flags_block_matching_all_three_signals(self) -> None:
        blocks = [_block(last_reinforced_at=0.0, reinforcement_count=0, outcome_evidence=0.0)]
        result = _stale_candidates(
            blocks, current_active_hours=1000.0,
            min_hours_since_reinforced=720.0, max_reinforcement_count=2, max_proposals=20,
        )
        assert len(result) == 1
        assert result[0].kind == "stale"
        assert result[0].block_id == "blk1"

    def test_recently_reinforced_not_flagged(self) -> None:
        blocks = [_block(last_reinforced_at=999.0, reinforcement_count=0, outcome_evidence=0.0)]
        result = _stale_candidates(
            blocks, current_active_hours=1000.0,
            min_hours_since_reinforced=720.0, max_reinforcement_count=2, max_proposals=20,
        )
        assert result == []

    def test_heavily_reinforced_not_flagged_even_if_old(self) -> None:
        blocks = [_block(last_reinforced_at=0.0, reinforcement_count=50, outcome_evidence=0.0)]
        result = _stale_candidates(
            blocks, current_active_hours=1000.0,
            min_hours_since_reinforced=720.0, max_reinforcement_count=2, max_proposals=20,
        )
        assert result == []

    def test_positive_outcome_evidence_not_flagged(self) -> None:
        blocks = [_block(last_reinforced_at=0.0, reinforcement_count=0, outcome_evidence=3.0)]
        result = _stale_candidates(
            blocks, current_active_hours=1000.0,
            min_hours_since_reinforced=720.0, max_reinforcement_count=2, max_proposals=20,
        )
        assert result == []

    def test_respects_max_proposals(self) -> None:
        blocks = [_block(block_id=f"blk{i}") for i in range(5)]
        result = _stale_candidates(
            blocks, current_active_hours=1000.0,
            min_hours_since_reinforced=720.0, max_reinforcement_count=2, max_proposals=3,
        )
        assert len(result) == 3

    def test_all_three_signals_required_not_any_one(self) -> None:
        """Requiring all three (not any one) avoids flagging a block that's
        merely old-but-heavily-used or new-but-never-reinforced-yet."""
        blocks = [
            _block("old_but_heavy", last_reinforced_at=0.0, reinforcement_count=40),
            _block("recent_and_light", last_reinforced_at=990.0, reinforcement_count=0),
        ]
        result = _stale_candidates(
            blocks, current_active_hours=1000.0,
            min_hours_since_reinforced=720.0, max_reinforcement_count=2, max_proposals=20,
        )
        assert result == []


@pytest.fixture
async def system():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    mem = MemorySystem(
        engine=engine,
        llm_service=MockLLMService(default_alignment=0.65),
        embedding_service=MockEmbeddingService(dimensions=64),
    )
    yield mem
    await mem.close()


async def _make_stale(system: MemorySystem, block_id: str) -> None:
    """Push a block's signals into staleness range regardless of how little
    real active-time has elapsed in the test (current_active_hours starts
    near zero) — a large negative last_reinforced_at guarantees
    current - last_reinforced_at exceeds any real threshold."""
    async with system._engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE blocks SET last_reinforced_at = -1000.0, "
                "reinforcement_count = 0, outcome_evidence = 0.0 "
                "WHERE id = :id"
            ),
            {"id": block_id},
        )


class TestReviewCorpusIntegration:
    async def test_stale_block_surfaced_as_proposal(self, system: MemorySystem) -> None:
        async with system.session():
            learned = await system.learn("Old unused fact.")
            await system.consolidate()
        await _make_stale(system, learned.block_id)

        result = await system.review_corpus()
        assert isinstance(result, CorpusReviewResult)
        assert any(p.block_id == learned.block_id for p in result.proposals)
        assert all(p.kind == "stale" for p in result.proposals)

    async def test_fresh_block_not_surfaced(self, system: MemorySystem) -> None:
        async with system.session():
            await system.learn("Fresh fact.")
            await system.consolidate()

        result = await system.review_corpus()
        assert result.proposals == []

    async def test_zero_llm_calls(self, system: MemorySystem) -> None:
        async with system.session():
            await system.learn("Some fact.")
            await system.consolidate()

        llm_calls_before = system._llm.process_block_calls
        embed_calls_before = system._embedding.embed_calls
        await system.review_corpus()
        assert system._llm.process_block_calls == llm_calls_before
        assert system._embedding.embed_calls == embed_calls_before

    async def test_reviewed_count_matches_active_blocks(self, system: MemorySystem) -> None:
        async with system.session():
            await system.learn("Fact one.")
            await system.learn("Fact two.")
            await system.consolidate()

        result = await system.review_corpus()
        assert result.reviewed_count >= 2

    async def test_max_proposals_respected(self, system: MemorySystem) -> None:
        from elfmem.config import (
            CorpusReviewConfig,
            ElfmemConfig,
            MemoryConfig,
            ReviewConfig,
        )

        async with system.session():
            block_ids = []
            for i in range(5):
                r = await system.learn(f"Stale-bound fact {i}.")
                block_ids.append(r.block_id)
            await system.consolidate(max_inbox_per_run=10)
        for bid in block_ids:
            await _make_stale(system, bid)

        system._config = ElfmemConfig(
            memory=MemoryConfig(),
            review=ReviewConfig(corpus=CorpusReviewConfig(max_proposals=2)),
        )
        result = await system.review_corpus()
        assert len(result.proposals) == 2


class TestForgetWithReason:
    async def test_default_reason_is_forgotten(self, system: MemorySystem) -> None:
        async with system.session():
            learned = await system.learn("Will be forgotten.")
            await system.consolidate()
        await system.forget(learned.block_id)
        async with system._engine.begin() as conn:
            block = await get_block(conn, learned.block_id)
        assert block is not None
        assert block["archive_reason"] == "forgotten"

    async def test_decayed_reason_recorded(self, system: MemorySystem) -> None:
        async with system.session():
            learned = await system.learn("Will be marked decayed.")
            await system.consolidate()
        await system.forget(learned.block_id, reason=ArchiveReason.DECAYED)
        async with system._engine.begin() as conn:
            block = await get_block(conn, learned.block_id)
        assert block is not None
        assert block["archive_reason"] == "decayed"
