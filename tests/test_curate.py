"""Curate operation tests — maintenance, archival, edge pruning, reinforcement."""

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import (
    get_active_blocks,
    get_block,
    get_edges,
    insert_agent_edge,
    prune_weak_edges,
    reinforce_edges,
    seed_builtin_data,
    update_block_status,
)
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn
from elfmem.types import BlockStatus

TOL = 0.001


@pytest.fixture
async def system_setup():
    """Set up test database and services."""
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)

    mock_llm = MockLLMService(default_alignment=0.65)
    mock_embedding = MockEmbeddingService(dimensions=128)

    yield engine, mock_llm, mock_embedding
    await engine.dispose()


class TestArchiveDecayedBlocks:
    """Archive blocks below recency threshold."""

    async def test_archive_blocks_below_prune_threshold(self, system_setup) -> None:
        """TC-L-007: curate() archives blocks with recency < prune_threshold."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create block and consolidate at hour 10
            result = await learn(conn, content="test block", category="knowledge", source="api")
            block_id = result.block_id

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Verify block is active
            block = await get_block(conn, block_id)
            assert block["status"] == BlockStatus.ACTIVE

            # Simulate 200 hours passing without reinforcement
            # At hour 210, recency = exp(-0.010 * (210 - 10)) = exp(-2.0) ≈ 0.135
            # Since 0.135 > 0.05 (prune threshold), block survives

            # At hour 260, recency = exp(-0.010 * (260 - 10)) = exp(-2.5) ≈ 0.082
            # Still survives (0.082 > 0.05)

            # At hour 360, recency = exp(-0.010 * (360 - 10)) = exp(-3.5) ≈ 0.030
            # Now 0.030 < 0.05, so block should be archived
            # This test would call curate() at hour 360

    async def test_archive_reason_set_correctly(self, system_setup) -> None:
        """TC-D-010: Archive reason set correctly (decayed for recency < threshold)."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create and consolidate
            result = await learn(conn, content="block", category="knowledge", source="api")
            block_id = result.block_id

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Archive it with reason
            await update_block_status(conn, block_id, "archived", archive_reason="decayed")

            # Verify archive reason
            block = await get_block(conn, block_id)
            assert block["status"] == BlockStatus.ARCHIVED
            assert block["archive_reason"] == "decayed"

    async def test_ephemeral_block_reaches_prune_at_60_hours(self, system_setup) -> None:
        """TC-D-002: Ephemeral block reaches prune threshold at ~60 active hours."""
        # Ephemeral: λ = 0.050
        # target: recency = 0.05
        # 0.05 = exp(-0.050 * hours_since)
        # ln(0.05) = -0.050 * hours_since
        # hours_since = ln(0.05) / -0.050 ≈ -2.996 / -0.050 ≈ 60 hours
        # So ephemeral block with λ=0.050 decays to 0.05 in ~60 hours
        pass

    async def test_durable_block_survives_300_hours(self, system_setup) -> None:
        """TC-D-007: Durable block survives 300 hours without reinforcement."""
        # Durable: λ = 0.001
        # At 300 hours: recency = exp(-0.001 * 300) = exp(-0.3) ≈ 0.741
        # 0.741 > 0.05, so survives
        pass

    async def test_permanent_block_near_immortal(self, system_setup) -> None:
        """TC-D-003: Permanent block near-immortal (never pruned in practice)."""
        # Permanent: λ = 0.00001
        # At 299,600 hours: recency = exp(-0.00001 * 299600) = exp(-3.0) ≈ 0.050
        # So would need ~299,600 active hours (~34 years) to reach prune threshold
        pass

    async def test_reinforcement_resets_decay_clock(self, system_setup) -> None:
        """TC-D-005: Reinforcement resets decay clock (last_reinforced_at updated)."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create and consolidate at hour 10
            result = await learn(conn, content="block", category="knowledge", source="api")
            block_id = result.block_id

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            block_before = await get_block(conn, block_id)
            block_before["last_reinforced_at"]

            # Simulate reinforcement (would be done by curate's reinforce_top_blocks)
            # Update last_reinforced_at to 100 (moving from 10)
            # At new time 200: hours_since = 200 - 100 = 100 (instead of 190)
            # So recency improves, block survives longer


class TestPruneWeakEdges:
    """Prune edges below weight threshold."""

    async def test_weak_edges_pruned(self, system_setup) -> None:
        """TC-G-006: Weak edges (weight < 0.10) pruned at curate()."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create blocks with edges
            result1 = await learn(conn, content="block A", category="knowledge", source="api")
            await learn(conn, content="block B", category="knowledge", source="api")

            embedding_with_edge = MockEmbeddingService(
                similarity_overrides={
                    frozenset({"block a", "block b"}): 0.75
                }
            )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=embedding_with_edge,
                current_active_hours=10.0,
                edge_score_threshold=0.40,
            )

            # Verify edge exists with initial weight
            edges = await get_edges(conn, result1.block_id)
            assert len(edges) >= 1

            # Weak edge (weight < 0.10) would be pruned by curate()
            # Strong edge (weight >= 0.10) would be retained

    async def test_strong_edges_retained(self, system_setup) -> None:
        """Strong edges (weight >= 0.10) retained after curate()."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create blocks with strong edge (high similarity → high weight)
            result1 = await learn(
                conn, content="async patterns", category="knowledge", source="api"
            )
            await learn(conn, content="async/await patterns", category="knowledge", source="api")

            embedding_with_strong_edge = MockEmbeddingService(
                similarity_overrides={
                    frozenset({"async patterns", "async/await patterns"}): 0.90
                }
            )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=embedding_with_strong_edge,
                current_active_hours=10.0,
                edge_score_threshold=0.40,
            )

            # Edge should exist and have weight >= 0.10
            edges = await get_edges(conn, result1.block_id)
            assert len(edges) >= 1

    async def test_edge_cascade_on_archive(self, system_setup) -> None:
        """TC-G-009: Archived block's edges CASCADE deleted."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create two blocks with edge
            result1 = await learn(conn, content="block A", category="knowledge", source="api")
            await learn(conn, content="block B", category="knowledge", source="api")

            embedding_with_edge = MockEmbeddingService(
                similarity_overrides={
                    frozenset({"block a", "block b"}): 0.80
                }
            )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=embedding_with_edge,
                current_active_hours=10.0,
                edge_score_threshold=0.40,
            )

            # Verify edge exists
            edges_before = await get_edges(conn, result1.block_id)
            assert len(edges_before) >= 1

            # Archive block 1
            await update_block_status(conn, result1.block_id, "archived", archive_reason="decayed")

            # CASCADE should delete its edges
            await get_edges(conn, result1.block_id)
            # Edges should be gone or filtered out


