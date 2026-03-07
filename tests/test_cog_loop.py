"""Constitutional cognitive loop tests.

Verifies that the 10-block SELF seed, curate() auto-reinforcement,
and guarantee enforcement form a self-sustaining cognitive loop.
"""

from __future__ import annotations

import math

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.context.frames import SELF_FRAME
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import (
    add_tags,
    get_block,
    get_blocks_by_tag_pattern,
    seed_builtin_data,
    update_block_status,
    update_block_scoring,
)
from elfmem.memory.blocks import determine_decay_tier
from elfmem.operations.consolidate import consolidate
from elfmem.operations.curate import curate
from elfmem.operations.learn import learn
from elfmem.operations.recall import recall
from elfmem.scoring import LAMBDA
from elfmem.seed import CONSTITUTIONAL_SEED
from elfmem.types import DecayTier

TOL = 0.001


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def system_setup():
    """In-memory DB + mock services with constitutional tag overrides."""
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)

    mock_llm = MockLLMService(
        default_alignment=0.65,
        tag_overrides={"constitutional": ["self/constitutional"]},
    )
    mock_embedding = MockEmbeddingService(dimensions=128)

    yield engine, mock_llm, mock_embedding
    await engine.dispose()


# ── TC-CL-001: Seed module ────────────────────────────────────────────────────


class TestSeedModule:
    def test_seed_has_10_blocks(self) -> None:
        """CONSTITUTIONAL_SEED exports exactly 10 blocks."""
        assert len(CONSTITUTIONAL_SEED) == 10

    def test_all_blocks_have_constitutional_tag(self) -> None:
        """Every seed block carries the self/constitutional tag."""
        for block in CONSTITUTIONAL_SEED:
            assert "self/constitutional" in block["tags"], (
                f"Block missing self/constitutional: {block['content'][:60]}"
            )

    def test_all_blocks_have_secondary_tag(self) -> None:
        """Every block has a secondary tag (self/context, self/value, or self/goal)."""
        secondary = {"self/context", "self/value", "self/goal"}
        for block in CONSTITUTIONAL_SEED:
            tag_set = set(block["tags"])
            assert tag_set & secondary, (
                f"Block has no secondary tag: {block['content'][:60]}"
            )

    def test_all_blocks_have_non_empty_content(self) -> None:
        """Every seed block has substantial content (>50 chars)."""
        for block in CONSTITUTIONAL_SEED:
            assert len(block["content"]) > 50, (
                f"Block content too short: {block['content']!r}"
            )

    def test_identity_block_is_first(self) -> None:
        """First block is the identity block (self/constitutional + self/context)."""
        first = CONSTITUTIONAL_SEED[0]
        assert "self/context" in first["tags"]
        assert "elf" in first["content"].lower()

    def test_reflection_block_is_last(self) -> None:
        """Last block is the reflection / goal block (self/constitutional + self/goal)."""
        last = CONSTITUTIONAL_SEED[-1]
        assert "self/goal" in last["tags"]


# ── TC-CL-002: Decay tier ─────────────────────────────────────────────────────


class TestConstitutionalDecayTier:
    def test_constitutional_tag_assigns_permanent_tier(self) -> None:
        """Blocks tagged self/constitutional get DecayTier.PERMANENT."""
        tier = determine_decay_tier(["self/constitutional"], "knowledge")
        assert tier == DecayTier.PERMANENT

    def test_permanent_lambda_is_near_zero(self) -> None:
        """PERMANENT lambda is 0.00001 — effectively immortal."""
        lam = LAMBDA[DecayTier.PERMANENT]
        assert abs(lam - 0.00001) < 1e-9

    def test_constitutional_outranks_value(self) -> None:
        """self/constitutional takes priority over self/value for decay tier."""
        tier = determine_decay_tier(["self/constitutional", "self/value"], "knowledge")
        assert tier == DecayTier.PERMANENT

    def test_constitutional_survives_long_decay(self) -> None:
        """Recency > 0.90 after 10,000 active hours with PERMANENT decay."""
        lam = LAMBDA[DecayTier.PERMANENT]
        recency = math.exp(-lam * 10_000)
        assert recency > 0.90, f"Constitutional recency after 10k hours: {recency:.4f}"


# ── TC-CL-003: Seed idempotency ───────────────────────────────────────────────


