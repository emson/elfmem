"""Tests for outcome scoring (outcome()).

Sections:
  - TestComputeBayesianUpdate  — pure-function unit tests (no DB)
  - TestRecordOutcome          — operation-level integration (confidence, penalise, edges)
  - TestOutcomeAPI             — end-to-end via MemorySystem.outcome()
  - TestOutcomePenalize        — penalisation branch (decay_lambda escalation)
  - TestOutcomeDrivenEdges     — edge creation and reinforcement
"""

from __future__ import annotations

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_block, get_edges_for_block, seed_builtin_data, update_block_scoring
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn
from elfmem.operations.outcome import compute_bayesian_update, record_outcome
from elfmem.types import OutcomeResult


# ── Fixtures ───────────────────────────────────────────────────────────────────


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


# ── Bayesian update — pure function ───────────────────────────────────────────


class TestComputeBayesianUpdate:
    """Unit tests for compute_bayesian_update() — no DB required."""

    def test_good_outcome_increases_confidence(self):
        result = compute_bayesian_update(confidence=0.5, outcome_evidence=0.0, signal=1.0, weight=1.0, prior_strength=2.0)
        assert result > 0.5

    def test_bad_outcome_decreases_confidence(self):
        result = compute_bayesian_update(confidence=0.5, outcome_evidence=0.0, signal=0.0, weight=1.0, prior_strength=2.0)
        assert result < 0.5

    def test_converges_toward_1_after_many_good_outcomes(self):
        confidence, evidence = 0.5, 0.0
        for _ in range(20):
            confidence = compute_bayesian_update(confidence=confidence, outcome_evidence=evidence, signal=1.0, weight=1.0, prior_strength=2.0)
            evidence += 1.0
        assert confidence > 0.90

    def test_converges_toward_0_after_many_bad_outcomes(self):
        confidence, evidence = 0.5, 0.0
        for _ in range(20):
            confidence = compute_bayesian_update(confidence=confidence, outcome_evidence=evidence, signal=0.0, weight=1.0, prior_strength=2.0)
            evidence += 1.0
        assert confidence < 0.10

    def test_output_always_in_range(self):
        for signal in [0.0, 0.25, 0.5, 0.75, 1.0]:
            result = compute_bayesian_update(confidence=0.3, outcome_evidence=10.0, signal=signal, weight=2.0, prior_strength=2.0)
            assert 0.0 <= result <= 1.0


# ── record_outcome() ───────────────────────────────────────────────────────────


