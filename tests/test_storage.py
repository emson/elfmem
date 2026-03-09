"""Storage layer test suite — database schema, engine, and CRUD queries."""

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.engine import create_test_engine
from elfmem.db.models import (
    contradictions,
    edges,
    metadata,
)
from elfmem.db.queries import (
    add_tags,
    block_exists,
    bytes_to_embedding,
    content_hash,
    embedding_to_bytes,
    get_active_blocks,
    get_block,
    get_blocks_by_tag_pattern,
    get_config,
    get_contradictions_for_blocks,
    get_edge,
    get_edges_for_block,
    get_frame,
    get_inbox_blocks,
    get_neighbours,
    get_tags,
    get_total_active_hours,
    get_weighted_degree,
    insert_block,
    insert_contradiction,
    insert_edge,
    list_frames,
    reinforce_edges,
    seed_builtin_data,
    set_config,
    set_total_active_hours,
    update_block_scoring,
    update_block_status,
    upsert_outcome_edge,
)


class TestEngine:
    """Engine creation and table setup."""

    async def test_create_test_engine_creates_tables(self) -> None:
        """Test engine creates all required tables."""
        engine = await create_test_engine()
        try:
            # If engine was created successfully, tables exist
            async with engine.begin() as conn:
                # Query a table to verify it exists
                result = await conn.execute(metadata.tables["blocks"].select().limit(1))
                assert result is not None
        finally:
            await engine.dispose()

    async def test_create_test_engine_with_foreign_keys_enabled(self) -> None:
        """Test that foreign key constraints are enforced."""
        engine = await create_test_engine()
        try:
            async with engine.begin() as conn:
                # Try to insert an edge referencing a non-existent block
                # This should fail with a foreign key constraint error
                from sqlalchemy import insert
                stmt = insert(edges).values(
                    from_id="nonexistent_a",
                    to_id="nonexistent_b",
                    weight=0.5,
                )
                with pytest.raises(Exception):  # noqa: B017  # FK error varies by backend
                    await conn.execute(stmt)
        finally:
            await engine.dispose()


class TestContentHash:
    """Content hash function for block IDs."""

    def test_content_hash_consistent(self) -> None:
        """Same content produces same hash."""
        content = "I prefer async patterns in Python."
        hash1 = content_hash(content)
        hash2 = content_hash(content)
        assert hash1 == hash2

    def test_content_hash_whitespace_normalized(self) -> None:
        """Whitespace is normalized: leading/trailing stripped, lowercase."""
        content_a = "  I prefer async patterns.  "
        content_b = "i prefer async patterns."
        assert content_hash(content_a) == content_hash(content_b)

    def test_content_hash_length(self) -> None:
        """Content hash is 16 hex characters (64 bits)."""
        h = content_hash("test")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestEmbeddingConversion:
    """Embedding to/from bytes conversion."""

    def test_embedding_to_bytes_converts_float32_vector(self) -> None:
        """Convert numpy float32 array to bytes."""
        vec = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        data = embedding_to_bytes(vec)
        assert isinstance(data, bytes)
        assert len(data) == 16  # 4 floats × 4 bytes each

    def test_bytes_to_embedding_recovers_vector(self) -> None:
        """Convert bytes back to numpy float32 array."""
        original = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        data = embedding_to_bytes(original)
        recovered = bytes_to_embedding(data)
        assert isinstance(recovered, np.ndarray)
        assert recovered.dtype == np.float32
        np.testing.assert_array_almost_equal(recovered, original)

    def test_embedding_roundtrip(self) -> None:
        """Roundtrip: ndarray → bytes → ndarray preserves values."""
        original = np.random.randn(10).astype(np.float32)
        data = embedding_to_bytes(original)
        recovered = bytes_to_embedding(data)
        np.testing.assert_array_almost_equal(recovered, original)


