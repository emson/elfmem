"""Tests for the direct block mutation API — edit(), forget(), ls().

v2 step 2 (docs/plans/plan_v2_substrate_reevaluation.md §5.3/§9) — the
direct write/delete/list path RC1 identified as missing: previously the
only way to change a block was an indirect side effect of near-duplicate
supersession, and there was no delete or list API at all.
"""

from __future__ import annotations

import pytest

from elfmem import MemorySystem
from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import get_block, seed_builtin_data
from elfmem.exceptions import BlockNotFound
from elfmem.types import BlockSummary, EditResult, ForgetResult

TOL = 0.001


@pytest.fixture
async def system():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    mem = MemorySystem(
        engine=engine,
        llm_service=MockLLMService(default_alignment=0.65, default_tags=["python"]),
        embedding_service=MockEmbeddingService(dimensions=64),
    )
    yield mem
    await mem.close()


class TestEdit:
    async def test_edit_returns_result(self, system) -> None:
        async with system.session():
            learned = await system.learn("Use tabs for indentation.")
            await system.consolidate()

        result = await system.edit(learned.block_id, "Use spaces for indentation.")
        assert isinstance(result, EditResult)
        assert result.block_id == learned.block_id

    async def test_edit_content_persisted(self, system) -> None:
        async with system.session():
            learned = await system.learn("Use tabs for indentation.")
            await system.consolidate()

        before = await system.ls()
        assert before[0].content == "Use tabs for indentation."

        await system.edit(learned.block_id, "Use spaces for indentation.")

        after = await system.ls()
        assert after[0].content == "Use spaces for indentation."
        assert after[0].id == learned.block_id

    async def test_edit_clears_summary_for_rescore_but_preserves_scoring_state(
        self, system
    ) -> None:
        """Content edits are not knowledge-confirmation events: confidence,
        reinforcement_count and decay_lambda must survive an edit unchanged."""
        async with system.session():
            learned = await system.learn("Original content about async patterns.")
            await system.consolidate()

        async with system._engine.begin() as conn:
            before = await get_block(conn, learned.block_id)
        assert before is not None
        confidence_before = before["confidence"]
        reinforcement_before = before["reinforcement_count"]
        decay_before = before["decay_lambda"]

        await system.edit(learned.block_id, "Reworded content about async patterns.")

        async with system._engine.begin() as conn:
            after = await get_block(conn, learned.block_id)
        assert after is not None
        assert after["summary"] is None
        assert after["last_scored_at"] is None
        assert abs(after["confidence"] - confidence_before) < TOL
        assert after["reinforcement_count"] == reinforcement_before
        assert abs(after["decay_lambda"] - decay_before) < TOL

    async def test_edit_raises_on_missing_block(self, system) -> None:
        with pytest.raises(BlockNotFound):
            await system.edit("nonexistent0000", "new content")

    async def test_edit_raises_on_inbox_block(self, system) -> None:
        async with system.session():
            learned = await system.learn("Not yet consolidated.")
        # No consolidate() — block is still in inbox, not active.
        with pytest.raises(BlockNotFound):
            await system.edit(learned.block_id, "new content")

    async def test_edit_raises_on_archived_block(self, system) -> None:
        async with system.session():
            learned = await system.learn("Will be forgotten.")
            await system.consolidate()
        await system.forget(learned.block_id)
        with pytest.raises(BlockNotFound):
            await system.edit(learned.block_id, "new content")


class TestForget:
    async def test_forget_archives_active_block(self, system) -> None:
        async with system.session():
            learned = await system.learn("Temporary preference.")
            await system.consolidate()

        result = await system.forget(learned.block_id)
        assert isinstance(result, ForgetResult)
        assert result.status == "forgotten"

        async with system._engine.begin() as conn:
            block = await get_block(conn, learned.block_id)
        assert block is not None
        assert block["status"] == "archived"
        assert block["archive_reason"] == "forgotten"

    async def test_forget_already_archived_is_idempotent(self, system) -> None:
        async with system.session():
            learned = await system.learn("Temporary preference.")
            await system.consolidate()

        first = await system.forget(learned.block_id)
        assert first.status == "forgotten"

        second = await system.forget(learned.block_id)
        assert second.status == "already_archived"

    async def test_forget_raises_on_missing_block(self, system) -> None:
        with pytest.raises(BlockNotFound):
            await system.forget("nonexistent0000")

    async def test_forget_removes_block_from_ls(self, system) -> None:
        async with system.session():
            learned = await system.learn("Will be forgotten.")
            await system.consolidate()

        assert any(b.id == learned.block_id for b in await system.ls())
        await system.forget(learned.block_id)
        assert not any(b.id == learned.block_id for b in await system.ls())


class TestLs:
    async def test_ls_returns_active_blocks(self, system) -> None:
        async with system.session():
            await system.learn("First active block.")
            await system.learn("Second active block.")
            await system.consolidate()

        results = await system.ls()
        assert len(results) >= 2
        assert all(isinstance(r, BlockSummary) for r in results)

    async def test_ls_excludes_inbox_blocks(self, system) -> None:
        async with system.session():
            await system.learn("Still in inbox.")
        # No consolidate() — nothing should be active.
        assert await system.ls() == []

    async def test_ls_excludes_forgotten_blocks(self, system) -> None:
        async with system.session():
            learned = await system.learn("Will be forgotten.")
            await system.consolidate()
        await system.forget(learned.block_id)
        assert await system.ls() == []

    async def test_ls_filters_by_category(self, system) -> None:
        async with system.session():
            await system.learn("A knowledge block.", category="knowledge")
            await system.learn("A task block.", category="task")
            await system.consolidate()

        knowledge_only = await system.ls(category="knowledge")
        assert knowledge_only
        assert all(r.category == "knowledge" for r in knowledge_only)

    async def test_ls_filters_by_tag_pattern(self, system) -> None:
        llm = MockLLMService(
            default_alignment=0.65,
            tag_overrides={"constitutional-marker": ["self/constitutional"]},
        )
        mem = MemorySystem(
            engine=system._engine,
            llm_service=llm,
            embedding_service=MockEmbeddingService(dimensions=64),
        )
        async with mem.session():
            await mem.learn("A constitutional-marker principle.")
            await mem.learn("An ordinary block.")
            await mem.consolidate()

        self_tagged = await mem.ls(tag="self/%")
        assert self_tagged
        assert all(any(t.startswith("self/") for t in r.tags) for r in self_tagged)
        await mem.close()

    async def test_ls_makes_zero_llm_or_embedding_calls(self, system) -> None:
        async with system.session():
            await system.learn("Some block.")
            await system.consolidate()

        llm_calls_before = system._llm.process_block_calls
        embed_calls_before = system._embedding.embed_calls

        await system.ls()

        assert system._llm.process_block_calls == llm_calls_before
        assert system._embedding.embed_calls == embed_calls_before

    async def test_ls_respects_limit(self, system) -> None:
        async with system.session():
            for i in range(5):
                await system.learn(f"Block number {i}.")
            await system.consolidate()

        assert len(await system.ls(limit=2)) == 2