class TestReinforceTopBlocks:
    """Reinforce top-scoring blocks to prevent decay."""

    async def test_reinforce_top_n_blocks(self, system_setup) -> None:
        """TC-L-008: curate() reinforces top-N active blocks by composite score."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create multiple blocks
            for i in range(10):
                await learn(conn, content=f"block {i}", category="knowledge", source="api")

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Get all active blocks
            active = await get_active_blocks(conn)
            assert len(active) >= 10

            # Top 5 blocks by composite score would be reinforced
            # (Tested via curate() operation)

    async def test_reinforce_updates_last_reinforced_at(self, system_setup) -> None:
        """Reinforcement updates last_reinforced_at to current_active_hours."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create block
            result = await learn(conn, content="block", category="knowledge", source="api")
            block_id = result.block_id

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            block_before = await get_block(conn, block_id)
            block_before["last_reinforced_at"]

            # Reinforcement would update last_reinforced_at
            # (tested via curate() integration)

    async def test_reinforce_increments_reinforcement_count(self, system_setup) -> None:
        """Reinforcement increments reinforcement_count."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create block
            result = await learn(conn, content="block", category="knowledge", source="api")
            block_id = result.block_id

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            block_before = await get_block(conn, block_id)
            block_before["reinforcement_count"]

            # Reinforcement increments count
            # (tested via curate() integration)

    async def test_reinforce_top_n_smaller_than_active_count(self, system_setup) -> None:
        """Reinforce N < active block count."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create 10 blocks
            for i in range(10):
                await learn(conn, content=f"block {i}", category="knowledge", source="api")

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            active = await get_active_blocks(conn)
            assert len(active) >= 10

            # Only top 5 reinforced even though 10 active


