"""Tests for ADR 0007 — bound and checkpoint consolidation for slow LLM adapters.

Three independent guarantees, each verified directly:
- contradiction_top_k bounds per-block LLM calls regardless of active-set size
- max_inbox_per_run bounds and resumes inbox processing across dream() calls
- rescore_blocks() commits durably per block — a crash mid-batch does not
  lose already-rescored blocks

See docs/decisions/0007-bound-and-checkpoint-consolidation.md and
docs/plans/plan_consolidation_checkpointing.md.
"""

from __future__ import annotations

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.queries import get_block, insert_block
from elfmem.operations.rescore import rescore_blocks


class TestContradictionTopKCap:
    """Change 1: cap contradiction LLM calls per block to contradiction_top_k."""

    async def test_caps_llm_calls_regardless_of_candidate_count(
        self, test_engine,
    ) -> None:
        embedding = MockEmbeddingService(dimensions=64)
        llm = MockLLMService()

        new_content = "a fresh claim about quarterly pricing strategy"
        new_norm = new_content.strip().lower()
        await embedding.embed(new_norm)  # cache the anchor before seeding actives

        n_active = 12
        top_k = 5
        overrides: dict[frozenset[str], float] = {}
        active_contents = []
        for i in range(n_active):
            c = f"active claim number {i} about something unrelated"
            active_contents.append(c)
            # 0.6: clears the 0.40 contradiction prefilter, stays below the
            # 0.90 near-dup threshold so it doesn't get superseded instead.
            overrides[frozenset({c.strip().lower(), new_norm})] = 0.6
        embedding._similarity_overrides = overrides

        cfg = ElfmemConfig(
            memory=MemoryConfig(
                inbox_threshold=100,
                max_inbox_per_run=100,
                contradiction_top_k=top_k,
            ),
        )
        system = MemorySystem(
            engine=test_engine, llm_service=llm, embedding_service=embedding, config=cfg,
        )
        await system.begin_session()

        # Seed n_active active blocks without contradiction checks — they
        # only need to exist as active, not be involved in this dream().
        for c in active_contents:
            await system.learn(c)
        await system.dream(skip_contradictions=True)
        assert llm.contradiction_calls == 0

        # The block under test: compares against all n_active seeded blocks,
        # all of which clear the prefilter (0.6 similarity).
        await system.learn(new_content)
        result = await system.dream()

        assert result is not None
        assert llm.contradiction_calls == top_k
        assert result.health is not None
        assert result.health.contradiction_cap_rate > 0.0


class TestInboxBudget:
    """Change 4: max_inbox_per_run bounds a single dream() call and reports
    what's left, so a large backlog drains across repeated calls instead of
    one unbounded run."""

    async def test_budget_caps_processing_and_resumes(
        self, test_engine, mock_llm, mock_embedding,
    ) -> None:
        cfg = ElfmemConfig(
            memory=MemoryConfig(inbox_threshold=100, max_inbox_per_run=3),
        )
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            config=cfg,
        )
        await system.begin_session()
        for i in range(10):
            await system.learn(f"fact {i}")

        first = await system.dream()
        assert first is not None
        assert first.processed == 3
        assert first.inbox_remaining == 7
        assert (await system.status()).pending_count == 7

        second = await system.dream()
        assert second is not None
        assert second.processed == 3
        assert second.inbox_remaining == 4

        third = await system.dream()
        assert third is not None
        assert third.processed == 3
        assert third.inbox_remaining == 1

        fourth = await system.dream()
        assert fourth is not None
        assert fourth.processed == 1
        assert fourth.inbox_remaining == 0
        assert (await system.status()).pending_count == 0

    async def test_large_max_drains_in_one_call(
        self, test_engine, mock_llm, mock_embedding,
    ) -> None:
        """Matches the existing rescore() convention: pass a large budget for
        a one-shot full-drain sweep, rather than None-for-unbounded."""
        cfg = ElfmemConfig(
            memory=MemoryConfig(inbox_threshold=100, max_inbox_per_run=3),
        )
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
            config=cfg,
        )
        await system.begin_session()
        for i in range(10):
            await system.learn(f"fact {i}")

        result = await system.consolidate(max_inbox_per_run=1000)
        assert result.processed == 10
        assert result.inbox_remaining == 0


class _RaisingAfterN(MockEmbeddingService):
    """Raises on the (n+1)th embed() call — simulates a crash mid-batch at a
    point rescore_blocks() does NOT catch (unlike the LLM call, which has its
    own try/except and degrades to a counted failure instead)."""

    def __init__(self, *, fail_after: int, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._fail_after = fail_after
        self._n_calls = 0

    async def embed(self, text: str):  # noqa: ANN201 — matches base signature
        self._n_calls += 1
        if self._n_calls > self._fail_after:
            raise RuntimeError("simulated crash mid-batch")
        return await super().embed(text)


class TestRescoreDurability:
    """Change 3: rescore_blocks() commits each block independently, so a
    crash partway through preserves every block already rescored instead of
    rolling back the whole batch (the pre-ADR-0007 behaviour)."""

    async def test_crash_mid_batch_preserves_prior_commits(
        self, test_engine,
    ) -> None:
        llm = MockLLMService(default_alignment=0.75)
        ids = ["block-a", "block-b", "block-c"]
        async with test_engine.begin() as conn:
            for bid in ids:
                await insert_block(
                    conn, block_id=bid, content=f"content for {bid}",
                    category="knowledge", source="test", status="active",
                )

        # Block 1's embed() succeeds; block 2's raises before its write.
        embedding = _RaisingAfterN(fail_after=1, dimensions=64)

        with pytest.raises(RuntimeError):
            await rescore_blocks(
                test_engine, block_ids=ids, llm=llm, embedding_svc=embedding,
            )

        async with test_engine.connect() as conn:
            a = await get_block(conn, "block-a")
            b = await get_block(conn, "block-b")
            c = await get_block(conn, "block-c")

        assert a["last_scored_at"] is not None  # committed before the crash
        assert b["last_scored_at"] is None  # crashed before its own commit
        assert c["last_scored_at"] is None  # loop never reached it
