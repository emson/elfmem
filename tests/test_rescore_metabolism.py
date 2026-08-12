"""Tests for edge-metabolism Stage A (docs/plans/plan_edge_metabolism.md).

Everything under test here is read-only: proposes goal-directed connections
and reports them, never writes to the ``edges`` table. Stage B (applying
proposals live) is a separate, not-yet-approved decision — see the plan
doc's "Decision needed" section.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.operations.rescore import (
    GOAL_DIRECTED_CANDIDATE_K,
    metabolism_dry_run,
    select_goal_directed_candidates,
)
from elfmem.types import GoalDirectedEdgeProposal, MetabolismDryRunResult


def _vec(seed: int, dims: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dims).astype(np.float32)
    return v / np.linalg.norm(v)


# ── Pure candidate selection (no DB, no LLM) ────────────────────────────────


class TestSelectGoalDirectedCandidates:
    def test_excludes_the_block_itself(self):
        target_vec = _vec(1)
        all_active = [
            ({"id": "self"}, target_vec),
            ({"id": "other"}, _vec(2)),
        ]
        result = select_goal_directed_candidates("self", target_vec, all_active, k=10)
        ids = [cid for cid, _score in result]
        assert "self" not in ids
        assert "other" in ids

    def test_returns_top_k_by_cosine_descending(self):
        target_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        all_active = [
            ({"id": "close"}, np.array([0.9, 0.1, 0.0], dtype=np.float32)),
            ({"id": "far"}, np.array([0.0, 0.0, 1.0], dtype=np.float32)),
            ({"id": "medium"}, np.array([0.5, 0.5, 0.0], dtype=np.float32)),
        ]
        result = select_goal_directed_candidates("target", target_vec, all_active, k=2)
        ids = [cid for cid, _score in result]
        assert ids == ["close", "medium"]  # top-2 by cosine, far excluded

    def test_no_threshold_cutoff_unlike_similarity_edges(self):
        """Deliberately unthresholded — a low-cosine candidate still appears
        if it's within the top-K. Goal relevance is the LLM's job, not a
        numeric floor (see module docstring)."""
        target_vec = np.array([1.0, 0.0], dtype=np.float32)
        all_active = [
            ({"id": "orthogonal"}, np.array([0.0, 1.0], dtype=np.float32)),
        ]
        result = select_goal_directed_candidates("target", target_vec, all_active, k=5)
        assert len(result) == 1
        assert result[0][0] == "orthogonal"

    def test_default_k_matches_module_constant(self):
        target_vec = _vec(1)
        all_active = [({"id": f"b{i}"}, _vec(i + 2)) for i in range(50)]
        result = select_goal_directed_candidates("target", target_vec, all_active)
        assert len(result) == GOAL_DIRECTED_CANDIDATE_K


# ── metabolism_dry_run orchestration (DB-backed, mock LLM) ──────────────────


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding):
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    return MemorySystem(
        engine=test_engine, llm_service=mock_llm,
        embedding_service=mock_embedding, config=cfg,
    )


async def _active_ids(test_engine) -> list[str]:
    async with test_engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT id FROM blocks WHERE status='active'")
        )).fetchall()
    return [r[0] for r in rows]


async def _edge_count(test_engine) -> int:
    async with test_engine.connect() as conn:
        return (await conn.execute(text("SELECT COUNT(*) FROM edges"))).fetchone()[0]


class TestMetabolismDryRunNeverWrites:
    async def test_edges_table_unchanged_before_and_after(self, system, test_engine):
        await system.learn("Build elfmem as the definitive adaptive memory library.",
                            tags=["self/goal"])
        await system.learn("A technical note about SQLite WAL mode.")
        await system.learn("Another fact entirely unrelated.")
        await system.consolidate()

        before = await _edge_count(test_engine)
        ids = await _active_ids(test_engine)
        async with test_engine.connect() as conn:
            await metabolism_dry_run(conn, block_ids=ids, llm=system._llm)
        after = await _edge_count(test_engine)
        assert before == after


class TestMetabolismDryRunNoGoals:
    async def test_zero_self_goals_reports_and_proposes_nothing(self, system, test_engine):
        await system.learn("A fact with no self/goal context anywhere.")
        await system.learn("filler")
        await system.consolidate()
        ids = await _active_ids(test_engine)

        async with test_engine.connect() as conn:
            result = await metabolism_dry_run(conn, block_ids=ids, llm=system._llm)

        assert result.self_goals == []
        assert result.proposals == []
        assert result.blocks_considered == len(ids)
        # Candidates are still recorded even with zero self_goals — a
        # caller reasoning by hand still wants to see what was there.
        assert all(result.candidates[bid] for bid in ids)


class TestMetabolismDryRunProposals:
    async def test_valid_proposal_is_recorded_with_reasoning(self, test_engine, mock_embedding):
        target_content = "Ship the v2 file substrate migration."
        candidate_content = "Decision: markdown files become the source of truth."

        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=5))
        mock_llm = MockLLMService(
            goal_directed_edge_overrides={
                target_content.lower(): [
                    {"candidate_id": "__PLACEHOLDER__", "reasoning": "serves the migration goal"},
                ],
            },
        )
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        await system.learn("Ship elfmem's v2 migration.", tags=["self/goal"])
        await system.learn(target_content)
        await system.learn(candidate_content)
        await system.consolidate()

        ids = await _active_ids(test_engine)
        # Resolve the real candidate id and rewrite the mock's override to
        # reference it — the mock can't know DB-assigned ids in advance.
        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id, content FROM blocks WHERE status='active'")
            )).fetchall()
        candidate_id = next(r[0] for r in rows if r[1] == candidate_content)
        target_id = next(r[0] for r in rows if r[1] == target_content)
        mock_llm.goal_directed_edge_overrides = {
            target_content.lower(): [
                {"candidate_id": candidate_id, "reasoning": "serves the migration goal"},
            ],
        }

        async with test_engine.connect() as conn:
            result = await metabolism_dry_run(conn, block_ids=ids, llm=mock_llm)

        assert len(result.self_goals) == 1
        matching = [p for p in result.proposals if p.block_id == target_id]
        assert len(matching) == 1
        assert matching[0].candidate_id == candidate_id
        assert matching[0].reasoning == "serves the migration goal"

    async def test_hallucinated_candidate_id_is_dropped(self, test_engine, mock_embedding):
        target_content = "A block with a goal-adjacent thought."
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=5))
        mock_llm = MockLLMService(
            goal_directed_edge_overrides={
                target_content.lower(): [
                    {"candidate_id": "not-a-real-id", "reasoning": "hallucinated"},
                ],
            },
        )
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        await system.learn("Pursue interesting goals.", tags=["self/goal"])
        await system.learn(target_content)
        await system.learn("A real candidate block.")
        await system.consolidate()
        ids = await _active_ids(test_engine)

        async with test_engine.connect() as conn:
            result = await metabolism_dry_run(conn, block_ids=ids, llm=mock_llm)

        assert result.proposals == []

    async def test_llm_failure_on_one_block_does_not_abort_the_batch(
        self, test_engine, mock_embedding,
    ):
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=5))
        mock_llm = MockLLMService(
            goal_directed_edge_raise_for=["explode"],
        )
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        await system.learn("A goal to keep going.", tags=["self/goal"])
        await system.learn("this block will explode during metabolism")
        await system.learn("a perfectly fine second block")
        await system.learn("a perfectly fine third block")
        await system.consolidate()
        ids = await _active_ids(test_engine)

        async with test_engine.connect() as conn:
            result = await metabolism_dry_run(conn, block_ids=ids, llm=mock_llm)

        assert result.llm_failures == 1
        assert result.blocks_considered == len(ids)

    async def test_caps_proposals_at_max_edges_per_block(self, test_engine, mock_embedding):
        target_content = "A block that could connect to many things."
        cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=10))
        candidate_contents = [f"candidate block number {i}" for i in range(5)]
        mock_llm = MockLLMService()
        system = MemorySystem(
            engine=test_engine, llm_service=mock_llm,
            embedding_service=mock_embedding, config=cfg,
        )
        await system.learn("A broad goal.", tags=["self/goal"])
        await system.learn(target_content)
        for c in candidate_contents:
            await system.learn(c)
        await system.consolidate()

        async with test_engine.connect() as conn:
            rows = (await conn.execute(
                text("SELECT id, content FROM blocks WHERE status='active'")
            )).fetchall()
        candidate_ids = [r[0] for r in rows if r[1] in candidate_contents]
        target_id = next(r[0] for r in rows if r[1] == target_content)
        mock_llm.goal_directed_edge_overrides = {
            target_content.lower(): [
                {"candidate_id": cid, "reasoning": "reason"} for cid in candidate_ids
            ],
        }
        ids = await _active_ids(test_engine)

        async with test_engine.connect() as conn:
            result = await metabolism_dry_run(
                conn, block_ids=ids, llm=mock_llm, max_edges_per_block=2,
            )

        matching = [p for p in result.proposals if p.block_id == target_id]
        assert len(matching) == 2  # capped, even though 5 were offered


# ── MemorySystem.metabolism_dry_run() integration ───────────────────────────


class TestMemorySystemMetabolismDryRun:
    async def test_returns_typed_result_and_writes_nothing(self, system, test_engine):
        # skip_llm=True leaves last_scored_at NULL -> unconditionally
        # rescore-eligible (debt, no 24h cooldown) — matches test_rescore.py's
        # own pattern for exercising anything downstream of
        # select_rescore_candidates. A normal full-LLM consolidate() stamps
        # last_scored_at=now, which the cooldown would then exclude here.
        await system.learn("A stated goal.", tags=["self/goal"])
        await system.learn("Some candidate content.")
        await system.consolidate(skip_llm=True)

        before = await _edge_count(test_engine)
        result = await system.metabolism_dry_run()
        after = await _edge_count(test_engine)

        assert isinstance(result, MetabolismDryRunResult)
        assert result.blocks_considered > 0
        assert before == after

    async def test_respects_max_count_budget(self, system, test_engine):
        await system.learn("A goal.", tags=["self/goal"])
        for i in range(5):
            await system.learn(f"filler block {i}")
        await system.consolidate(skip_llm=True, max_inbox_per_run=100)

        result = await system.metabolism_dry_run(max_count=1)
        assert result.blocks_considered <= 1


# ── MockLLMService.propose_goal_directed_edges ──────────────────────────────


class TestMockProposeGoalDirectedEdges:
    async def test_defaults_to_empty_list(self):
        mock = MockLLMService()
        result = await mock.propose_goal_directed_edges(
            block_content="anything", block_summary=None,
            self_goals=["a goal"], candidates=[("id1", "content1")], max_edges=3,
        )
        assert result == []
        assert mock.propose_goal_directed_edges_calls == 1

    async def test_override_match_returns_configured_payload(self):
        mock = MockLLMService(
            goal_directed_edge_overrides={
                "keyword": [{"candidate_id": "id1", "reasoning": "matched"}],
            },
        )
        result = await mock.propose_goal_directed_edges(
            block_content="a block with the keyword in it", block_summary=None,
            self_goals=["a goal"], candidates=[("id1", "content1")], max_edges=3,
        )
        assert result == [{"candidate_id": "id1", "reasoning": "matched"}]

    async def test_raise_for_match_raises_runtime_error(self):
        mock = MockLLMService(goal_directed_edge_raise_for=["boom"])
        with pytest.raises(RuntimeError):
            await mock.propose_goal_directed_edges(
                block_content="this will boom", block_summary=None,
                self_goals=[], candidates=[], max_edges=3,
            )


# ── Type contract: GoalDirectedEdgeProposal / MetabolismDryRunResult ────────


class TestResultTypes:
    def test_metabolism_dry_run_result_summary_when_no_goals(self):
        r = MetabolismDryRunResult(blocks_considered=3)
        assert "no self/goal blocks" in r.summary

    def test_metabolism_dry_run_result_summary_with_proposals(self):
        r = MetabolismDryRunResult(
            blocks_considered=2, self_goals=["a goal"],
            proposals=[GoalDirectedEdgeProposal("b1", "c1", "reason")],
        )
        assert "1 connection(s) proposed" in r.summary
        assert str(r) == r.summary

    def test_candidates_recorded_even_without_llm(self):
        """The whole point of enriching the result: a caller with no
        configured LLM (llm_failures == blocks_considered) still gets
        the raw candidates to reason over itself."""
        r = MetabolismDryRunResult(
            blocks_considered=1, self_goals=["a goal"],
            candidates={"b1": [("c1", "candidate summary")]},
            llm_failures=1,
        )
        assert r.candidates["b1"] == [("c1", "candidate summary")]
        assert r.to_dict()["candidates"]["b1"] == [{"id": "c1", "content": "candidate summary"}]

    def test_to_dict_round_trips_proposal_fields(self):
        p = GoalDirectedEdgeProposal(block_id="b1", candidate_id="c1", reasoning="why")
        assert p.to_dict() == {"block_id": "b1", "candidate_id": "c1", "reasoning": "why"}
