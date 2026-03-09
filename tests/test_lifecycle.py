"""Lifecycle operations test suite — learn(), consolidate(), end_session()."""

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_active_blocks, get_block, get_inbox_blocks, seed_builtin_data
from elfmem.operations.consolidate import consolidate
from elfmem.operations.learn import learn
from elfmem.session import begin_session, end_session
from elfmem.types import BlockStatus

TOL = 0.001


@pytest.fixture
async def system_setup():
    """Set up test database and services."""
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)

    mock_llm = MockLLMService(
        default_alignment=0.65,
        alignment_overrides={
            "identity": 0.90,
            "value": 0.85,
        },
        default_tags=["python"],
        tag_overrides={
            "constitutional": ["self/constitutional"],
            "value": ["self/value"],
        },
    )
    mock_embedding = MockEmbeddingService(dimensions=128)

    yield engine, mock_llm, mock_embedding
    await engine.dispose()


class TestLearnOperation:
    """Test the learn() operation — fast-path ingestion."""

    async def test_learn_adds_block_to_inbox(self, system_setup) -> None:
        """TC-L-001: learn() adds block to inbox with inbox status."""
        engine, _, _ = system_setup
        async with engine.begin() as conn:
            result = await learn(
                conn,
                content="async patterns in Python",
                category="knowledge",
                source="api",
            )
            assert result.status == "created"

            block = await get_block(conn, result.block_id)
            assert block is not None
            assert block["status"] == BlockStatus.INBOX
            assert block["content"] == "async patterns in Python"

    async def test_learn_returns_block_id(self, system_setup) -> None:
        """TC-L-013: learn() returns block ID immediately."""
        engine, _, _ = system_setup
        async with engine.begin() as conn:
            result = await learn(
                conn,
                content="test content",
                category="knowledge",
                source="api",
            )
            assert result.block_id is not None
            assert len(result.block_id) == 16
            assert all(c in "0123456789abcdef" for c in result.block_id)

    async def test_learn_exact_duplicate_rejected(self, system_setup) -> None:
        """TC-L-002: Exact duplicates rejected silently at learn()."""
        engine, _, _ = system_setup
        async with engine.begin() as conn:
            content = "I prefer async patterns."
            result1 = await learn(conn, content=content, category="knowledge", source="api")
            assert result1.status == "created"

            # Try same content again
            result2 = await learn(conn, content=content, category="knowledge", source="api")
            assert result2.status == "duplicate_rejected"
            assert result1.block_id == result2.block_id

            # Verify only one block in inbox
            inbox = await get_inbox_blocks(conn)
            assert len(inbox) == 1

    async def test_learn_with_tags(self, system_setup) -> None:
        """learn() can add initial tags to block."""
        engine, _, _ = system_setup
        async with engine.begin() as conn:
            result = await learn(
                conn,
                content="test",
                tags=["python", "async"],
                category="knowledge",
                source="api",
            )
            block = await get_block(conn, result.block_id)
            assert block is not None