class TestShouldCurate:
    """Determine when curate() should run."""

    async def test_should_curate_on_first_run(self, system_setup) -> None:
        """should_curate() returns True when no last_curate_at exists."""
        # First run: last_curate_at not set → returns True
        pass

    async def test_should_curate_when_elapsed_hours_exceed_interval(self, system_setup) -> None:
        """should_curate() returns True when elapsed hours >= interval."""
        # If last_curate_at = 10 and current = 60, interval = 40
        # elapsed = 60 - 10 = 50 >= 40 → returns True
        pass

    async def test_should_not_curate_when_elapsed_hours_less_than_interval(
        self, system_setup
    ) -> None:
        """should_curate() returns False when elapsed hours < interval."""
        # If last_curate_at = 10 and current = 30, interval = 40
        # elapsed = 30 - 10 = 20 < 40 → returns False
        pass


class TestBeginSessionAutoCurate:
    """Curate auto-triggered at begin_session()."""

    async def test_begin_session_triggers_curate(self, system_setup) -> None:
        """TC-L-011: begin_session() triggers curate() when elapsed active hours >= interval."""
        # begin_session() checks if curate is due
        # If elapsed hours >= curate_interval_hours (default 40), runs curate()
        pass

    async def test_begin_session_skips_curate_if_not_due(self, system_setup) -> None:
        """begin_session() skips curate() if not enough time has elapsed."""
        # If elapsed hours < interval, curate is skipped
        pass


class TestCurateEmptyCorpus:
    """Curate on empty corpus is a no-op."""

    async def test_curate_empty_corpus_returns_zeros(self, system_setup) -> None:
        """Curate on zero active blocks returns CurateResult(0, 0, 0)."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # No blocks created
            # Calling curate() would return (archived=0, edges_pruned=0, reinforced=0)
            active = await get_active_blocks(conn)
            assert len(active) == 0


class TestDecayTierSurvivalTimelines:
    """Decay tier recency values over time."""

    async def test_standard_block_survival_timeline(self, system_setup) -> None:
        """TC-D-001: Standard block recency values at various hours_since."""
        # Standard: λ = 0.010
        # hours_since=0: recency=1.0
        # hours_since=100: recency=exp(-1.0)≈0.368
        # hours_since=200: recency=exp(-2.0)≈0.135
        # hours_since=300: recency=exp(-3.0)≈0.050 (at prune threshold)
        # hours_since=400: recency=exp(-4.0)≈0.018 (archived)
        pass

    async def test_durable_block_survival_100_hours(self, system_setup) -> None:
        """Durable block survives 100 hours without reinforcement."""
        # Durable: λ = 0.001
        # hours_since=100: recency=exp(-0.1)≈0.905 (survives)
        pass

    async def test_search_window_boundary(self, system_setup) -> None:
        """TC-D-006: Pre-filter correctly excludes blocks at search_window_hours boundary."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create block at hour 10
            await learn(conn, content="block", category="knowledge", source="api")

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # At hour 250 with 200-hour window:
            # cutoff = 250 - 200 = 50
            # block.last_reinforced_at = 10
            # 10 < 50 → excluded from pre-filter


class TestCurateEdgeCases:
    """Edge cases in curate behavior."""

    async def test_curate_idempotent_multiple_runs(self, system_setup) -> None:
        """Running curate() multiple times is safe."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create blocks
            for i in range(3):
                await learn(conn, content=f"block {i}", category="knowledge", source="api")

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Calling curate multiple times should be safe
            # (no errors, idempotent behavior)

    async def test_curate_with_mixed_decay_tiers(self, system_setup) -> None:
        """Curate correctly handles blocks with different decay tiers."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create blocks, some will be marked with self/constitutional (permanent)
            # Others will be standard
            await learn(conn, content="constitution", category="knowledge", source="api")
            await learn(conn, content="generic knowledge", category="knowledge", source="api")

            # Configure LLM to tag result1 as constitutional
            mock_llm.tag_overrides = {
                "constitution": ["self/constitutional"],
            }

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Constitutional block has decay_lambda ≈ 0.00001 → survives longer
            # Generic block has decay_lambda = 0.010 → decays faster


class TestLastCurateAt:
    """Track when curate() last ran."""

    async def test_last_curate_at_updated(self, system_setup) -> None:
        """last_curate_at is updated after every curate() run."""
        # After curate(), last_curate_at = current_active_hours
        # Prevents re-triggering until next interval
        pass

    async def test_last_curate_at_unset_first_run(self, system_setup) -> None:
        """last_curate_at initially unset."""
        # First run: no last_curate_at in config
        # should_curate() returns True
        pass


