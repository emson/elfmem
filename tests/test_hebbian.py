"""Hebbian co-retrieval edge creation tests (C1 + simulation fixes).

Validates that frame() staging promotes unconnected pairs to co_occurs edges
at the configured threshold, with four correctness properties:
  1. Cache hits do not count as retrieval signal
  2. Per-session dedup: each pair counts at most once per begin_session() cycle
  3. Zombie cleanup: curate() removes staging entries for archived blocks
  4. edges_promoted surfaced on FrameResult

Tests use threshold=2 and cosine_override=0.10 between test blocks to prevent
consolidation from creating a similarity edge (MINIMUM_COSINE_FOR_EDGE=0.30).
"""

from __future__ import annotations

import pytest

from elfmem.adapters.mock import MockEmbeddingService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.queries import get_edge, insert_edge, update_block_status
from elfmem.types import BlockStatus, Edge

ALPHA = "hebbian alpha"
BETA = "hebbian beta"


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
async def system(test_engine, mock_llm) -> MemorySystem:
    """MemorySystem with threshold=2 and no similarity edge between test blocks."""
    emb = MockEmbeddingService(
        similarity_overrides={frozenset({ALPHA, BETA}): 0.10}
    )
    cfg = ElfmemConfig(memory=MemoryConfig(
        inbox_threshold=2,              # dream() works with 2 blocks
        co_retrieval_edge_threshold=2,  # fast promotion — 2 distinct sessions needed
    ))
    s = MemorySystem(engine=test_engine, llm_service=mock_llm, embedding_service=emb, config=cfg)
    await s.begin_session()
    return s


async def _two_active_no_edge(system: MemorySystem) -> tuple[str, str]:
    """Learn and consolidate 2 blocks guaranteed to have no similarity edge."""
    r1 = await system.learn(ALPHA)
    r2 = await system.learn(BETA)
    await system.dream()
    return r1.block_id, r2.block_id


async def _new_session(system: MemorySystem) -> None:
    """Rotate to a fresh session, clearing per-session dedup state."""
    await system.end_session()
    await system.begin_session()


# ── Core staging tests ─────────────────────────────────────────────────────────


class TestHebbianCoRetrieval:

    async def test_staging_count_increments_on_co_retrieval(self, system) -> None:
        """TC-H-001: After first frame() with 2 unconnected blocks, staging_count == 1."""
        await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 1

    async def test_co_retrieval_edge_promoted_at_threshold(self, system, test_engine) -> None:
        """TC-H-002: Across threshold distinct sessions, co_occurs edge is created."""
        # threshold=2 → need 2 separate sessions to promote
        b1, b2 = await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")   # session 1: count → 1
        await _new_session(system)
        await system.frame("attention", query="hebbian")   # session 2: count → 2 → PROMOTED
        from_id, to_id = Edge.canonical(b1, b2)
        async with test_engine.connect() as conn:
            edge = await get_edge(conn, from_id, to_id)
        assert edge is not None
        assert edge["origin"] == "co_retrieval"
        assert edge["relation_type"] == "co_occurs"

    async def test_staging_cleared_after_promotion(self, system) -> None:
        """TC-H-003: Staging count drops to 0 after cross-session promotion."""
        await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")
        await _new_session(system)
        await system.frame("attention", query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 0

    async def test_existing_edge_pair_not_staged(self, system, test_engine) -> None:
        """TC-H-004: Pair with pre-existing edge is not incremented in staging."""
        b1, b2 = await _two_active_no_edge(system)
        from_id, to_id = Edge.canonical(b1, b2)
        async with test_engine.begin() as conn:
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.70)
        await system.frame("attention", query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 0

    async def test_raw_recall_does_not_stage(self, system) -> None:
        """TC-H-005: MemorySystem.recall() does not trigger Hebbian staging."""
        await _two_active_no_edge(system)
        await system.recall(query="hebbian")
        assert (await system.status()).co_retrieval_staging_count == 0


# ── Fix 1: Cache-aware staging ─────────────────────────────────────────────────


class TestCacheAwareStaging:

    async def test_same_session_second_frame_does_not_double_count(self, system) -> None:
        """TC-H-006: Calling frame() twice in one session counts pair only once (per-session dedup).

        Without dedup, 2 calls in one session with threshold=2 would promote immediately.
        With dedup, the pair is only staged once per session — promotion requires 2 sessions.
        """
        await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")
        await system.frame("attention", query="hebbian")  # same session — should NOT double-count
        # staging_count still 1 (not 2, not 0) — pair was counted once, threshold=2 not reached
        assert (await system.status()).co_retrieval_staging_count == 1


# ── Fix 2: edges_promoted on FrameResult ───────────────────────────────────────


class TestEdgesPromotedObservability:

    async def test_edges_promoted_zero_before_threshold(self, system) -> None:
        """TC-H-007: frame() result.edges_promoted == 0 when threshold not reached."""
        await _two_active_no_edge(system)
        result = await system.frame("attention", query="hebbian")
        assert result.edges_promoted == 0

    async def test_edges_promoted_nonzero_at_threshold(self, system) -> None:
        """TC-H-008: frame() result.edges_promoted == 1 exactly when pair is promoted."""
        await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")   # session 1
        await _new_session(system)
        result = await system.frame("attention", query="hebbian")  # session 2 → promotion
        assert result.edges_promoted == 1


# ── Fix 3: Zombie cleanup in curate() ─────────────────────────────────────────


class TestZombieCleanup:

    async def test_curate_removes_staging_entries_for_archived_blocks(
        self, system, test_engine
    ) -> None:
        """TC-H-009: curate() purges staging pairs where either block is archived."""
        b1, b2 = await _two_active_no_edge(system)
        await system.frame("attention", query="hebbian")  # staging_count == 1
        assert (await system.status()).co_retrieval_staging_count == 1

        # Manually archive one of the blocks (simulates decay)
        async with test_engine.begin() as conn:
            await update_block_status(conn, b1, BlockStatus.ARCHIVED)

        await system.curate()
        # Staging entry (b1, b2) should be removed — b1 is no longer active
        assert (await system.status()).co_retrieval_staging_count == 0
