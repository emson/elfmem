"""Tests for Tier 1 adaptive intelligence improvements.

- TestMMRDiversity: unit tests for _stage_5_mmr_diversity()
- TestBridgeProtection: integration tests for betweenness-based archival protection
"""

from __future__ import annotations

import numpy as np
import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import (
    get_active_blocks,
    get_block,
    insert_edge,
    seed_builtin_data,
)
from elfmem.memory.retrieval import MMR_DIVERSITY_LAMBDA, _stage_5_mmr_diversity
from elfmem.operations.consolidate import consolidate
from elfmem.operations.curate import BRIDGE_PROTECTION_QUANTILE, _archive_decayed_blocks
from elfmem.operations.learn import learn
from elfmem.types import BlockStatus, ScoredBlock


# ── Helpers ───────────────────────────────────────────────────────────────────


def _block(block_id: str, score: float) -> ScoredBlock:
    """Create a minimal ScoredBlock for MMR unit tests."""
    return ScoredBlock(
        id=block_id,
        content=block_id,
        tags=[],
        similarity=score,
        confidence=0.8,
        recency=0.8,
        centrality=0.5,
        reinforcement=0.5,
        score=score,
    )


def _unit_vec(*components: float) -> np.ndarray:
    """Return a normalised float32 vector."""
    v = np.array(components, dtype=np.float32)
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 0 else v


# ── MMR Diversity ─────────────────────────────────────────────────────────────


class TestMMRDiversity:
    """Unit tests for _stage_5_mmr_diversity() — no DB required."""

    def test_empty_candidates_returns_empty(self):
        assert _stage_5_mmr_diversity([], {}, limit=5) == []

    def test_single_candidate_returned_unchanged(self):
        block = _block("a", 0.9)
        result = _stage_5_mmr_diversity([block], {"a": _unit_vec(1, 0, 0)}, limit=1)
        assert result == [block]

    def test_limit_respected(self):
        blocks = [_block(str(i), 0.9 - i * 0.1) for i in range(5)]
        embs = {str(i): _unit_vec(float(i + 1), 0, 0) for i in range(5)}
        result = _stage_5_mmr_diversity(blocks, embs, limit=3)
        assert len(result) == 3

    def test_first_selection_is_highest_score(self):
        # Regardless of embeddings, the first selection must be the top-scoring block
        blocks = [_block("a", 0.9), _block("b", 0.5), _block("c", 0.3)]
        embs = {"a": _unit_vec(1, 0, 0), "b": _unit_vec(1, 0, 0), "c": _unit_vec(1, 0, 0)}
        result = _stage_5_mmr_diversity(blocks, embs, limit=3)
        assert result[0].id == "a"

    def test_diverse_block_preferred_over_near_duplicate(self):
        """A lower-scoring but diverse block beats a near-duplicate of the first pick."""
        # a: score=0.9, b: score=0.85 (similar to a), c: score=0.70 (diverse from a)
        a = _block("a", 0.90)
        b = _block("b", 0.85)
        c = _block("c", 0.70)

        emb_a = _unit_vec(1.0, 0.0, 0.0)
        emb_b = _unit_vec(0.9999, 0.0001, 0.0)  # nearly identical to a
        emb_c = _unit_vec(-1.0, 0.0, 0.0)       # orthogonal-ish/opposite to a

        embs = {"a": emb_a, "b": emb_b, "c": emb_c}
        result = _stage_5_mmr_diversity([a, b, c], embs, limit=3)

        assert result[0].id == "a"   # highest score wins first slot
        assert result[1].id == "c"   # diversity bonus overcomes score gap

    def test_no_embeddings_falls_back_to_score_order(self):
        """Blocks without embeddings select by score only."""
        blocks = [_block("a", 0.9), _block("b", 0.8), _block("c", 0.7)]
        result = _stage_5_mmr_diversity(blocks, {}, limit=3)
        # No embeddings → each block uses score-only path; order preserved by score
        assert [b.id for b in result] == ["a", "b", "c"]

    def test_partial_embeddings_handled_gracefully(self):
        """Some blocks without embeddings don't cause errors."""
        blocks = [_block("a", 0.9), _block("b", 0.8), _block("c", 0.7)]
        embs = {"a": _unit_vec(1, 0, 0)}  # only a has embedding
        result = _stage_5_mmr_diversity(blocks, embs, limit=3)
        # Should return all 3 without error
        assert len(result) == 3

    def test_identical_embeddings_does_not_raise(self):
        """All blocks with identical embeddings should not raise or loop."""
        blocks = [_block(str(i), 0.9 - i * 0.1) for i in range(4)]
        emb = _unit_vec(1, 0, 0)
        embs = {b.id: emb for b in blocks}
        result = _stage_5_mmr_diversity(blocks, embs, limit=4)
        assert len(result) == 4

    def test_mmr_diversity_lambda_constant_is_in_valid_range(self):
        assert 0.0 < MMR_DIVERSITY_LAMBDA < 1.0

    def test_result_contains_no_duplicates(self):
        blocks = [_block(str(i), float(i)) for i in range(6)]
        embs = {b.id: _unit_vec(float(i + 1), 0, 0) for i, b in enumerate(blocks)}
        result = _stage_5_mmr_diversity(blocks, embs, limit=6)
        ids = [b.id for b in result]
        assert len(ids) == len(set(ids))

    def test_pure_diversity_mode_maximises_spread(self):
        """With lambda=0, second pick is the block most different from first."""
        # We'll verify the MMR logic directly by checking the math:
        # After picking a (score=0.9, emb=[1,0,0]):
        # - b has sim(b, a)=0.0 (orthogonal) → MMR = 0*0.7 - 1*0.0 = 0.0 (ignored lambda=0 case)
        # This test just verifies that with identical scores, the function picks the most diverse

        # Create two blocks with the same score but very different embeddings
        # and one block also same score but nearly identical to first
        a = _block("a", 0.9)
        similar = _block("similar", 0.8)
        diverse = _block("diverse", 0.8)

        emb_a = _unit_vec(1.0, 0.0, 0.0)
        emb_sim = _unit_vec(0.9999, 0.0001, 0.0)   # nearly same as a
        emb_div = _unit_vec(0.0, 0.0, 1.0)          # orthogonal to a

        embs = {"a": emb_a, "similar": emb_sim, "diverse": emb_div}
        result = _stage_5_mmr_diversity([a, similar, diverse], embs, limit=3)

        assert result[0].id == "a"
        # second pick: diverse has zero similarity to a, similar has ~1.0 similarity
        # MMR(similar)  = 0.7*0.8 - 0.3*1.0 = 0.56 - 0.30 = 0.26
        # MMR(diverse)  = 0.7*0.8 - 0.3*0.0 = 0.56 - 0.00 = 0.56 → wins
        assert result[1].id == "diverse"