class TestRecordOutcome:
    """Integration tests via record_outcome() operation."""

    async def test_empty_block_ids_returns_zero_counts(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            result = await record_outcome(
                conn, block_ids=[], signal=0.8, weight=1.0, source="test",
                current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
        assert result.blocks_updated == 0
        assert result.mean_confidence_delta == 0.0

    async def test_active_block_confidence_updated(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding)
            conf_before = (await get_block(conn, block_id))["confidence"]
            result = await record_outcome(
                conn, block_ids=[block_id], signal=1.0, weight=1.0, source="test",
                current_active_hours=2.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            conf_after = (await get_block(conn, block_id))["confidence"]
        assert result.blocks_updated == 1
        assert conf_after > conf_before

    async def test_archived_block_silently_skipped(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "archived block")
            from elfmem.db.queries import update_block_status
            await update_block_status(conn, block_id, "archived", archive_reason="decayed")
            result = await record_outcome(
                conn, block_ids=[block_id], signal=0.9, weight=1.0, source="test",
                current_active_hours=2.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
        assert result.blocks_updated == 0

    async def test_outcome_evidence_accumulates(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "accumulate block")
            for h in (2.0, 3.0):
                await record_outcome(
                    conn, block_ids=[block_id], signal=0.8, weight=1.0, source="test",
                    current_active_hours=h, prior_strength=2.0, reinforce_threshold=0.5,
                )
            block = await get_block(conn, block_id)
        assert abs(block["outcome_evidence"] - 2.0) < 0.001

    async def test_positive_signal_triggers_reinforcement(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "reinforce block")
            rc_before = (await get_block(conn, block_id))["reinforcement_count"]
            await record_outcome(
                conn, block_ids=[block_id], signal=0.9, weight=1.0, source="test",
                current_active_hours=2.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            rc_after = (await get_block(conn, block_id))["reinforcement_count"]
        assert rc_after == rc_before + 1

    async def test_negative_signal_skips_reinforcement(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "no reinforce")
            rc_before = (await get_block(conn, block_id))["reinforcement_count"]
            await record_outcome(
                conn, block_ids=[block_id], signal=0.1, weight=1.0, source="test",
                current_active_hours=2.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            rc_after = (await get_block(conn, block_id))["reinforcement_count"]
        assert rc_after == rc_before

    async def test_valueerror_for_signal_below_zero(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            with pytest.raises(ValueError, match="signal"):
                await record_outcome(
                    conn, block_ids=[], signal=-0.1, weight=1.0, source="test",
                    current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                )

    async def test_valueerror_for_signal_above_one(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            with pytest.raises(ValueError, match="signal"):
                await record_outcome(
                    conn, block_ids=[], signal=1.1, weight=1.0, source="test",
                    current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                )

    async def test_valueerror_for_weight_zero_or_negative(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            with pytest.raises(ValueError, match="weight"):
                await record_outcome(
                    conn, block_ids=[], signal=0.5, weight=0.0, source="test",
                    current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                )


# ── MemorySystem.outcome() — API-level ─────────────────────────────────────────


class TestOutcomeAPI:
    """Integration tests via MemorySystem.outcome()."""

    @pytest.fixture
    async def system(self):
        from elfmem import MemorySystem
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
        assert isinstance(await system.outcome([], signal=0.8), OutcomeResult)

    async def test_works_without_active_session(self, system):
        result = await system.outcome([], signal=0.5)
        assert result.blocks_updated == 0

    async def test_works_within_active_session(self, system):
        async with system.session():
            result = await system.outcome([], signal=0.5)
        assert result.blocks_updated == 0

    async def test_confidence_increases_after_positive_outcomes(self, system):
        """Acceptance: 10 positive outcomes raise block confidence."""
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
        """Acceptance: 10 negative outcomes lower block confidence."""
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


# ── Penalisation branch ────────────────────────────────────────────────────────


class TestOutcomePenalize:
    async def test_outcome_penalizes_blocks_when_signal_below_threshold(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "penalize me")
            lambda_before = (await get_block(conn, block_id))["decay_lambda"]
            await record_outcome(
                conn, block_ids=[block_id], signal=0.05, weight=1.0, source="test",
                current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                penalize_threshold=0.20, penalty_factor=2.0, lambda_ceiling=0.050,
            )
            lambda_after = (await get_block(conn, block_id))["decay_lambda"]
        assert lambda_after > lambda_before

    async def test_outcome_does_not_penalize_when_signal_above_threshold(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "no penalize")
            lambda_before = (await get_block(conn, block_id))["decay_lambda"]
            await record_outcome(
                conn, block_ids=[block_id], signal=0.80, weight=1.0, source="test",
                current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                penalize_threshold=0.20, penalty_factor=2.0, lambda_ceiling=0.050,
            )
            lambda_after = (await get_block(conn, block_id))["decay_lambda"]
        assert lambda_after == lambda_before

    async def test_outcome_penalize_respects_lambda_ceiling(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "ceiling block")
            await update_block_scoring(conn, block_id, decay_lambda=0.040)
            await record_outcome(
                conn, block_ids=[block_id], signal=0.05, weight=1.0, source="test",
                current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                penalize_threshold=0.20, penalty_factor=2.0, lambda_ceiling=0.050,
            )
            lambda_after = (await get_block(conn, block_id))["decay_lambda"]
        assert lambda_after <= 0.050

    async def test_outcome_penalize_skips_durable_blocks(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "durable block")
            await update_block_scoring(conn, block_id, decay_lambda=0.001)  # DURABLE tier
            result = await record_outcome(
                conn, block_ids=[block_id], signal=0.05, weight=1.0, source="test",
                current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                penalize_threshold=0.20, penalty_factor=2.0, lambda_ceiling=0.050,
            )
        assert result.blocks_penalized == 0

    async def test_outcome_penalize_threshold_boundary_exactly_at_threshold(self, setup):
        """Signal exactly equal to penalize_threshold does NOT trigger penalisation."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            block_id = await _make_active_block(conn, mock_llm, mock_embedding, "boundary block")
            lambda_before = (await get_block(conn, block_id))["decay_lambda"]
            result = await record_outcome(
                conn, block_ids=[block_id], signal=0.20, weight=1.0, source="test",
                current_active_hours=1.0, prior_strength=2.0, reinforce_threshold=0.5,
                penalize_threshold=0.20, penalty_factor=2.0, lambda_ceiling=0.050,
            )
            lambda_after = (await get_block(conn, block_id))["decay_lambda"]
        assert result.blocks_penalized == 0
        assert lambda_after == lambda_before


# ── Outcome-driven edge creation ───────────────────────────────────────────────


class TestOutcomeDrivenEdges:
    """Outcome-driven edge creation: co-used blocks get connected."""

    async def test_outcome_creates_edge_between_non_similar_blocks(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            b1 = await _make_active_block(conn, mock_llm, mock_embedding, "orbital mechanics alpha")
            b2 = await _make_active_block(conn, mock_llm, mock_embedding, "sourdough bread recipe")
            result = await record_outcome(
                conn, block_ids=[b1, b2], signal=0.9, weight=1.0, source="test",
                current_active_hours=5.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            edges_b1 = await get_edges_for_block(conn, b1)
        assert result.outcome_edges_created + result.edges_reinforced == 1
        assert len(edges_b1) >= 1

    async def test_outcome_reinforces_existing_similarity_edge_not_duplicates(self, setup):
        engine, mock_llm, mock_embedding = setup
        embedding_with_edge = MockEmbeddingService(
            dimensions=64,
            similarity_overrides={frozenset({"block similar a", "block similar b"}): 0.85},
        )
        async with engine.begin() as conn:
            r1 = await learn(conn, content="block similar a", category="knowledge", source="api")
            r2 = await learn(conn, content="block similar b", category="knowledge", source="api")
            await consolidate(conn, llm=mock_llm, embedding_svc=embedding_with_edge, current_active_hours=1.0)
            b1, b2 = r1.block_id, r2.block_id
            rc_before = (await get_edges_for_block(conn, b1))[0]["reinforcement_count"]
            result = await record_outcome(
                conn, block_ids=[b1, b2], signal=0.9, weight=1.0, source="test",
                current_active_hours=5.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            all_edges = await get_edges_for_block(conn, b1)
        assert len(all_edges) == 1, "No duplicate edge should be created"
        assert result.edges_reinforced == 1
        assert all_edges[0]["reinforcement_count"] == rc_before + 1

    async def test_low_signal_does_not_create_edges(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            b1 = await _make_active_block(conn, mock_llm, mock_embedding, "cold fusion perhaps")
            b2 = await _make_active_block(conn, mock_llm, mock_embedding, "medieval tapestry")
            result = await record_outcome(
                conn, block_ids=[b1, b2], signal=0.3, weight=1.0, source="test",
                current_active_hours=5.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
        assert result.outcome_edges_created == 0
        assert result.edges_reinforced == 0

    async def test_single_block_outcome_creates_no_edges(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            b1 = await _make_active_block(conn, mock_llm, mock_embedding, "lonely block")
            result = await record_outcome(
                conn, block_ids=[b1], signal=1.0, weight=1.0, source="test",
                current_active_hours=5.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
        assert result.outcome_edges_created == 0
        assert result.edges_reinforced == 0

    async def test_three_blocks_creates_three_pairs(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            b1 = await _make_active_block(conn, mock_llm, mock_embedding, "alpha centauri light")
            b2 = await _make_active_block(conn, mock_llm, mock_embedding, "pickling vegetables")
            b3 = await _make_active_block(conn, mock_llm, mock_embedding, "morse code history")
            result = await record_outcome(
                conn, block_ids=[b1, b2, b3], signal=0.9, weight=1.0, source="test",
                current_active_hours=5.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
        assert result.outcome_edges_created + result.edges_reinforced == 3

    async def test_outcome_idempotent_second_call_reinforces_not_duplicates(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            b1 = await _make_active_block(conn, mock_llm, mock_embedding, "dark matter physics")
            b2 = await _make_active_block(conn, mock_llm, mock_embedding, "origami cranes")
            await record_outcome(
                conn, block_ids=[b1, b2], signal=0.9, weight=1.0, source="test",
                current_active_hours=5.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            result2 = await record_outcome(
                conn, block_ids=[b1, b2], signal=0.9, weight=1.0, source="test",
                current_active_hours=6.0, prior_strength=2.0, reinforce_threshold=0.5,
            )
            all_edges = await get_edges_for_block(conn, b1)
        assert len(all_edges) == 1, "Must not duplicate the outcome edge"
        assert result2.outcome_edges_created == 0
        assert result2.edges_reinforced == 1
