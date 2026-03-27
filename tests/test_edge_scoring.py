"""Tests for composite edge scoring — public functions and integration.

Pure function tests: jaccard_similarity and temporal_proximity from scoring.py.
Integration tests: composite edge behaviour observed through consolidate().
"""

import math

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_edges_for_block, seed_builtin_data
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn
from elfmem.scoring import (
    TEMPORAL_SIGMA_HOURS,
    jaccard_similarity,
    temporal_proximity,
)

TOL = 0.001


# ── jaccard_similarity ────────────────────────────────────────────────────────

class TestJaccardSimilarity:
    def test_both_empty_returns_zero(self) -> None:
        assert jaccard_similarity([], []) == 0.0

    def test_left_empty_returns_zero(self) -> None:
        assert jaccard_similarity([], ["x", "y"]) == 0.0

    def test_right_empty_returns_zero(self) -> None:
        assert jaccard_similarity(["a", "b"], []) == 0.0

    def test_identical_lists_return_one(self) -> None:
        assert abs(jaccard_similarity(["a", "b"], ["a", "b"]) - 1.0) < TOL

    def test_no_overlap_returns_zero(self) -> None:
        assert jaccard_similarity(["a"], ["b"]) == 0.0

    def test_partial_overlap(self) -> None:
        # {a,b} ∩ {b,c} / {a,b,c} = 1/3
        result = jaccard_similarity(["a", "b"], ["b", "c"])
        assert abs(result - 1 / 3) < TOL

    def test_symmetric(self) -> None:
        a = ["python", "async", "coroutine"]
        b = ["async", "database"]
        assert abs(jaccard_similarity(a, b) - jaccard_similarity(b, a)) < TOL

    def test_order_independent(self) -> None:
        assert abs(jaccard_similarity(["b", "a"], ["a", "b"]) - 1.0) < TOL


# ── temporal_proximity ────────────────────────────────────────────────────────

class TestTemporalProximity:
    def test_same_hours_returns_one(self) -> None:
        assert abs(temporal_proximity(10.0, 10.0) - 1.0) < TOL

    def test_sigma_apart_returns_exp_neg_half(self) -> None:
        result = temporal_proximity(0.0, TEMPORAL_SIGMA_HOURS)
        assert abs(result - math.exp(-0.5)) < TOL

    def test_three_sigma_near_zero(self) -> None:
        result = temporal_proximity(0.0, 3 * TEMPORAL_SIGMA_HOURS)
        assert result < 0.012

    def test_symmetric(self) -> None:
        a, b = 5.0, 20.0
        assert abs(temporal_proximity(a, b) - temporal_proximity(b, a)) < TOL

    def test_custom_sigma(self) -> None:
        # sigma=4: 4 hours apart → exp(-0.5)
        result = temporal_proximity(0.0, 4.0, sigma=4.0)
        assert abs(result - math.exp(-0.5)) < TOL


# ── Integration: composite scoring through full consolidate() pipeline ─────────

@pytest.fixture
async def db():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    return engine


class TestCompositeEdgeIntegration:
    """Composite edge scoring behaviour observed through consolidate()."""

    async def test_shared_tags_form_edge_at_moderate_cosine(self, db) -> None:
        """Blocks with shared tags form an edge at cosine=0.65.

        With shared tags the composite score comfortably exceeds 0.45.
        """
        llm = MockLLMService(default_tags=["frame-selection"])
        embedding = MockEmbeddingService(
            similarity_overrides={
                frozenset({"frame heuristic alpha", "frame heuristic beta"}): 0.65
            }
        )
        async with db.begin() as conn:
            await learn(conn, content="frame heuristic alpha", category="knowledge", source="api")
            await learn(conn, content="frame heuristic beta", category="knowledge", source="api")
            cr = await consolidate(
                conn, llm=llm, embedding_svc=embedding, current_active_hours=5.0
            )

        assert cr.edges_created >= 1

    async def test_minimum_cosine_guard_prevents_spurious_edge(self, db) -> None:
        """Cosine=0.45 < MINIMUM_COSINE_FOR_EDGE (0.50): no edge even with same category.

        Without the guard, same-session + same-category context gives a non-cosine
        floor of 0.25, allowing weakly related blocks to form spurious edges that
        corrupt graph expansion during recall.
        """
        embedding = MockEmbeddingService(
            similarity_overrides={
                frozenset({"python async patterns", "sql query optimization"}): 0.45
            }
        )
        llm = MockLLMService(default_tags=[])
        async with db.begin() as conn:
            result = await learn(
                conn, content="python async patterns", category="knowledge", source="api"
            )
            await learn(conn, content="sql query optimization", category="knowledge", source="api")
            await consolidate(
                conn, llm=llm, embedding_svc=embedding, current_active_hours=5.0
            )
            edges = await get_edges_for_block(conn, result.block_id)

        assert len(edges) == 0

    async def test_edge_weight_is_composite_not_raw_cosine(self, db) -> None:
        """Edge weight stored in DB is the composite score, not raw cosine.

        Using current_active_hours=0.0 so both block_hours and the stale
        a_block["last_reinforced_at"] are 0.0, giving temporal_proximity=1.0
        and a fully deterministic expected weight.

        Expected: cosine×0.55 + jaccard×0.20 + category×0.15 + temporal×0.10
                = 0.78×0.55 + 1.0×0.20 + 1.0×0.15 + 1.0×0.10 = 0.879
        """
        cosine = 0.78
        embedding = MockEmbeddingService(
            similarity_overrides={frozenset({"alpha block", "beta block"}): cosine}
        )
        llm = MockLLMService(default_tags=["shared-tag"])
        async with db.begin() as conn:
            result = await learn(conn, content="alpha block", category="knowledge", source="api")
            await learn(conn, content="beta block", category="knowledge", source="api")
            await consolidate(
                conn, llm=llm, embedding_svc=embedding, current_active_hours=0.0
            )
            edges = await get_edges_for_block(conn, result.block_id)

        assert len(edges) >= 1
        expected = cosine * 0.55 + 1.0 * 0.20 + 1.0 * 0.15 + 1.0 * 0.10  # = 0.879
        assert abs(float(edges[0]["weight"]) - expected) < TOL