class TestBlockQueries:
    """CRUD operations for blocks."""

    async def test_insert_block_creates_inbox_block(self, db_conn: AsyncConnection) -> None:
        """Insert block lands in inbox with default confidence."""
        block_id = "test123"
        content = "async patterns in Python"
        await insert_block(
            db_conn,
            block_id=block_id,
            content=content,
            category="knowledge",
            source="test",
        )
        block = await get_block(db_conn, block_id)
        assert block is not None
        assert block["status"] == "inbox"
        assert block["confidence"] == 0.50
        assert block["reinforcement_count"] == 0

    async def test_get_block_returns_none_for_missing(self, db_conn: AsyncConnection) -> None:
        """Get missing block returns None."""
        block = await get_block(db_conn, "nonexistent")
        assert block is None

    async def test_block_exists_check(self, db_conn: AsyncConnection) -> None:
        """Check if block exists."""
        block_id = "test123"
        assert not await block_exists(db_conn, block_id)
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        assert await block_exists(db_conn, block_id)

    async def test_get_active_blocks_filters_by_status(self, db_conn: AsyncConnection) -> None:
        """get_active_blocks returns only active blocks."""
        # Insert inbox block
        await insert_block(
            db_conn,
            block_id="inbox1",
            content="in inbox",
            category="knowledge",
            source="test",
        )
        # Insert and promote active block
        await insert_block(
            db_conn,
            block_id="active1",
            content="already active",
            category="knowledge",
            source="test",
            status="active",
        )
        # Insert archived block
        await insert_block(
            db_conn,
            block_id="archived1",
            content="archived",
            category="knowledge",
            source="test",
            status="archived",
        )
        blocks_list = await get_active_blocks(db_conn)
        assert len(blocks_list) == 1
        assert blocks_list[0]["id"] == "active1"

    async def test_get_inbox_blocks(self, db_conn: AsyncConnection) -> None:
        """get_inbox_blocks returns only inbox blocks."""
        await insert_block(
            db_conn,
            block_id="inbox1",
            content="inbox content",
            category="knowledge",
            source="test",
        )
        await insert_block(
            db_conn,
            block_id="active1",
            content="active content",
            category="knowledge",
            source="test",
            status="active",
        )
        inbox = await get_inbox_blocks(db_conn)
        assert len(inbox) == 1
        assert inbox[0]["id"] == "inbox1"

    async def test_update_block_status_transitions(self, db_conn: AsyncConnection) -> None:
        """Block status transitions correctly."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        assert (await get_block(db_conn, block_id))["status"] == "inbox"

        await update_block_status(db_conn, block_id, status="active")
        assert (await get_block(db_conn, block_id))["status"] == "active"

        await update_block_status(
            db_conn,
            block_id,
            status="archived",
            archive_reason="decayed",
        )
        block = await get_block(db_conn, block_id)
        assert block["status"] == "archived"
        assert block["archive_reason"] == "decayed"

    async def test_update_block_scoring_partial_update(self, db_conn: AsyncConnection) -> None:
        """update_block_scoring updates only specified fields."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        original = await get_block(db_conn, block_id)
        original_confidence = original["confidence"]

        # Update only self_alignment
        await update_block_scoring(
            db_conn,
            block_id,
            self_alignment=0.85,
        )
        updated = await get_block(db_conn, block_id)
        assert updated["self_alignment"] == 0.85
        assert updated["confidence"] == original_confidence  # unchanged

    async def test_update_block_scoring_with_embedding(self, db_conn: AsyncConnection) -> None:
        """update_block_scoring stores embedding as BLOB."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        await update_block_scoring(
            db_conn,
            block_id,
            embedding=vec,
            embedding_model="test-model",
        )
        block = await get_block(db_conn, block_id)
        assert block["embedding_model"] == "test-model"
        assert block["embedding"] is not None
        recovered = bytes_to_embedding(block["embedding"])
        np.testing.assert_array_almost_equal(recovered, vec)


class TestTagQueries:
    """Tag management."""

    async def test_add_tags_to_block(self, db_conn: AsyncConnection) -> None:
        """Add tags to a block."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        await add_tags(db_conn, block_id, ["self/value", "python"])
        tags = await get_tags(db_conn, block_id)
        assert set(tags) == {"self/value", "python"}

    async def test_add_tags_silently_ignores_duplicates(self, db_conn: AsyncConnection) -> None:
        """Adding duplicate tags is a no-op."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        await add_tags(db_conn, block_id, ["python"])
        await add_tags(db_conn, block_id, ["python"])  # add again
        tags = await get_tags(db_conn, block_id)
        assert tags == ["python"]

    async def test_get_tags_empty_for_untagged_block(self, db_conn: AsyncConnection) -> None:
        """Untagged block returns empty list."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        tags = await get_tags(db_conn, block_id)
        assert tags == []

    async def test_get_blocks_by_tag_pattern(self, db_conn: AsyncConnection) -> None:
        """Tag pattern matching with SQL LIKE."""
        # Create two blocks with self/* tags
        await insert_block(
            db_conn,
            block_id="b1",
            content="identity block",
            category="knowledge",
            source="test",
        )
        await insert_block(
            db_conn,
            block_id="b2",
            content="knowledge block",
            category="knowledge",
            source="test",
        )
        await add_tags(db_conn, "b1", ["self/value"])
        await add_tags(db_conn, "b2", ["python"])

        # Query for self/* blocks
        self_blocks = await get_blocks_by_tag_pattern(db_conn, "self/%")
        assert self_blocks == ["b1"]


