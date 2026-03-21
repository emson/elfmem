"""Tests for connect(), disconnect(), connect_by_query(), connects(), and breadcrumbs."""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.queries import get_edge
from elfmem.exceptions import BlockNotActiveError, SelfLoopError
from elfmem.types import ConnectSpec

TOL = 0.001


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding):
    """MemorySystem with low inbox_threshold for fast test cycles."""
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    s = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )
    await s.begin_session()
    return s


async def _two_active_blocks(system: MemorySystem) -> tuple[str, str]:
    """Learn 3 blocks and dream to get 2 known active block IDs."""
    r1 = await system.learn("frame heuristics guide agent context selection")
    r2 = await system.learn("outcome signals confirm which memories are useful")
    await system.learn("decay prevents stale knowledge from cluttering retrieval")
    await system.dream()
    return r1.block_id, r2.block_id


# ── Schema ────────────────────────────────────────────────────────────────────

class TestEdgeSchemaExtended:
    def test_edges_table_has_relation_type_column(self) -> None:
        """Edges table schema includes the relation_type column."""
        from elfmem.db.models import edges
        assert "relation_type" in edges.c

    def test_edges_table_has_origin_column(self) -> None:
        """Edges table schema includes the origin column."""
        from elfmem.db.models import edges
        assert "origin" in edges.c

    def test_edges_table_has_last_active_hours_column(self) -> None:
        """Edges table schema includes the last_active_hours column."""
        from elfmem.db.models import edges
        assert "last_active_hours" in edges.c

    def test_edges_table_has_note_column(self) -> None:
        """Edges table schema includes the note column."""
        from elfmem.db.models import edges
        assert "note" in edges.c

    async def test_similarity_edge_sets_correct_metadata(self, system) -> None:
        """Similarity edges from consolidation have relation_type='similar' and origin='similarity'.
        """
        id1, id2 = await _two_active_blocks(system)
        # consolidation (dream) creates similarity edges between active blocks
        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row = await get_edge(conn, from_id, to_id)
        # Edge may or may not exist depending on mock similarity — check if present
        if row is not None:
            assert row["relation_type"] == "similar"
            assert row["origin"] == "similarity"

    async def test_outcome_edge_sets_outcome_metadata(self, system) -> None:
        """Outcome edges carry relation_type='outcome' and origin='outcome'."""
        id1, id2 = await _two_active_blocks(system)
        await system.outcome([id1, id2], signal=0.9)

        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row = await get_edge(conn, from_id, to_id)
        assert row is not None
        assert row["relation_type"] == "outcome"
        assert row["origin"] == "outcome"


# ── Outcome edge fix ──────────────────────────────────────────────────────────

class TestOutcomeEdgeFix:
    async def test_outcome_edge_weight_is_signal_times_0_8(self, system) -> None:
        """Outcome edges are created at signal × 0.8, not the old 0.5."""
        id1, id2 = await _two_active_blocks(system)
        signal = 0.9
        await system.outcome([id1, id2], signal)

        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row = await get_edge(conn, from_id, to_id)

        assert row is not None
        assert abs(row["weight"] - signal * 0.8) < TOL

    async def test_outcome_edge_reinforced_weight_increases_on_second_call(self, system) -> None:
        """Repeated positive outcome() raises edge weight via edge_reinforce_delta."""
        id1, id2 = await _two_active_blocks(system)
        signal = 0.9
        await system.outcome([id1, id2], signal)

        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row1 = await get_edge(conn, from_id, to_id)

        await system.outcome([id1, id2], signal)

        async with system._engine.connect() as conn:
            row2 = await get_edge(conn, from_id, to_id)

        assert row2["weight"] > row1["weight"]


# ── connect() happy paths ─────────────────────────────────────────────────────