class TestSeedIdempotency:
    async def test_seeding_twice_creates_no_duplicates(self, system_setup) -> None:
        """Calling learn() with constitutional content twice → second is duplicate_rejected."""
        engine, _, _ = system_setup
        first_block = CONSTITUTIONAL_SEED[0]

        async with engine.begin() as conn:
            r1 = await learn(
                conn,
                content=first_block["content"],
                tags=first_block["tags"],
                category="knowledge",
                source="api",
            )
            r2 = await learn(
                conn,
                content=first_block["content"],
                tags=first_block["tags"],
                category="knowledge",
                source="api",
            )

        assert r1.status == "created"
        assert r2.status == "duplicate_rejected"

    async def test_all_10_seed_blocks_can_be_stored(self, system_setup) -> None:
        """All 10 CONSTITUTIONAL_SEED blocks can be stored without error."""
        engine, _, _ = system_setup

        async with engine.begin() as conn:
            results = []
            for block in CONSTITUTIONAL_SEED:
                r = await learn(
                    conn,
                    content=block["content"],
                    tags=block["tags"],
                    category="knowledge",
                    source="api",
                )
                results.append(r)

        created = [r for r in results if r.status == "created"]
        assert len(created) == 10


# ── TC-CL-004: curate() auto-reinforces constitutional ───────────────────────


class TestCurateConstitutionalReinforcement:
    async def test_curate_reinforces_constitutional_blocks(self, system_setup) -> None:
        """curate() reinforces all active constitutional blocks and reports the count."""
        engine, mock_llm, mock_embedding = system_setup

        async with engine.begin() as conn:
            # Create and consolidate a constitutional block
            r = await learn(
                conn,
                content=CONSTITUTIONAL_SEED[2]["content"],  # Curiosity block
                tags=CONSTITUTIONAL_SEED[2]["tags"],
                category="knowledge",
                source="api",
            )
            block_id = r.block_id
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Verify it is active with constitutional tag
            constitutional_ids = await get_blocks_by_tag_pattern(conn, "self/constitutional")
            assert block_id in constitutional_ids

            block_before = await get_block(conn, block_id)
            rc_before = block_before["reinforcement_count"]

            # Run curate
            result = await curate(conn, current_active_hours=100.0)

            block_after = await get_block(conn, block_id)
            rc_after = block_after["reinforcement_count"]

        assert result.constitutional_reinforced >= 1
        assert rc_after > rc_before

    async def test_curate_constitutional_reinforced_zero_when_none_exist(
        self, system_setup
    ) -> None:
        """CurateResult.constitutional_reinforced is 0 when no constitutional blocks exist."""
        engine, mock_llm, mock_embedding = system_setup

        async with engine.begin() as conn:
            # Learn a plain knowledge block (no constitutional tag)
            await learn(conn, content="some plain fact", category="knowledge", source="api")
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            result = await curate(conn, current_active_hours=100.0)

        assert result.constitutional_reinforced == 0

    async def test_curate_result_includes_constitutional_reinforced_field(
        self, system_setup
    ) -> None:
        """CurateResult.to_dict() always includes constitutional_reinforced."""
        engine, _, _ = system_setup

        async with engine.begin() as conn:
            result = await curate(conn, current_active_hours=10.0)

        d = result.to_dict()
        assert "constitutional_reinforced" in d
        assert isinstance(d["constitutional_reinforced"], int)


# ── TC-CL-005: Guarantee enforcement in SELF frame ───────────────────────────


class TestSelfFrameGuarantees:
    async def test_constitutional_blocks_guaranteed_in_self_frame(
        self, system_setup
    ) -> None:
        """Constitutional blocks appear in SELF frame result regardless of score."""
        engine, mock_llm, mock_embedding = system_setup

        async with engine.begin() as conn:
            # Create ONE constitutional block (will have lower reinforcement)
            r_const = await learn(
                conn,
                content=CONSTITUTIONAL_SEED[0]["content"],  # Identity
                tags=CONSTITUTIONAL_SEED[0]["tags"],
                category="knowledge",
                source="api",
            )
            constitutional_id = r_const.block_id

            # Create several domain blocks with same tag prefix (self/value)
            for i in range(6):
                await learn(
                    conn,
                    content=f"domain value block number {i} about performance and growth",
                    tags=["self/value"],
                    category="knowledge",
                    source="api",
                )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Recall SELF frame (top_k=3 to stress the guarantee slots)
            result = await recall(
                conn,
                embedding_svc=mock_embedding,
                frame_def=SELF_FRAME,
                query=None,
                current_active_hours=10.0,
                top_k=3,
            )

        returned_ids = [b.id for b in result.blocks]
        assert constitutional_id in returned_ids, (
            f"Constitutional block {constitutional_id} not in SELF frame result: {returned_ids}"
        )

    async def test_curate_result_summary_includes_constitutional_when_nonzero(
        self, system_setup
    ) -> None:
        """CurateResult.summary mentions constitutional reinforcement when > 0."""
        engine, mock_llm, mock_embedding = system_setup

        async with engine.begin() as conn:
            await learn(
                conn,
                content=CONSTITUTIONAL_SEED[1]["content"],
                tags=CONSTITUTIONAL_SEED[1]["tags"],
                category="knowledge",
                source="api",
            )
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            result = await curate(conn, current_active_hours=100.0)

        assert result.constitutional_reinforced >= 1
        assert "constitutional" in str(result)