class TestEdgeQueries:
    """Graph edge management."""

    async def test_insert_edge_canonical_order(self, db_conn: AsyncConnection) -> None:
        """Insert edge enforces canonical ordering at application level."""
        # Create two blocks
        await insert_block(
            db_conn,
            block_id="b1",
            content="first",
            category="knowledge",
            source="test",
        )
        await insert_block(
            db_conn,
            block_id="b2",
            content="second",
            category="knowledge",
            source="test",
        )
        # Insert edge (caller responsible for canonical order)
        await insert_edge(db_conn, from_id="b1", to_id="b2", weight=0.75)
        edges_b1 = await get_edges_for_block(db_conn, "b1")
        assert len(edges_b1) == 1
        assert edges_b1[0]["weight"] == 0.75

    async def test_get_edges_for_block_returns_both_directions(
        self, db_conn: AsyncConnection
    ) -> None:
        """get_edges_for_block returns edges where block is either endpoint."""
        await insert_block(
            db_conn,
            block_id="b1",
            content="one",
            category="knowledge",
            source="test",
        )
        await insert_block(
            db_conn,
            block_id="b2",
            content="two",
            category="knowledge",
            source="test",
        )
        await insert_edge(db_conn, from_id="b1", to_id="b2", weight=0.5)

        # Both blocks see the same edge
        edges_b1 = await get_edges_for_block(db_conn, "b1")
        edges_b2 = await get_edges_for_block(db_conn, "b2")
        assert len(edges_b1) == 1
        assert len(edges_b2) == 1

    async def test_get_neighbours_returns_1hop(self, db_conn: AsyncConnection) -> None:
        """get_neighbours returns 1-hop neighbours only."""
        # Create a chain: b1 — b2 — b3
        for bid in ["b1", "b2", "b3"]:
            await insert_block(
                db_conn,
                block_id=bid,
                content=f"block {bid}",
                category="knowledge",
                source="test",
            )
        await insert_edge(db_conn, from_id="b1", to_id="b2", weight=0.5)
        await insert_edge(db_conn, from_id="b2", to_id="b3", weight=0.5)

        # b1 neighbours: [b2]
        neighbours_b1 = await get_neighbours(db_conn, ["b1"])
        assert set(neighbours_b1) == {"b2"}

        # b2 neighbours: [b1, b3]
        neighbours_b2 = await get_neighbours(db_conn, ["b2"])
        assert set(neighbours_b2) == {"b1", "b3"}

    async def test_get_weighted_degree(self, db_conn: AsyncConnection) -> None:
        """Weighted degree is sum of connected edge weights."""
        await insert_block(
            db_conn,
            block_id="hub",
            content="hub block",
            category="knowledge",
            source="test",
        )
        for i in range(3):
            await insert_block(
                db_conn,
                block_id=f"b{i}",
                content=f"block {i}",
                category="knowledge",
                source="test",
            )
            await insert_edge(db_conn, from_id="b" + str(i), to_id="hub", weight=0.1 * (i + 1))

        degrees = await get_weighted_degree(db_conn, ["hub"])
        assert "hub" in degrees
        # 0.1 + 0.2 + 0.3 = 0.6
        assert abs(degrees["hub"] - 0.6) < 0.001

    async def test_cascade_delete_removes_edges(self, db_conn: AsyncConnection) -> None:
        """Deleting a block cascades to its edges."""
        await insert_block(
            db_conn,
            block_id="b1",
            content="one",
            category="knowledge",
            source="test",
        )
        await insert_block(
            db_conn,
            block_id="b2",
            content="two",
            category="knowledge",
            source="test",
        )
        await insert_edge(db_conn, from_id="b1", to_id="b2", weight=0.5)

        # Delete b1 (via archive)
        await update_block_status(db_conn, "b1", status="archived")

        # Edge should be gone
        edges = await get_edges_for_block(db_conn, "b1")
        assert len(edges) == 0

    async def test_insert_edge_stores_default_metadata(self, db_conn: AsyncConnection) -> None:
        """insert_edge() defaults: relation_type='similar', origin='similarity', last_active_hours=None."""
        await insert_block(db_conn, block_id="b1", content="one", category="knowledge", source="test")
        await insert_block(db_conn, block_id="b2", content="two", category="knowledge", source="test")
        await insert_edge(db_conn, from_id="b1", to_id="b2", weight=0.5)

        edge = await get_edge(db_conn, "b1", "b2")
        assert edge is not None
        assert edge["relation_type"] == "similar"
        assert edge["origin"] == "similarity"
        assert edge["last_active_hours"] is None

    async def test_upsert_outcome_edge_tags_relation_and_origin(self, db_conn: AsyncConnection) -> None:
        """upsert_outcome_edge() records relation_type='outcome' and origin='outcome'."""
        await insert_block(db_conn, block_id="b1", content="one", category="knowledge", source="test")
        await insert_block(db_conn, block_id="b2", content="two", category="knowledge", source="test")
        await upsert_outcome_edge(db_conn, from_id="b1", to_id="b2", weight=0.72)

        edge = await get_edge(db_conn, "b1", "b2")
        assert edge is not None
        assert edge["relation_type"] == "outcome"
        assert edge["origin"] == "outcome"

    async def test_reinforce_edges_sets_last_active_hours(self, db_conn: AsyncConnection) -> None:
        """reinforce_edges() writes last_active_hours from the session clock."""
        await insert_block(db_conn, block_id="b1", content="one", category="knowledge", source="test")
        await insert_block(db_conn, block_id="b2", content="two", category="knowledge", source="test")
        await insert_edge(db_conn, from_id="b1", to_id="b2", weight=0.5)

        await reinforce_edges(db_conn, [("b1", "b2")], current_active_hours=42.0)

        edge = await get_edge(db_conn, "b1", "b2")
        assert edge is not None
        assert abs(edge["last_active_hours"] - 42.0) < 0.001


