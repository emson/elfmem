"""Tests for suppress_contradictions() — the recall-time contradiction filter.

This is the read side of contradiction handling: it stays live after v2 step
7b retired the pairwise LLM *detection* loop in consolidate() (ADR 0010).
Previously only exercised indirectly through two assertion-free placeholder
tests that relied on the now-removed detection loop to populate the
contradictions table — neither ever asserted the suppression behaviour
itself. This file closes that gap with direct, DB-level tests.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.context.contradiction import suppress_contradictions
from elfmem.db.queries import insert_block, insert_contradiction
from elfmem.types import ScoredBlock


def _scored_block(
    block_id: str, *, confidence: float = 0.5, recency: float = 0.5,
) -> ScoredBlock:
    return ScoredBlock(
        id=block_id,
        content=f"content for {block_id}",
        tags=[],
        similarity=0.0,
        confidence=confidence,
        recency=recency,
        centrality=0.0,
        reinforcement=0.0,
        score=0.0,
    )


async def _seed_blocks(conn: AsyncConnection, *block_ids: str) -> None:
    for bid in block_ids:
        await insert_block(
            conn, block_id=bid, content=f"content for {bid}",
            category="knowledge", source="test", status="active",
        )


class TestSuppressContradictions:
    async def test_removes_lower_confidence_member(self, db_conn: AsyncConnection) -> None:
        await _seed_blocks(db_conn, "b1", "b2")
        await insert_contradiction(db_conn, block_a_id="b1", block_b_id="b2", score=0.9)

        candidates = [
            _scored_block("b1", confidence=0.9),
            _scored_block("b2", confidence=0.3),
        ]
        result = await suppress_contradictions(db_conn, candidates)

        assert [b.id for b in result] == ["b1"]

    async def test_equal_confidence_keeps_higher_recency(self, db_conn: AsyncConnection) -> None:
        await _seed_blocks(db_conn, "b1", "b2")
        await insert_contradiction(db_conn, block_a_id="b1", block_b_id="b2", score=0.9)

        candidates = [
            _scored_block("b1", confidence=0.5, recency=0.2),
            _scored_block("b2", confidence=0.5, recency=0.8),
        ]
        result = await suppress_contradictions(db_conn, candidates)

        assert [b.id for b in result] == ["b2"]

    async def test_no_contradiction_record_keeps_both(self, db_conn: AsyncConnection) -> None:
        await _seed_blocks(db_conn, "b1", "b2")

        candidates = [_scored_block("b1"), _scored_block("b2")]
        result = await suppress_contradictions(db_conn, candidates)

        assert {b.id for b in result} == {"b1", "b2"}

    async def test_single_candidate_returned_unchanged(self, db_conn: AsyncConnection) -> None:
        await _seed_blocks(db_conn, "b1")

        candidates = [_scored_block("b1")]
        result = await suppress_contradictions(db_conn, candidates)

        assert result == candidates

    async def test_contradiction_outside_candidate_set_ignored(
        self, db_conn: AsyncConnection,
    ) -> None:
        """A contradiction record involving a block not in `candidates` must
        not suppress anything — only pairs where both members are present."""
        await _seed_blocks(db_conn, "b1", "b2", "b3")
        await insert_contradiction(db_conn, block_a_id="b1", block_b_id="b3", score=0.9)

        candidates = [_scored_block("b1"), _scored_block("b2")]
        result = await suppress_contradictions(db_conn, candidates)

        assert {b.id for b in result} == {"b1", "b2"}
