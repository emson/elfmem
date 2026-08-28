"""Tests for elfmem.migration.parity — U-005 (migration Phase 4 gate)."""

from __future__ import annotations

import pytest

from elfmem.adapters.mock import make_mock_embedding
from elfmem.context.frames import ATTENTION_FRAME
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import add_tags, insert_block, seed_builtin_data, update_block_scoring
from elfmem.migration.parity import (
    ParityGateResult,
    QueryParityCheck,
    check_retrieval_parity,
)


@pytest.fixture
async def two_engines():
    """Two independent in-memory databases — simulates 'before' (original
    DB-native state) and 'after' (rebuilt from exported files)."""
    before = await create_test_engine()
    after = await create_test_engine()
    async with before.begin() as conn:
        await seed_builtin_data(conn)
    async with after.begin() as conn:
        await seed_builtin_data(conn)
    yield before, after
    await before.dispose()
    await after.dispose()


async def _populate(
    engine, block_id: str, content: str, tags: list[str], *, confidence: float = 0.8
) -> None:
    # ATTENTION_FRAME has no tag filter, so retrieval always goes through
    # the embedding-required prefilter path (_stage_1_prefilter) -- a block
    # with no embedding is invisible to it regardless of content.
    embedder = make_mock_embedding()
    vec = await embedder.embed(content.strip().lower())
    async with engine.begin() as conn:
        await insert_block(
            conn,
            block_id=block_id,
            content=content,
            category="knowledge",
            source="api",
            status="active",
            confidence=confidence,
        )
        if tags:
            await add_tags(conn, block_id, tags)
        await update_block_scoring(
            conn, block_id, embedding=vec, embedding_model=embedder.model_name
        )


class TestIdenticalStatesPass:
    async def test_identical_states_pass_the_gate(self, two_engines, mock_embedding):
        before_engine, after_engine = two_engines
        await _populate(before_engine, "b1", "Shared content.", ["attention"])
        await _populate(after_engine, "b1", "Shared content.", ["attention"])

        async with before_engine.connect() as conn_before, after_engine.connect() as conn_after:
            result = await check_retrieval_parity(
                conn_before,
                conn_after,
                mock_embedding,
                [("shared", ATTENTION_FRAME)],
            )

        assert result.block_count_matches
        assert result.passed
        assert result.diverging_queries() == []


class TestBlockCountDivergence:
    async def test_block_count_mismatch_fails_the_gate(self, two_engines, mock_embedding):
        before_engine, after_engine = two_engines
        await _populate(before_engine, "b1", "Only in before.", [])
        await _populate(before_engine, "b2", "Also only in before.", [])
        await _populate(after_engine, "b1", "Only in before.", [])

        async with before_engine.connect() as conn_before, after_engine.connect() as conn_after:
            result = await check_retrieval_parity(
                conn_before, conn_after, mock_embedding, []
            )

        assert result.block_count_before == 2
        assert result.block_count_after == 1
        assert not result.block_count_matches
        assert not result.passed


class TestQueryResultDivergence:
    async def test_diverging_query_results_fail_the_gate(
        self, two_engines, mock_embedding
    ):
        before_engine, after_engine = two_engines
        # Two blocks in both DBs (same count either way) -- but confidence
        # is swapped in "after", the scenario this gate exists to catch: a
        # botched migration that scrambles evidence data without dropping
        # or adding blocks, so a block-count check alone would miss it.
        await _populate(before_engine, "b1", "Block one.", ["attention"], confidence=0.95)
        await _populate(before_engine, "b2", "Block two.", ["attention"], confidence=0.05)
        await _populate(after_engine, "b1", "Block one.", ["attention"], confidence=0.05)
        await _populate(after_engine, "b2", "Block two.", ["attention"], confidence=0.95)

        async with before_engine.connect() as conn_before, after_engine.connect() as conn_after:
            result = await check_retrieval_parity(
                conn_before,
                conn_after,
                mock_embedding,
                [(None, ATTENTION_FRAME)],  # no query -> confidence weighs more heavily
            )

        assert result.block_count_matches  # same count, same content, different evidence
        assert not result.passed
        diverging = result.diverging_queries()
        assert len(diverging) == 1
        assert diverging[0].frame_name == "attention"
        # The ranking order itself flipped -- top result differs.
        assert diverging[0].before_ids[0] != diverging[0].after_ids[0]


class TestNoQueryFrame:
    async def test_frame_with_no_query_still_compared(self, two_engines, mock_embedding):
        before_engine, after_engine = two_engines
        await _populate(before_engine, "b1", "Constitutional-ish content.", ["attention"])
        await _populate(after_engine, "b1", "Constitutional-ish content.", ["attention"])

        async with before_engine.connect() as conn_before, after_engine.connect() as conn_after:
            result = await check_retrieval_parity(
                conn_before,
                conn_after,
                mock_embedding,
                [(None, ATTENTION_FRAME)],
            )

        assert result.passed
        assert result.query_checks[0].query is None


class TestStaleEdgeDiagnosis:
    """A gate failure has to say why. The verified cause on a real corpus was
    edges in the source pointing at non-active blocks: they inflate centrality
    on the 'before' side, and a rebuild cannot reproduce them because
    `archive/` is deliberately never re-read."""

    def test_diagnosis_is_none_when_the_gate_passes(self):
        result = ParityGateResult(
            block_count_before=10, block_count_after=10,
            query_checks=[
                QueryParityCheck(None, "attention", ["a", "b"], ["a", "b"])
            ],
        )
        assert result.passed is True
        assert result.diagnosis is None

    def test_block_count_mismatch_is_diagnosed_first(self):
        result = ParityGateResult(
            block_count_before=10, block_count_after=9, stale_edges_in_source=5,
        )
        assert "Block count differs" in result.diagnosis

    def test_stale_edges_are_named_with_a_repair(self):
        result = ParityGateResult(
            block_count_before=10, block_count_after=10,
            query_checks=[
                QueryParityCheck(None, "attention", ["a", "b"], ["a", "c"])
            ],
            stale_edges_in_source=67,
        )
        assert result.passed is False
        diagnosis = result.diagnosis
        assert "67 edge(s)" in diagnosis
        assert "DELETE FROM edges" in diagnosis

    def test_divergence_with_no_known_cause_reports_nothing_rather_than_guessing(
        self,
    ):
        result = ParityGateResult(
            block_count_before=10, block_count_after=10,
            query_checks=[
                QueryParityCheck(None, "attention", ["a", "b"], ["a", "c"])
            ],
            stale_edges_in_source=0,
        )
        assert result.passed is False
        assert result.diagnosis is None