class TestContradictionQueries:
    """Contradiction detection storage."""

    async def test_insert_contradiction(self, db_conn: AsyncConnection) -> None:
        """Insert contradiction record."""
        await insert_block(
            db_conn,
            block_id="b1",
            content="sync is better",
            category="knowledge",
            source="test",
        )
        await insert_block(
            db_conn,
            block_id="b2",
            content="async is better",
            category="knowledge",
            source="test",
        )
        await insert_contradiction(db_conn, block_a_id="b1", block_b_id="b2", score=0.92)

        contradictions = await get_contradictions_for_blocks(db_conn, ["b1", "b2"])
        assert len(contradictions) == 1
        assert contradictions[0]["score"] == 0.92

    async def test_get_contradictions_filters_resolved(self, db_conn: AsyncConnection) -> None:
        """Only unresolved contradictions returned."""
        await insert_block(db_conn, block_id="b1", content="a", category="knowledge", source="test")
        await insert_block(db_conn, block_id="b2", content="b", category="knowledge", source="test")
        await insert_contradiction(db_conn, block_a_id="b1", block_b_id="b2", score=0.9)

        # Mark as resolved
        from sqlalchemy import update as sql_update
        await db_conn.execute(
            sql_update(contradictions)
            .where(contradictions.c.block_a_id == "b1")
            .values(resolved=1)
        )

        # Should not be returned
        found = await get_contradictions_for_blocks(db_conn, ["b1"])
        assert len(found) == 0