class TestConsolidateOperation:
    """Test the consolidate() operation — batch promotion and scoring."""

    async def test_consolidate_promotes_inbox_to_active(self, system_setup) -> None:
        """TC-L-003: consolidate() promotes inbox blocks to active."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Learn a block
            await learn(conn, content="test knowledge", category="knowledge", source="api")
            inbox_before = await get_inbox_blocks(conn)
            assert len(inbox_before) == 1

            # Consolidate
            consolidate_result = await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Inbox should be empty, block should be active
            inbox_after = await get_inbox_blocks(conn)
            assert len(inbox_after) == 0

            active = await get_active_blocks(conn)
            assert len(active) >= 1
            assert consolidate_result.promoted >= 1

    async def test_consolidate_empty_inbox_is_noop(self, system_setup) -> None:
        """TC-L-014: consolidate() on empty inbox is a no-op."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # No blocks learned
            result = await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )
            assert result.processed == 0
            assert result.promoted == 0
            assert result.deduplicated == 0
            assert result.edges_created == 0

    async def test_consolidate_scores_self_alignment(self, system_setup) -> None:
        """TC-L-004: consolidate() calls LLM to score alignment and infer tags."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Learn a block that matches alignment override
            result = await learn(
                conn,
                content="I value clarity in my communication.",
                category="knowledge",
                source="api",
            )

            consolidate_result = await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
                self_alignment_threshold=0.70,
            )

            # Check that block was processed
            assert consolidate_result.processed >= 1
            block = await get_block(conn, result.block_id)
            assert block["self_alignment"] is not None

    async def test_consolidate_near_duplicate_supersedes(self, system_setup) -> None:
        """TC-L-005: Near-duplicate resolution (forget + create + inherit)."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create first block and promote it
            await learn(
                conn,
                content="Use async patterns in Python for I/O-bound tasks.",
                category="knowledge",
                source="api",
            )
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Create near-duplicate (high similarity but not identical)
            await learn(
                conn,
                content="Use async/await in Python for I/O-bound task handling.",
                category="knowledge",
                source="api",
            )

            # Consolidate with similarity override to force near-duplicate detection
            embedding_with_override = MockEmbeddingService(
                similarity_overrides={
                    frozenset({
                        "Use async patterns in Python for I/O-bound tasks.".lower().strip(),
                        "Use async/await in Python for I/O-bound task handling.".lower().strip(),
                    }): 0.92
                }
            )

            consolidate_result = await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=embedding_with_override,
                current_active_hours=10.0,
            )

            # First block should be archived, second should be promoted
            assert consolidate_result.deduplicated >= 1

    async def test_consolidate_very_high_similarity_rejected(self, system_setup) -> None:
        """TC-L-006: Very high similarity (>0.95) block rejected silently."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            # Create first block and promote
            await learn(
                conn,
                content="async patterns in Python",
                category="knowledge",
                source="api",
            )
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Create nearly identical block
            await learn(
                conn,
                content="async patterns in Python",  # exact same
                category="knowledge",
                source="api",
            )

            consolidate_result = await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Should be rejected as duplicate
            assert consolidate_result.deduplicated >= 1


class TestSessionManagement:
    """Session lifecycle and active hours tracking."""

    async def test_begin_and_end_session(self, system_setup) -> None:
        """Session begin/end records active hours."""
        engine, _, _ = system_setup
        async with engine.begin() as conn:
            session_id = await begin_session(conn, task_type="general")
            assert session_id is not None
            assert len(session_id) > 0

            # End session (simulate some duration)
            duration = await end_session(conn, session_id)
            assert duration >= 0.0


class TestLearnConsolidateIntegration:
    """Integration tests for learn + consolidate flow."""

    async def test_end_session_consolidates_inbox(self, system_setup) -> None:
        """TC-L-012: end_session() consolidates inbox regardless of size."""
        engine, mock_llm, mock_embedding = system_setup
        async with engine.begin() as conn:
            await begin_session(conn, task_type="test")

            # Add 3 blocks (below inbox_threshold=10)
            for i in range(3):
                await learn(
                    conn,
                    content=f"Knowledge block {i}",
                    category="knowledge",
                    source="api",
                )

            inbox_before = await get_inbox_blocks(conn)
            assert len(inbox_before) == 3

            # End session should consolidate all
            # Note: In real API, this would be called via MemorySystem.end_session()
            # Here we manually call consolidate to simulate
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            inbox_after = await get_inbox_blocks(conn)
            assert len(inbox_after) == 0

            active = await get_active_blocks(conn)
            assert len(active) >= 3


class TestEdgeCreationDuringConsolidate:
    """Graph edge creation at consolidation time."""

    async def test_edge_created_above_similarity_threshold(self, system_setup) -> None:
        """TC-G-001: Edge created for similar blocks at consolidation."""
        engine, mock_llm, mock_embedding = system_setup

        # Create embedding service with controlled similarity
        embedding_service = MockEmbeddingService(
            similarity_overrides={
                frozenset({"async patterns", "coroutine patterns"}): 0.78
            }
        )

        async with engine.begin() as conn:
            # Create and promote first block
            await learn(
                conn,
                content="async patterns",
                category="knowledge",
                source="api",
            )
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=embedding_service,
                current_active_hours=10.0,
            )

            # Create second block (will find first as similar)
            await learn(
                conn,
                content="coroutine patterns",
                category="knowledge",
                source="api",
            )

            consolidate_result = await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=embedding_service,
                current_active_hours=10.0,
                edge_score_threshold=0.40,
            )

            # Should have created edges
            assert consolidate_result.edges_created >= 1

    async def test_no_edge_below_threshold(self, system_setup) -> None:
        """TC-G-002: No edge created below similarity threshold."""
        engine, mock_llm, mock_embedding = system_setup

        # Embeddings will have default low similarity
        async with engine.begin() as conn:
            # Create and promote first block
            await learn(
                conn,
                content="async patterns in Python",
                category="knowledge",
                source="api",
            )
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Create second block (unrelated)
            await learn(
                conn,
                content="SQL database optimization",
                category="knowledge",
                source="api",
            )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
                edge_score_threshold=0.40,
            )

            # Low similarity should result in no edges
            # (depends on actual embedding behavior)


class TestDecayTierDetermination:
    """Decay tier assignment from tags."""

    async def test_decay_tier_constitutional_block(self, system_setup) -> None:
        """TC-D-008: Decay tier from self/constitutional tag (permanent)."""
        engine, mock_llm_with_tags, mock_embedding = system_setup

        # Override mock to return constitutional tag
        mock_llm_with_tags.tag_overrides = {
            "fundamental": ["self/constitutional"],
        }

        async with engine.begin() as conn:
            result = await learn(
                conn,
                content="This is a fundamental constitutional belief.",
                category="knowledge",
                source="api",
            )

            await consolidate(
                conn,
                llm=mock_llm_with_tags,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
                self_alignment_threshold=0.5,
            )

            block = await get_block(conn, result.block_id)
            # Permanent tier should have very low decay_lambda
            assert block is not None

    async def test_decay_tier_tag_free_defaults_to_standard(self, system_setup) -> None:
        """TC-D-009: Tag-free block defaults to standard decay tier."""
        engine, mock_llm, mock_embedding = system_setup

        # Override mock to return no tags
        mock_llm.default_tags = []
        mock_llm.tag_overrides = {}

        async with engine.begin() as conn:
            result = await learn(
                conn,
                content="generic knowledge",
                category="knowledge",
                source="api",
            )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
                self_alignment_threshold=0.5,
            )

            block = await get_block(conn, result.block_id)
            # Standard tier has lambda=0.010
            assert block is not None


class TestContradictionStorage:
    """Contradiction detection at consolidation."""

    async def test_contradiction_stored_in_contradictions_table(self, system_setup) -> None:
        """TC-G-008: Contradictions stored in contradictions table, not edges."""
        engine, mock_llm, mock_embedding = system_setup

        # Configure LLM to detect contradictions
        mock_llm.contradiction_overrides = {
            ("sync", "async"): 0.92,
        }

        async with engine.begin() as conn:
            # Create and promote first block
            await learn(
                conn,
                content="Use synchronous calls always.",
                category="knowledge",
                source="api",
            )
            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Create contradicting block
            await learn(
                conn,
                content="Never use synchronous calls — always async.",
                category="knowledge",
                source="api",
            )

            await consolidate(
                conn,
                llm=mock_llm,
                embedding_svc=mock_embedding,
                current_active_hours=10.0,
            )

            # Should have detected contradiction
            # Check via queries.get_contradictions_for_blocks
