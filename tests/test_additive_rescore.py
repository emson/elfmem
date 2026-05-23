"""Tests for v0.17 additive rescore — Beta-Binomial update on (α, β).

The headline change of the v0.17 scoring bundle: rescore no longer clobbers
``confidence`` with the new alignment score. It folds the new alignment in as
one weighted evidence event on the Beta posterior, so mature blocks
(α + β ≫ 1) barely move on rescore while cold blocks (still at the
promotion prior, α + β = 1) track the new alignment closely.

The regression fixture below pins the empirical damage-reduction number
that justifies the release: at α=15, β=2 (confidence ≈ 0.882), a rescore
with alignment=0.55 and weight=0.5 drops confidence by ≈ 0.009 instead of
the 0.332 the old clobber would have produced. See ADR 0002 and
docs/plans/plan_memory_scoring.md.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_block, insert_block, seed_builtin_data
from elfmem.operations.rescore import rescore_blocks


@pytest.fixture
async def system_with_engine():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    llm = MockLLMService(default_alignment=0.55)
    embedding = MockEmbeddingService(dimensions=64)
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    system = MemorySystem(
        engine=engine, llm_service=llm, embedding_service=embedding, config=cfg,
    )
    yield system, engine, llm, embedding
    await engine.dispose()


async def _seed_mature_block(
    engine, *, block_id: str, alpha: float, beta: float, content: str = "mature",
) -> None:
    """Insert an active block with explicit Beta sufficient statistics."""
    async with engine.begin() as conn:
        confidence = alpha / (alpha + beta)
        await insert_block(
            conn,
            block_id=block_id,
            content=content,
            category="knowledge",
            source="test",
            status="active",
            confidence=confidence,
            success_count=alpha,
            failure_count=beta,
        )
        # The rescore eligibility query requires last_scored_at presence to be
        # NULL or stale; rescore_blocks itself doesn't check that — it only
        # checks status. So no further setup is needed here.


class TestAdditiveRescoreRegression:
    """The headline number: α=15, β=2 mature block barely moves on rescore."""

    async def test_rescore_additive_preserves_earned_evidence(
        self, system_with_engine,
    ):
        system, engine, llm, _ = system_with_engine
        # A mature block: 15 successes vs 2 failures. confidence ≈ 0.882.
        await _seed_mature_block(engine, block_id="mature1", alpha=15.0, beta=2.0)

        # Run rescore at the production-default evidence_weight=0.5 with the
        # new alignment at 0.55 (significant drop from 0.882).
        llm.default_alignment = 0.55
        async with engine.begin() as conn:
            result = await rescore_blocks(
                conn, block_ids=["mature1"],
                llm=system._llm, embedding_svc=system._embedding,
                evidence_weight=0.5,
            )
        assert result["rescored"] == 1

        async with engine.connect() as conn:
            block = await get_block(conn, "mature1")
        new_confidence = float(block["confidence"])

        # Damage reduction headline (ADR 0002):
        #   old clobber: 0.882 → 0.550 (Δ = 0.332)
        #   new additive: 0.882 → ≈ 0.873 (Δ ≈ 0.009)
        # We assert the new behaviour with a generous tolerance — the
        # mathematical value is (15 + 0.55*0.5) / (15 + 0.55*0.5 + 2 + 0.45*0.5)
        # = 15.275 / 17.5 ≈ 0.873.
        assert abs(new_confidence - 0.873) < 0.005
        # And: the new value is far closer to the old confidence than to
        # the new alignment — the inverse of the old clobber behaviour.
        assert abs(new_confidence - 0.882) < 0.05
        assert abs(new_confidence - 0.55) > 0.30


class TestAdditiveRescoreWeights:
    """Verify the evidence_weight knob behaves as designed."""

    async def test_rescore_weight_zero_is_noop(self, system_with_engine):
        """weight=0.0 leaves (α, β) untouched — alignment metadata only."""
        system, engine, llm, _ = system_with_engine
        await _seed_mature_block(engine, block_id="w0", alpha=15.0, beta=2.0)

        llm.default_alignment = 0.20  # would massively pull confidence down
        async with engine.begin() as conn:
            await rescore_blocks(
                conn, block_ids=["w0"],
                llm=system._llm, embedding_svc=system._embedding,
                evidence_weight=0.0,
            )

        async with engine.connect() as conn:
            block = await get_block(conn, "w0")
        # α and β unchanged; confidence still ≈ 15/17.
        assert abs(float(block["success_count"]) - 15.0) < 1e-9
        assert abs(float(block["failure_count"]) - 2.0) < 1e-9
        assert abs(float(block["confidence"]) - 15.0 / 17.0) < 1e-9

    async def test_rescore_weight_two_moves_more_than_one(
        self, system_with_engine,
    ):
        """Higher evidence_weight ⇒ larger confidence movement on the same signal."""
        system, engine, llm, _ = system_with_engine
        # Two cold blocks at the same starting posterior so we can compare
        # how far each moves under different weights.
        await _seed_mature_block(engine, block_id="w1", alpha=2.0, beta=2.0)
        await _seed_mature_block(engine, block_id="w2", alpha=2.0, beta=2.0)

        llm.default_alignment = 1.0  # maximally positive evidence
        async with engine.begin() as conn:
            await rescore_blocks(
                conn, block_ids=["w1"],
                llm=system._llm, embedding_svc=system._embedding,
                evidence_weight=1.0,
            )
            await rescore_blocks(
                conn, block_ids=["w2"],
                llm=system._llm, embedding_svc=system._embedding,
                evidence_weight=2.0,
            )

        async with engine.connect() as conn:
            b1 = await get_block(conn, "w1")
            b2 = await get_block(conn, "w2")

        move1 = float(b1["confidence"]) - 0.5
        move2 = float(b2["confidence"]) - 0.5
        assert move2 > move1 > 0.0


class TestPromotionSeedsConsistentPriors:
    """Promoted blocks land with α = confidence, β = 1 - confidence."""

    async def test_consolidate_promotes_with_consistent_alpha_beta(self):
        """A consolidate-promoted block has α / (α + β) == confidence."""
        engine = await create_test_engine()
        async with engine.begin() as conn:
            await seed_builtin_data(conn)
        llm = MockLLMService(default_alignment=0.72)
        embedding = MockEmbeddingService(dimensions=64)
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
        system = MemorySystem(
            engine=engine, llm_service=llm, embedding_service=embedding, config=cfg,
        )
        try:
            await system.learn("promotion fact 1")
            await system.learn("promotion fact 2")
            await system.learn("promotion fact 3")
            await system.consolidate()

            async with engine.connect() as conn:
                rows = (await conn.execute(text(
                    "SELECT id, confidence, success_count, failure_count "
                    "FROM blocks WHERE status='active' AND category='knowledge'"
                ))).fetchall()
            assert rows, "consolidate produced no active blocks"
            for _id, conf, alpha, beta in rows:
                total = float(alpha) + float(beta)
                # Total prior mass at promotion is exactly 1.0 (α + β = 1).
                assert abs(total - 1.0) < 1e-9
                # And confidence is consistent with the priors.
                assert abs(float(conf) - float(alpha) / total) < 1e-9
        finally:
            await engine.dispose()

    async def test_peer_import_promotes_with_consistent_alpha_beta(self):
        """A peer-imported block has α = confidence, β = 1 - confidence."""
        engine = await create_test_engine()
        async with engine.begin() as conn:
            await seed_builtin_data(conn)
            # Mimic peer import: insert_block with status='inbox' and an
            # explicit confidence — α / β default from confidence.
            await insert_block(
                conn,
                block_id="peer1",
                content="from a peer",
                category="knowledge",
                source="peer_import",
                status="inbox",
                confidence=0.45,
            )
            block = await get_block(conn, "peer1")
        try:
            alpha = float(block["success_count"])
            beta = float(block["failure_count"])
            assert abs(alpha - 0.45) < 1e-9
            assert abs(beta - 0.55) < 1e-9
            assert abs(alpha / (alpha + beta) - float(block["confidence"])) < 1e-9
        finally:
            await engine.dispose()


class TestConfig:
    """rescore_evidence_weight validation: zero allowed, negative rejected."""

    def test_default_is_half(self):
        cfg = MemoryConfig()
        assert cfg.rescore_evidence_weight == 0.5

    def test_zero_is_accepted(self):
        cfg = MemoryConfig(rescore_evidence_weight=0.0)
        assert cfg.rescore_evidence_weight == 0.0

    def test_negative_is_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MemoryConfig(rescore_evidence_weight=-0.1)