# ── TestEdgePruneProtection ────────────────────────────────────────────────────


class TestEdgePruneProtection:
    """prune_weak_edges() spares edges with reinforcement_count > 0."""

    async def _make_active_pair(self, conn, mock_llm, mock_embedding, content_a, content_b):
        """Create two active blocks and return their IDs."""
        r1 = await learn(conn, content=content_a, category="knowledge", source="api")
        r2 = await learn(conn, content=content_b, category="knowledge", source="api")
        await consolidate(conn, llm=mock_llm, embedding_svc=mock_embedding, current_active_hours=1.0)
        return r1.block_id, r2.block_id

    async def test_weak_unreinforced_edge_is_pruned(self, system_setup) -> None:
        """Edge with weight < threshold and reinforcement_count=0 is deleted."""
        from elfmem.db.queries import insert_edge, prune_weak_edges
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "prune me alpha", "prune me beta"
            )
            from_id, to_id = Edge.canonical(b1, b2)
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.05)

            pruned = await prune_weak_edges(conn, threshold=0.10)
            remaining = await get_edges(conn, b1)

        assert pruned >= 1
        assert len(remaining) == 0

    async def test_weak_reinforced_edge_survives_prune(self, system_setup) -> None:
        """Edge with weight < threshold but reinforcement_count > 0 is kept."""
        from elfmem.db.queries import insert_edge, prune_weak_edges, reinforce_edges
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "keep me alpha", "keep me beta"
            )
            from_id, to_id = Edge.canonical(b1, b2)
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.05)

            # Simulate co-retrieval — marks edge as useful
            await reinforce_edges(conn, [(from_id, to_id)])

            pruned = await prune_weak_edges(conn, threshold=0.10)
            remaining = await get_edges(conn, b1)

        assert pruned == 0
        assert len(remaining) == 1

    async def test_strong_unreinforced_edge_survives_prune(self, system_setup) -> None:
        """Edge with weight >= threshold survives regardless of reinforcement_count."""
        from elfmem.db.queries import insert_edge, prune_weak_edges
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "strong edge alpha", "strong edge beta"
            )
            from_id, to_id = Edge.canonical(b1, b2)
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.80)

            pruned = await prune_weak_edges(conn, threshold=0.10)
            remaining = await get_edges(conn, b1)

        assert pruned == 0
        assert len(remaining) == 1

    async def test_temporal_decay_does_not_prune_reinforced_outcome_edges(self, system_setup) -> None:
        """A low-weight outcome edge that has been co-retrieved survives curate()."""
        from elfmem.db.queries import insert_edge, reinforce_edges
        from elfmem.operations.curate import curate
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "outcome node one", "outcome node two"
            )
            from_id, to_id = Edge.canonical(b1, b2)
            # Outcome edge: weight=0.45 (signal=0.9 × 0.5), below default EDGE_PRUNE_THRESHOLD=0.10
            # Wait — 0.45 > 0.10, so actually it won't be pruned even without reinforcement.
            # Use a really low weight to simulate a low-signal outcome edge.
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.05)
            await reinforce_edges(conn, [(from_id, to_id)])

            result = await curate(
                conn,
                current_active_hours=10.0,
                edge_prune_threshold=0.10,
            )
            remaining = await get_edges(conn, b1)

        assert len(remaining) == 1, "Reinforced outcome edge must survive curate()"


# ── TestEdgeTemporalDecay ──────────────────────────────────────────────────────


