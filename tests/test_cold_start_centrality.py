"""Integration test for v0.15.3 cold-start centrality floor.

Reproduces Dmitry's symptom (#50): a fresh block with strong semantic match
loses top-K retrieval to bedrock blocks on graph centrality alone. The floor
in ``effective_centrality()`` protects fresh blocks during the cold-start
window. See ``docs/plans/plan_v0.15.3_centrality_floor.md``.
"""

from __future__ import annotations

import pytest

from elfmem import ElfmemConfig, MemorySystem
from elfmem.config import MemoryConfig


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    """MemorySystem with low inbox_threshold for fast consolidation cycles."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    s = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    await s.begin_session()
    return s


class TestColdStartCentralityFloor:
    """Reproduce Dmitry's symptom and verify the v0.15.3 fix."""

    async def test_fresh_block_surfaces_alongside_bedrock(self, system) -> None:
        """A semantically relevant fresh block should appear in recall results
        even when many high-centrality bedrock blocks exist for the same query.

        Pre-fix: fresh block could lose top-K to bedrock on centrality alone
        (centrality contributes 0.105 to score gap, vs 0.015 for the cliff
        that v0.15.2 fixed). Post-fix: cold-start floor lifts the fresh
        block's centrality contribution; it can compete on its own merits.
        """
        # Build bedrock — many connected blocks that have been reinforced.
        bedrock_ids: list[str] = []
        for i in range(6):
            r = await system.learn(
                f"core principle {i}: agents must respect identity and principles"
            )
            bedrock_ids.append(r.block_id)
        await system.dream()

        # Manually connect bedrock blocks to give them high centrality.
        for i in range(len(bedrock_ids) - 1):
            await system.connect(bedrock_ids[i], bedrock_ids[i + 1])

        # Reinforce bedrock through outcome signals — these are well-validated blocks.
        for bid in bedrock_ids:
            await system.outcome([bid], signal=1.0)

        # Now learn a fresh block about a distinct topic — the agent has just
        # been told something specific that should surface.
        fresh = await system.learn(
            "important new insight: cold-start retrieval needs centrality protection"
        )
        await system.dream()

        # Query that semantically matches the fresh content, not the bedrock.
        results = await system.recall(
            "cold-start retrieval centrality protection insight",
            top_k=5,
        )

        # The fresh block should be in the top-K. Pre-fix, this assertion
        # could fail because bedrock dominated on centrality.
        result_ids = [r.id for r in results]
        assert fresh.block_id in result_ids, (
            f"Fresh block {fresh.block_id[:8]} should surface in top-5 "
            f"recall via cold-start centrality floor. Got: {result_ids}"
        )

    async def test_fresh_block_does_not_displace_bedrock_when_irrelevant(
        self, system
    ) -> None:
        """The floor should not let semantically-irrelevant fresh blocks
        dominate. Candidate selection (top-K by cosine) already filters
        the candidate set — the floor only ranks within already-relevant
        candidates, so an off-topic fresh block won't appear.
        """
        # Bedrock about topic A
        await system.learn("alpha topic: information about alpha")
        await system.learn("alpha details: alpha implementation notes")
        await system.learn("alpha applied: applying alpha in practice")
        await system.dream()

        # Fresh block about a totally unrelated topic.
        fresh_unrelated = await system.learn(
            "completely unrelated subject matter about omega"
        )
        await system.dream()

        # Query about alpha — the unrelated fresh block should NOT dominate.
        results = await system.recall("alpha implementation", top_k=3)
        result_ids = [r.id for r in results]
        # The fresh unrelated block may or may not be in the result set, but
        # it should not be ranked above all alpha blocks.
        if fresh_unrelated.block_id in result_ids:
            position = result_ids.index(fresh_unrelated.block_id)
            # If present, it must not be #1 — alpha-relevant blocks should win.
            assert position > 0, (
                "Fresh but semantically irrelevant block should not be top-1 "
                "over semantically-matching content."
            )
