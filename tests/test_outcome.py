"""Tests for domain-agnostic outcome scoring (outcome()).

- TestComputeBayesianUpdate: pure function unit tests (no DB)
- TestRecordOutcome: integration tests via operation directly
- TestOutcomeAPI: integration tests via MemorySystem.outcome()
"""

from __future__ import annotations

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_block, seed_builtin_data, update_block_scoring
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn
from elfmem.operations.outcome import compute_bayesian_update, record_outcome
from elfmem.types import OutcomeResult


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def setup():
    """In-memory engine with seeded schema."""
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    mock_llm = MockLLMService(default_alignment=0.65)
    mock_embedding = MockEmbeddingService(dimensions=64)
    yield engine, mock_llm, mock_embedding
    await engine.dispose()


async def _make_active_block(conn, mock_llm, mock_embedding, content="test block"):
    """Create a block in inbox, consolidate to active, return block_id."""
    result = await learn(conn, content=content, category="knowledge", source="api")
    await consolidate(conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=1.0)
    return result.block_id


# ── TestComputeBayesianUpdate ──────────────────────────────────────────────────


class TestComputeBayesianUpdate:
    """Unit tests for compute_bayesian_update() — no DB required."""

    def test_prior_dominates_when_no_evidence(self):
        """With zero outcome_evidence, prior_strength fully determines resistance."""
        confidence = 0.5
        new = compute_bayesian_update(
            confidence=confidence,
            outcome_evidence=0.0,
            signal=1.0,
            weight=1.0,
            prior_strength=2.0,
        )
        # Prior=2.0, weight=1.0 → total=2.0, alpha=0.5*2+1.0*1=2.0, beta=0.5*2+0.0*1=1.0 → 2/3≈0.667
        assert abs(new - 2.0 / 3.0) < 0.001

    def test_good_outcome_increases_confidence(self):
        result = compute_bayesian_update(
            confidence=0.5, outcome_evidence=0.0, signal=1.0, weight=1.0, prior_strength=2.0
        )
        assert result > 0.5

    def test_bad_outcome_decreases_confidence(self):
        result = compute_bayesian_update(
            confidence=0.5, outcome_evidence=0.0, signal=0.0, weight=1.0, prior_strength=2.0
        )
        assert result < 0.5

    def test_neutral_signal_barely_changes_confidence(self):
        before = 0.7
        after = compute_bayesian_update(
            confidence=before, outcome_evidence=0.0, signal=0.5, weight=1.0, prior_strength=2.0
        )
        # signal=0.5 is neutral; confidence should stay near 0.7
        assert abs(after - before) < 0.1

    def test_converges_toward_1_after_many_good_outcomes(self):
        confidence = 0.5
        evidence = 0.0
        for _ in range(20):
            new = compute_bayesian_update(
                confidence=confidence,
                outcome_evidence=evidence,
                signal=1.0,
                weight=1.0,
                prior_strength=2.0,
            )
            evidence += 1.0
            confidence = new
        assert confidence > 0.90

    def test_converges_toward_0_after_many_bad_outcomes(self):
        confidence = 0.5
        evidence = 0.0
        for _ in range(20):
            new = compute_bayesian_update(
                confidence=confidence,
                outcome_evidence=evidence,
                signal=0.0,
                weight=1.0,
                prior_strength=2.0,
            )
            evidence += 1.0
            confidence = new
        assert confidence < 0.10

    def test_high_weight_moves_confidence_faster_than_low_weight(self):
        kwargs = dict(confidence=0.5, outcome_evidence=0.0, signal=1.0, prior_strength=2.0)
        low = compute_bayesian_update(**kwargs, weight=0.1)
        high = compute_bayesian_update(**kwargs, weight=5.0)
        assert high > low

    def test_output_always_in_range(self):
        for signal in [0.0, 0.25, 0.5, 0.75, 1.0]:
            result = compute_bayesian_update(
                confidence=0.3, outcome_evidence=10.0, signal=signal, weight=2.0, prior_strength=2.0
            )
            assert 0.0 <= result <= 1.0


# ── TestRecordOutcome ─────────────────────────────────────────────────────────