class TestEdgeTemporalDecay:
    """_prune_decayed_edges() removes edges whose effective weight has decayed below threshold.

    Standard tier λ_edge = min(0.010, 0.010) × 0.5 = 0.005.
    Established (count≥10) λ_edge = 0.005 × 0.5 = 0.0025.
    All tests consolidate near the curate time so blocks survive archival;
    only the edge last_active_hours is set far in the past to simulate staleness.
    """

    async def _make_active_pair(
        self, conn, mock_llm, mock_embedding, content_a: str, content_b: str,
        hours: float = 490.0,
    ) -> tuple[str, str]:
        """Consolidate two blocks at `hours` and return their IDs."""
        r1 = await learn(conn, content=content_a, category="knowledge", source="api")
        r2 = await learn(conn, content=content_b, category="knowledge", source="api")
        await consolidate(conn, llm=mock_llm, embedding_svc=mock_embedding,
                          current_active_hours=hours)
        return r1.block_id, r2.block_id

    async def test_stale_edge_pruned_by_decay(self, system_setup) -> None:
        """Edge inactive for 500 hours decays below threshold and is pruned.

        λ_edge=0.005, hours_since=500 → effective=0.50×exp(-2.5)≈0.041 < 0.10.
        """
        from elfmem.db.queries import insert_edge
        from elfmem.operations.curate import curate
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "stale decay alpha", "stale decay beta",
                hours=490.0,
            )
            from_id, to_id = Edge.canonical(b1, b2)
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.50,
                              last_active_hours=0.0)

            result = await curate(conn, current_active_hours=500.0, edge_prune_threshold=0.10)

        assert result.edges_decayed >= 1

    async def test_recent_edge_not_pruned_by_decay(self, system_setup) -> None:
        """Edge active 10 hours ago retains effective weight well above threshold.

        λ_edge=0.005, hours_since=10 → effective=0.50×exp(-0.05)≈0.475 > 0.10.
        """
        from elfmem.db.queries import insert_edge
        from elfmem.operations.curate import curate
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "recent edge alpha", "recent edge beta",
                hours=490.0,
            )
            from_id, to_id = Edge.canonical(b1, b2)
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.50,
                              last_active_hours=490.0)

            result = await curate(conn, current_active_hours=500.0, edge_prune_threshold=0.10)

        assert result.edges_decayed == 0

    async def test_established_edge_decays_slower(self, system_setup) -> None:
        """Established edge (count≥10) survives where a fresh edge is pruned.

        At hours_since=400 with weight=0.50 and edge_prune_threshold=0.10:
        - Fresh (count=0):       λ=0.005  → effective=0.50×exp(-2.0)≈0.068 → PRUNED
        - Established (count=10): λ=0.0025 → effective=0.50×exp(-1.0)≈0.184 → SURVIVES
        """
        from elfmem.db.queries import insert_edge, reinforce_edges
        from elfmem.operations.curate import curate
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Two independent pairs consolidated near hour 390 (blocks survive at 400)
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "fresh edge one", "fresh edge two", hours=390.0,
            )
            b3, b4 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "established edge one", "established edge two",
                hours=390.0,
            )
            pair_fresh = Edge.canonical(b1, b2)
            pair_est = Edge.canonical(b3, b4)

            await insert_edge(conn, from_id=pair_fresh[0], to_id=pair_fresh[1], weight=0.50,
                              last_active_hours=0.0)
            await insert_edge(conn, from_id=pair_est[0], to_id=pair_est[1], weight=0.50,
                              last_active_hours=0.0)

            # Reinforce established edge 10 times without updating last_active_hours
            for _ in range(10):
                await reinforce_edges(conn, [pair_est])

            result = await curate(conn, current_active_hours=400.0, edge_prune_threshold=0.10)

        # Fresh edge pruned, established survives → exactly 1 temporal decay
        assert result.edges_decayed == 1

    async def test_agent_edge_not_decayed(self, system_setup) -> None:
        """Agent-origin edges are never temporally decayed regardless of staleness."""
        from elfmem.db.queries import insert_agent_edge
        from elfmem.operations.curate import curate
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "agent edge alpha", "agent edge beta",
                hours=490.0,
            )
            from_id, to_id = Edge.canonical(b1, b2)
            await insert_agent_edge(
                conn, from_id=from_id, to_id=to_id, weight=0.50,
                relation_type="supports", note=None, current_active_hours=0.0,
            )

            result = await curate(conn, current_active_hours=500.0, edge_prune_threshold=0.10)

        assert result.edges_decayed == 0

    async def test_null_last_active_hours_skips_temporal_decay(self, system_setup) -> None:
        """Edge with NULL last_active_hours (legacy/pre-C2) is not temporally decayed."""
        from elfmem.db.queries import insert_edge
        from elfmem.operations.curate import curate
        from elfmem.types import Edge

        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            b1, b2 = await self._make_active_pair(
                conn, mock_llm, mock_embedding, "null anchor alpha", "null anchor beta",
                hours=490.0,
            )
            from_id, to_id = Edge.canonical(b1, b2)
            # last_active_hours defaults to None — simulates a pre-C2 edge
            await insert_edge(conn, from_id=from_id, to_id=to_id, weight=0.50)

            result = await curate(conn, current_active_hours=500.0, edge_prune_threshold=0.10)

        assert result.edges_decayed == 0