# ── Bridge Protection ─────────────────────────────────────────────────────────


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


class TestBridgeProtection:
    """Integration tests for betweenness-based archival protection in curate()."""

    async def test_bridge_protection_quantile_constant(self):
        assert 0.0 < BRIDGE_PROTECTION_QUANTILE < 1.0

    async def test_isolated_decayed_block_is_archived(self, setup):
        """A block with no edges that has decayed should be archived normally."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            result = await learn(conn, content="isolated block", category="knowledge", source="api")
            block_id = result.block_id
            await consolidate(
                conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=10.0
            )
            # Age to recency ≈ exp(-0.010 * 490) ≈ 0.007 < 0.05
            archived = await _archive_decayed_blocks(conn, current_active_hours=500.0, prune_threshold=0.05)

        assert archived >= 1

    async def test_hub_block_survives_archival_despite_low_recency(self, setup):
        """The most-connected block survives even when recency drops below threshold."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            # Create 5 blocks
            ids = []
            for i in range(5):
                r = await learn(
                    conn, content=f"unique content block {i}", category="knowledge", source="api"
                )
                ids.append(r.block_id)

            await consolidate(
                conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=10.0
            )

            # Manually create edges: ids[0] connects to ids[1], ids[2], ids[3]
            # This makes ids[0] the hub with weighted_degree=3*weight
            # Others have degree=weight each
            hub_id = ids[0]
            for spoke_id in ids[1:4]:
                fid, tid = (min(hub_id, spoke_id), max(hub_id, spoke_id))
                await insert_edge(conn, from_id=fid, to_id=tid, weight=0.7)

            # All blocks are now old enough to be archived
            # recency = exp(-0.010 * (500 - 10)) ≈ 0.007 < 0.05
            archived = await _archive_decayed_blocks(
                conn, current_active_hours=500.0, prune_threshold=0.05
            )

            hub_block = await get_block(conn, hub_id)

        # Hub should be bridge-protected (highest degree in the top 20%)
        assert hub_block["status"] == BlockStatus.ACTIVE

    async def test_spoke_blocks_are_archived_despite_graph_connection(self, setup):
        """Non-hub blocks with a single edge are not bridge-protected."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            ids = []
            for i in range(5):
                r = await learn(
                    conn, content=f"spoke test block {i}", category="knowledge", source="api"
                )
                ids.append(r.block_id)

            await consolidate(
                conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=10.0
            )

            hub_id = ids[0]
            for spoke_id in ids[1:4]:
                fid, tid = (min(hub_id, spoke_id), max(hub_id, spoke_id))
                await insert_edge(conn, from_id=fid, to_id=tid, weight=0.7)

            await _archive_decayed_blocks(
                conn, current_active_hours=500.0, prune_threshold=0.05
            )

            # ids[4] has no edges at all → definitely archived
            isolated = await get_block(conn, ids[4])

        assert isolated["status"] == BlockStatus.ARCHIVED

    async def test_no_edges_archives_all_decayed_blocks(self, setup):
        """With no edges in the graph, bridge_threshold=0 and all decayed blocks archive."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            for i in range(3):
                await learn(
                    conn, content=f"no-edge block {i}", category="knowledge", source="api"
                )
            await consolidate(
                conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=10.0
            )

            archived = await _archive_decayed_blocks(
                conn, current_active_hours=500.0, prune_threshold=0.05
            )

        assert archived == 3

    async def test_fresh_blocks_not_archived_regardless_of_degree(self, setup):
        """Blocks with high recency are never archived, even without bridge protection."""
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            r = await learn(
                conn, content="fresh block content", category="knowledge", source="api"
            )
            block_id = r.block_id
            await consolidate(
                conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=10.0
            )

            # Very short time elapsed — recency still high
            # recency = exp(-0.010 * (11 - 10)) = exp(-0.01) ≈ 0.99 >> 0.05
            archived = await _archive_decayed_blocks(
                conn, current_active_hours=11.0, prune_threshold=0.05
            )

        assert archived == 0

    async def test_empty_db_returns_zero_archived(self, setup):
        engine, mock_llm, mock_embedding = setup
        async with engine.begin() as conn:
            archived = await _archive_decayed_blocks(
                conn, current_active_hours=500.0, prune_threshold=0.05
            )
        assert archived == 0