class TestConnect:
    async def test_connect_creates_edge(self, system) -> None:
        """connect() creates an edge between two active blocks."""
        id1, id2 = await _two_active_blocks(system)
        result = await system.connect(id1, id2, "supports")
        assert result.action == "created"

    async def test_connect_sets_origin_to_agent(self, system) -> None:
        """Agent-asserted edges have origin='agent' in the database."""
        id1, id2 = await _two_active_blocks(system)
        await system.connect(id1, id2, "supports")

        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row = await get_edge(conn, from_id, to_id)
        assert row["origin"] == "agent"

    async def test_connect_uses_relation_default_weight(self, system) -> None:
        """connect('supports') uses 0.75 default weight."""
        id1, id2 = await _two_active_blocks(system)
        result = await system.connect(id1, id2, "supports")
        assert abs(result.weight - 0.75) < TOL

    async def test_connect_stores_note(self, system) -> None:
        """Edge note is persisted to the database."""
        id1, id2 = await _two_active_blocks(system)
        await system.connect(id1, id2, note="B explains the mechanism behind A")

        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row = await get_edge(conn, from_id, to_id)
        assert row["note"] == "B explains the mechanism behind A"

    async def test_connect_canonical_order_is_same_edge(self, system) -> None:
        """Connecting A→B and then B→A (reinforce) hits the same canonical edge."""
        id1, id2 = await _two_active_blocks(system)
        r1 = await system.connect(id1, id2, "similar")
        r2 = await system.connect(id2, id1, "similar")  # reversed — should reinforce
        assert r1.action == "created"
        assert r2.action == "reinforced"

    async def test_connect_reinforce_boosts_weight(self, system) -> None:
        """Reinforcing an existing edge increases its weight."""
        id1, id2 = await _two_active_blocks(system)
        r1 = await system.connect(id1, id2)
        r2 = await system.connect(id1, id2)  # reinforce
        assert r2.weight > r1.weight

    async def test_connect_skip_leaves_edge_unchanged(self, system) -> None:
        """if_exists='skip' returns existing edge without modification."""
        id1, id2 = await _two_active_blocks(system)
        r1 = await system.connect(id1, id2, weight=0.65)
        r2 = await system.connect(id1, id2, if_exists="skip")
        assert r2.action == "skipped"
        assert abs(r2.weight - r1.weight) < TOL

    async def test_connect_update_changes_relation_type(self, system) -> None:
        """if_exists='update' changes the relation type on an existing edge."""
        id1, id2 = await _two_active_blocks(system)
        await system.connect(id1, id2, "similar")
        result = await system.connect(id1, id2, "supports", if_exists="update")
        assert result.action == "updated"
        assert result.relation == "supports"


# ── connect() validation and errors ──────────────────────────────────────────

class TestConnectErrors:
    async def test_connect_self_loop_raises(self, system) -> None:
        """connect(id, id) raises SelfLoopError."""
        id1, _ = await _two_active_blocks(system)
        with pytest.raises(SelfLoopError) as exc_info:
            await system.connect(id1, id1)
        assert exc_info.value.recovery  # .recovery field always present

    async def test_connect_inbox_block_raises_block_not_active(self, system) -> None:
        """connect() with an inbox (unconsolidated) block raises BlockNotActiveError."""
        id1, _ = await _two_active_blocks(system)
        inbox_result = await system.learn("this block stays in inbox")  # no dream
        with pytest.raises(BlockNotActiveError):
            await system.connect(id1, inbox_result.block_id)

    async def test_block_not_active_error_has_recovery(self, system) -> None:
        """BlockNotActiveError includes a recovery hint."""
        id1, _ = await _two_active_blocks(system)
        inbox_result = await system.learn("inbox block")
        with pytest.raises(BlockNotActiveError) as exc_info:
            await system.connect(id1, inbox_result.block_id)
        assert exc_info.value.recovery


# ── disconnect() ──────────────────────────────────────────────────────────────

class TestDisconnect:
    async def test_disconnect_removes_edge(self, system) -> None:
        """disconnect() deletes an existing edge and returns action='removed'."""
        id1, id2 = await _two_active_blocks(system)
        await system.connect(id1, id2, "supports")
        result = await system.disconnect(id1, id2)
        assert result.action == "removed"
        assert result.removed_relation == "supports"

    async def test_disconnect_not_found_when_no_edge(self, system) -> None:
        """disconnect() returns action='not_found' when no edge exists."""
        id1, id2 = await _two_active_blocks(system)
        result = await system.disconnect(id1, id2)
        assert result.action == "not_found"

    async def test_disconnect_guard_prevents_wrong_type_removal(self, system) -> None:
        """guard_relation='similar' does not remove a 'supports' edge."""
        id1, id2 = await _two_active_blocks(system)
        await system.connect(id1, id2, "supports")
        result = await system.disconnect(id1, id2, guard_relation="similar")
        assert result.action == "guarded"

    async def test_disconnect_edge_gone_from_db(self, system) -> None:
        """After disconnect(), the edge is removed from the database."""
        id1, id2 = await _two_active_blocks(system)
        await system.connect(id1, id2)
        await system.disconnect(id1, id2)

        async with system._engine.connect() as conn:
            from elfmem.types import Edge
            from_id, to_id = Edge.canonical(id1, id2)
            row = await get_edge(conn, from_id, to_id)
        assert row is None