class TestFrameQueries:
    """Frame configuration storage."""

    async def test_seed_builtin_data_creates_three_frames(self, db_conn: AsyncConnection) -> None:
        """seed_builtin_data populates SELF, ATTENTION, TASK frames."""
        await seed_builtin_data(db_conn)

        self_frame = await get_frame(db_conn, "self")
        attention_frame = await get_frame(db_conn, "attention")
        task_frame = await get_frame(db_conn, "task")

        assert self_frame is not None
        assert attention_frame is not None
        assert task_frame is not None

    async def test_get_frame_returns_none_for_missing(self, db_conn: AsyncConnection) -> None:
        """get_frame returns None for non-existent frame."""
        frame = await get_frame(db_conn, "nonexistent")
        assert frame is None

    async def test_list_frames(self, db_conn: AsyncConnection) -> None:
        """list_frames returns all frames."""
        await seed_builtin_data(db_conn)
        frames_list = await list_frames(db_conn)
        assert len(frames_list) >= 3
        names = {f["name"] for f in frames_list}
        assert "self" in names
        assert "attention" in names
        assert "task" in names


class TestSystemConfig:
    """Global system configuration."""

    async def test_set_and_get_config(self, db_conn: AsyncConnection) -> None:
        """Get/set system config values."""
        await set_config(db_conn, "test_key", "test_value")
        value = await get_config(db_conn, "test_key")
        assert value == "test_value"

    async def test_get_config_returns_none_for_missing(self, db_conn: AsyncConnection) -> None:
        """get_config returns None for missing key."""
        value = await get_config(db_conn, "nonexistent_key")
        assert value is None

    async def test_total_active_hours_roundtrip(self, db_conn: AsyncConnection) -> None:
        """Set and retrieve total_active_hours counter."""
        await set_total_active_hours(db_conn, 123.45)
        hours = await get_total_active_hours(db_conn)
        assert abs(hours - 123.45) < 0.001

    async def test_total_active_hours_default_zero(self, db_conn: AsyncConnection) -> None:
        """Uninitialized total_active_hours defaults to 0.0."""
        # Fresh DB has no config
        hours = await get_total_active_hours(db_conn)
        assert hours == 0.0


class TestForeignKeyConstraints:
    """Foreign key CASCADE behavior."""

    async def test_cascade_deletes_tags_when_block_archived(self, db_conn: AsyncConnection) -> None:
        """Tags are deleted when block is archived via foreign key CASCADE."""
        block_id = "test123"
        await insert_block(
            db_conn,
            block_id=block_id,
            content="test",
            category="knowledge",
            source="test",
        )
        await add_tags(db_conn, block_id, ["self/value", "python"])

        # Archive the block
        await update_block_status(db_conn, block_id, status="archived")

        # Tags should be gone
        tags = await get_tags(db_conn, block_id)
        assert len(tags) == 0

    async def test_cascade_deletes_contradictions_when_block_archived(
        self, db_conn: AsyncConnection
    ) -> None:
        """Contradiction records are deleted when either block is archived."""
        await insert_block(db_conn, block_id="b1", content="a", category="knowledge", source="test")
        await insert_block(db_conn, block_id="b2", content="b", category="knowledge", source="test")
        await insert_contradiction(db_conn, block_a_id="b1", block_b_id="b2", score=0.9)

        # Archive b1
        await update_block_status(db_conn, "b1", status="archived")

        # Contradiction should be gone
        contradictions = await get_contradictions_for_blocks(db_conn, ["b1", "b2"])
        assert len(contradictions) == 0