class TestRecordOutcome:
    """Integration tests via record_outcome() operation directly."""

    async def test_empty_block_ids_returns_zero_counts(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            result = await record_outcome(
                conn,
                block_ids=[],
                signal=0.8,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )
        assert result.blocks_updated == 0
        assert result.mean_confidence_delta == 0.0
        assert result.edges_reinforced == 0

    async def test_active_block_confidence_updated(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding)
            block_before = await get_block(conn, block_id)
            conf_before = block_before["confidence"]

            result = await record_outcome(
                conn,
                block_ids=[block_id],
                signal=1.0,
                weight=1.0,
                source="test",
                current_active_hours=2.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )

            block_after = await get_block(conn, block_id)

        assert result.blocks_updated == 1
        assert block_after["confidence"] > conf_before

    async def test_archived_block_silently_skipped(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "archived block")
            # Manually archive the block
            from elfmem.db.queries import update_block_status
            await update_block_status(conn, block_id, "archived", archive_reason="decayed")

            result = await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.9,
                weight=1.0,
                source="test",
                current_active_hours=2.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )

        assert result.blocks_updated == 0

    async def test_outcome_evidence_accumulates(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "accumulate block")

            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.8,
                weight=1.0,
                source="test",
                current_active_hours=2.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )
            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.8,
                weight=1.0,
                source="test",
                current_active_hours=3.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )

            block = await get_block(conn, block_id)

        # After 2 calls with weight=1.0 each, outcome_evidence should be 2.0
        assert abs(block["outcome_evidence"] - 2.0) < 0.001

    async def test_positive_signal_triggers_reinforcement(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "reinforce block")
            block_before = await get_block(conn, block_id)
            rc_before = block_before["reinforcement_count"]

            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.9,
                weight=1.0,
                source="test",
                current_active_hours=2.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )

            block_after = await get_block(conn, block_id)

        assert block_after["reinforcement_count"] == rc_before + 1

    async def test_negative_signal_skips_reinforcement(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "no reinforce")
            block_before = await get_block(conn, block_id)
            rc_before = block_before["reinforcement_count"]

            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.1,
                weight=1.0,
                source="test",
                current_active_hours=2.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )

            block_after = await get_block(conn, block_id)

        assert block_after["reinforcement_count"] == rc_before

    async def test_multi_block_positive_outcome_reinforces_edges(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            b1 = await _make_active_block(conn, mock_llm, mock_embedding, "edge block one")
            b2 = await _make_active_block(conn, mock_llm, mock_embedding, "edge block two")

            result = await record_outcome(
                conn,
                block_ids=[b1, b2],
                signal=0.9,
                weight=1.0,
                source="test",
                current_active_hours=2.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
            )

        assert result.edges_reinforced == 1

    async def test_valueerror_for_signal_below_zero(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            with pytest.raises(ValueError, match="signal"):
                await record_outcome(
                    conn,
                    block_ids=[],
                    signal=-0.1,
                    weight=1.0,
                    source="test",
                    current_active_hours=1.0,
                    prior_strength=2.0,
                    reinforce_threshold=0.5,
                )

    async def test_valueerror_for_signal_above_one(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            with pytest.raises(ValueError, match="signal"):
                await record_outcome(
                    conn,
                    block_ids=[],
                    signal=1.1,
                    weight=1.0,
                    source="test",
                    current_active_hours=1.0,
                    prior_strength=2.0,
                    reinforce_threshold=0.5,
                )

    async def test_valueerror_for_weight_zero_or_negative(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            with pytest.raises(ValueError, match="weight"):
                await record_outcome(
                    conn,
                    block_ids=[],
                    signal=0.5,
                    weight=0.0,
                    source="test",
                    current_active_hours=1.0,
                    prior_strength=2.0,
                    reinforce_threshold=0.5,
                )


# ── TestOutcomeAPI ─────────────────────────────────────────────────────────────


class TestOutcomeAPI:
    """Integration tests via MemorySystem.outcome()."""

    @pytest.fixture
    async def system(self):
        from elfmem import MemorySystem
        from elfmem.adapters.mock import MockEmbeddingService, MockLLMService

        engine = await create_test_engine()
        async with engine.begin() as conn:
            await seed_builtin_data(conn)

        system = MemorySystem(
            engine=engine,
            llm_service=MockLLMService(default_alignment=0.65),
            embedding_service=MockEmbeddingService(dimensions=64),
        )
        yield system
        await system.close()

    async def test_returns_outcome_result(self, system):
        result = await system.outcome([], signal=0.8)
        assert isinstance(result, OutcomeResult)

    async def test_str_result_is_agent_readable(self, system):
        result = await system.outcome([], signal=0.8)
        s = str(result)
        assert "Outcome recorded" in s

    async def test_to_dict_has_correct_keys(self, system):
        result = await system.outcome([], signal=0.8)
        d = result.to_dict()
        assert set(d.keys()) == {
            "blocks_updated", "mean_confidence_delta", "edges_reinforced", "blocks_penalized"
        }

    async def test_works_without_active_session(self, system):
        # No session started
        result = await system.outcome([], signal=0.5)
        assert result.blocks_updated == 0

    async def test_works_within_active_session(self, system):
        async with system.session():
            result = await system.outcome([], signal=0.5)
        assert result.blocks_updated == 0

    async def test_history_records_the_operation(self, system):
        await system.outcome([], signal=0.8)
        records = system.history()
        ops = [r.operation for r in records]
        assert "outcome" in ops

    async def test_guide_outcome_returns_full_guide_text(self, system):
        text = system.guide("outcome")
        assert "outcome" in text.lower()
        assert "signal" in text.lower()

    async def test_confidence_increases_after_positive_outcomes(self, system):
        """Acceptance: after 10 positive outcomes, block confidence measurably higher."""
        async with system.session():
            await system.learn("EUR/USD will rise based on momentum indicator")
            await system.consolidate()

        blocks = await system.recall("EUR/USD momentum")
        assert blocks, "Expected at least one block after consolidate"
        block_id = blocks[0].id
        confidence_initial = blocks[0].confidence

        for _ in range(10):
            await system.outcome([block_id], signal=0.9, source="brier")

        blocks_after = await system.recall("EUR/USD momentum")
        confidence_after = next(b.confidence for b in blocks_after if b.id == block_id)

        assert confidence_after > confidence_initial

    async def test_confidence_decreases_after_negative_outcomes(self, system):
        """Acceptance: after 10 negative outcomes, block confidence measurably lower."""
        async with system.session():
            await system.learn("Always buy high-volatility assets for quick gains")
            await system.consolidate()

        blocks = await system.recall("high-volatility assets")
        assert blocks, "Expected at least one block after consolidate"
        block_id = blocks[0].id
        confidence_initial = blocks[0].confidence

        for _ in range(10):
            await system.outcome([block_id], signal=0.1, source="brier")

        blocks_after = await system.recall("high-volatility assets")
        confidence_after = next(b.confidence for b in blocks_after if b.id == block_id)

        assert confidence_after < confidence_initial


# ── TestOutcomePenalize ────────────────────────────────────────────────────────


class TestOutcomePenalize:
    """Tests for the automatic penalize branch in record_outcome()."""

    async def test_outcome_penalizes_blocks_when_signal_below_threshold(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "penalize me")
            lambda_before = (await get_block(conn, block_id))["decay_lambda"]

            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.05,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

            lambda_after = (await get_block(conn, block_id))["decay_lambda"]

        assert lambda_after > lambda_before

    async def test_outcome_does_not_penalize_when_signal_above_threshold(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "no penalize")
            lambda_before = (await get_block(conn, block_id))["decay_lambda"]

            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.80,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

            lambda_after = (await get_block(conn, block_id))["decay_lambda"]

        assert lambda_after == lambda_before

    async def test_outcome_penalize_respects_lambda_ceiling(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "ceiling block")
            # Set lambda near ceiling so multiplication would exceed it
            await update_block_scoring(conn, block_id, decay_lambda=0.040)

            await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.05,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

            lambda_after = (await get_block(conn, block_id))["decay_lambda"]

        assert lambda_after <= 0.050

    async def test_outcome_penalize_skips_durable_blocks(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "durable block")
            await update_block_scoring(conn, block_id, decay_lambda=0.001)  # DURABLE tier

            result = await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.05,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

        assert result.blocks_penalized == 0

    async def test_outcome_penalize_skips_permanent_blocks(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "permanent block")
            await update_block_scoring(conn, block_id, decay_lambda=0.00001)  # PERMANENT tier

            result = await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.05,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

        assert result.blocks_penalized == 0

    async def test_outcome_result_includes_blocks_penalized_count(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "count me")

            result = await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.05,
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

        assert result.blocks_penalized == 1

    async def test_outcome_penalize_threshold_boundary_at_exactly_threshold(self, setup):
        """Signal exactly equal to penalize_threshold does NOT trigger penalization."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "boundary block")
            lambda_before = (await get_block(conn, block_id))["decay_lambda"]

            result = await record_outcome(
                conn,
                block_ids=[block_id],
                signal=0.20,  # exactly at threshold — NOT < threshold
                weight=1.0,
                source="test",
                current_active_hours=1.0,
                prior_strength=2.0,
                reinforce_threshold=0.5,
                penalize_threshold=0.20,
                penalty_factor=2.0,
                lambda_ceiling=0.050,
            )

            lambda_after = (await get_block(conn, block_id))["decay_lambda"]

        assert result.blocks_penalized == 0
        assert lambda_after == lambda_before