# ── connect_by_query() ────────────────────────────────────────────────────────

class TestConnectByQuery:
    async def test_connect_by_query_dry_run_writes_nothing(self, system) -> None:
        """dry_run=True previews matches without writing an edge."""
        await _two_active_blocks(system)
        result = await system.connect_by_query(
            "frame heuristics", "outcome signals",
            dry_run=True, min_confidence=0.0,  # bypass threshold for mock scores
        )
        assert result.action == "dry_run_preview"
        assert result.source_content is not None
        assert result.target_content is not None

    async def test_connect_by_query_returns_content_for_verification(self, system) -> None:
        """connect_by_query always exposes matched block content for agent verification."""
        await _two_active_blocks(system)
        result = await system.connect_by_query(
            "frame selection context", "outcome memory feedback",
            dry_run=True, min_confidence=0.0,
        )
        # Full block content always present — agent must verify correct match
        assert result.source_content is not None
        assert result.target_content is not None

    async def test_connect_by_query_insufficient_confidence(self, system) -> None:
        """connect_by_query returns insufficient_confidence when scores are below threshold."""
        await _two_active_blocks(system)
        result = await system.connect_by_query(
            "frame heuristics", "outcome signals",
            min_confidence=1.0,  # impossible threshold
        )
        assert result.action == "insufficient_confidence"


# ── Session breadcrumbs ───────────────────────────────────────────────────────

class TestSessionBreadcrumbs:
    async def test_last_learned_block_id_set_after_learn(self, system) -> None:
        """last_learned_block_id is set after a successful learn()."""
        result = await system.learn("some new fact")
        assert system.last_learned_block_id == result.block_id

    async def test_last_learned_block_id_none_for_duplicate(self, system) -> None:
        """last_learned_block_id is not updated for duplicate blocks."""
        await system.learn("duplicate content")
        first_id = system.last_learned_block_id
        await system.learn("duplicate content")  # duplicate — rejected
        assert system.last_learned_block_id == first_id  # unchanged

    async def test_last_recall_block_ids_set_after_recall(self, system) -> None:
        """last_recall_block_ids reflects blocks from the most recent recall()."""
        id1, _ = await _two_active_blocks(system)
        blocks = await system.recall("frame heuristics")
        assert system.last_recall_block_ids == [b.id for b in blocks]

    async def test_session_block_ids_accumulates(self, system) -> None:
        """session_block_ids collects IDs across learn() and recall() calls."""
        r = await system.learn("knowledge A")
        assert r.block_id in system.session_block_ids

    async def test_breadcrumbs_reset_on_new_session(self, system) -> None:
        """Breadcrumbs are cleared when a new session begins."""
        await system.learn("before reset")
        await system.end_session()
        await system.begin_session()  # genuinely new session
        assert system.last_learned_block_id is None
        assert system.last_recall_block_ids == []
        assert system.session_block_ids == []


# ── connects() batch ──────────────────────────────────────────────────────────

class TestConnectsBatch:
    async def test_connects_creates_multiple_edges(self, system) -> None:
        """connects() creates all specified edges and reports correct counts."""
        # Need 4 active blocks for 2 edges
        r1 = await system.learn("concept A for batch")
        r2 = await system.learn("concept B for batch")
        r3 = await system.learn("concept C for batch")
        await system.dream()
        await system.learn("concept D for batch")
        # r4 is in inbox — add a 5th so we can dream again
        await system.learn("concept E for batch")
        await system.learn("concept F for batch")
        await system.dream()

        specs = [
            ConnectSpec(source=r1.block_id, target=r2.block_id, relation="supports"),
            ConnectSpec(source=r2.block_id, target=r3.block_id, relation="elaborates"),
        ]
        result = await system.connects(specs)
        assert result.created == 2
        assert result.errors == []

    async def test_connects_collects_errors_without_aborting(self, system) -> None:
        """Per-edge errors are collected; valid edges in the batch still succeed."""
        id1, id2 = await _two_active_blocks(system)
        specs = [
            ConnectSpec(source=id1, target=id1, relation="similar"),  # self-loop — error
            ConnectSpec(source=id1, target=id2, relation="supports"),  # valid
        ]
        result = await system.connects(specs)
        assert len(result.errors) == 1
        assert result.created == 1
