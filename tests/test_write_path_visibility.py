"""The write path must report what it silently reduced.

`remember` -> inbox -> `consolidate` -> frame render has four stages, and each
one can deliver less than the caller intended while stage one returns success.
An integrator seeded ten constitutional principles, got ten `status="created"`
results, and the agent saw none of them; then five; then five in someone
else's words (docs/integration_friction_report.md).

These tests pin the reporting, not just the behaviour: the fix for that class
of bug is that every stage which reduces the caller's intent says so in its
typed result.
"""

from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.context.rendering import render_blocks
from elfmem.types import ScoredBlock


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


def _block(bid: str, content: str, tags: list[str], score: float = 0.5) -> ScoredBlock:
    return ScoredBlock(
        id=bid, content=content, tags=tags, similarity=0.0, confidence=0.5,
        recency=0.5, centrality=0.5, reinforcement=0.5, score=score,
    )


async def _seed_constitution(system: MemorySystem, n: int) -> None:
    """n deliberately distinct principles, fully consolidated."""
    async with system.session():
        for i in range(n):
            await system.remember(
                f"Principle {i}: {['alpha', 'beta', 'gamma', 'delta'][i % 4]} "
                f"governs decision class {i} and nothing else.",
                tags=["self/constitutional"],
                cue=f"when principle {i} applies",
            )
        for _ in range(n):  # drain past max_inbox_per_run
            await system.consolidate()


# ── Stage 1: stored is not visible ───────────────────────────────────────────


class TestPendingConsolidation:
    async def test_remember_reports_it_is_not_yet_visible(self, system: MemorySystem):
        """`status="created"` is true and, alone, misleading."""
        async with system.session():
            result = await system.remember("A principle.", cue="when it applies")
        assert result.status == "created"
        assert result.pending_consolidation is True
        assert result.visible is False
        assert "not yet retrievable" in result.summary

    async def test_relearning_active_content_is_also_pending(self, system: MemorySystem):
        """Re-remembering already-active content does NOT dedupe: learn()
        deliberately inserts a fresh inbox block so consolidation's
        embedding-based near-duplicate pass can reconcile the pair. That new
        block is genuinely not visible yet, and says so."""
        async with system.session():
            first = await system.remember("A principle.", cue="when it applies")
            await system.consolidate()
            again = await system.remember("A principle.", cue="when it applies")
        assert again.block_id != first.block_id
        assert again.pending_consolidation is True

    async def test_mind_create_against_an_active_mind_is_visible(
        self, system: MemorySystem
    ):
        """The one path that reports visible=True: a mind_create matching a
        mind already promoted to active by predict()."""
        async with system.session():
            first = await system.mind_create("customer", goals=["Ship fast"])
            await system.mind_predict(
                first.block_id, "Will pay 49/mo", verify_at="2026-06-30"
            )
            again = await system.mind_create("customer", goals=["Ship fast"])
        assert again.status == "duplicate_rejected"
        assert again.visible is True
        assert again.pending_consolidation is False

    async def test_to_dict_carries_visibility(self, system: MemorySystem):
        async with system.session():
            result = await system.remember("A principle.", cue="x")
        assert result.to_dict()["pending_consolidation"] is True
        assert result.to_dict()["visible"] is False


# ── Stage 4: rendered ────────────────────────────────────────────────────────


class TestTopKNoLongerStarvesGuarantees:
    async def test_default_top_k_grows_to_fit_the_guarantee(self, system: MemorySystem):
        """Ten principles must not render five just because top_k defaults to 5.

        The default is now max(config.top_k, n_guaranteed). Well inside the
        600-token budget, so nothing else can explain a short render.
        """
        await _seed_constitution(system, 10)
        result = await system.frame("self")
        assert len(result.blocks) > system._config.memory.top_k
        assert result.budget_used < result.budget_total

    async def test_explicit_top_k_is_still_a_hard_ceiling(self, system: MemorySystem):
        """An explicit argument always binds. Silently exceeding it would be
        the same bug as silently ignoring host_analyses."""
        await _seed_constitution(system, 10)
        result = await system.frame("self", top_k=3)
        assert len(result.blocks) == 3

    async def test_explicit_top_k_reports_what_it_cost(self, system: MemorySystem):
        await _seed_constitution(system, 10)
        result = await system.frame("self", top_k=3)
        assert result.dropped, "a bound ceiling that drops blocks must say so"
        assert "top_k" in result.dropped_reasons
        assert "dropped" in result.summary


class TestBudgetReporting:
    def test_renderer_reports_what_would_not_fit(self):
        blocks = [_block(str(i), "x" * 400, ["self/constitutional"]) for i in range(10)]
        result = render_blocks(blocks, "self", token_budget=200)
        assert result.dropped
        assert len(result.selected) + len(result.dropped) == len(blocks)
        assert result.budget_used <= 200

    def test_oversized_block_renders_instead_of_vanishing(self):
        """A single block larger than the whole budget used to render "".

        An empty identity the agent believes is whole is the worst outcome
        this library can produce, so the block is rendered and the overrun
        is made visible instead.
        """
        blocks = [_block("big", "y" * 8000, ["self/constitutional"])]
        result = render_blocks(blocks, "self", token_budget=200)
        assert result.text != ""
        assert result.selected == blocks
        assert result.budget_used > 200

    def test_oversized_content_is_never_truncated(self):
        """Cutting a principle mid-sentence can invert it ("never do X" ->
        "never do"), so the budget is overrun rather than the meaning."""
        content = "Never deploy on a Friday without a rollback plan. " * 200
        result = render_blocks(
            [_block("big", content, ["self/constitutional"])], "self", token_budget=50
        )
        assert content in result.text

    async def test_frame_reports_budget_totals(self, system: MemorySystem):
        await _seed_constitution(system, 3)
        result = await system.frame("self")
        assert result.budget_total == 600
        assert result.budget_used > 0


class TestNothingDroppedIsDistinguishable:
    async def test_complete_frame_reports_no_drops(self, system: MemorySystem):
        """"This is everything" must be distinguishable from "this is the
        first five of ten" -- the distinction the report was written about."""
        await _seed_constitution(system, 3)
        result = await system.frame("self")
        assert result.dropped == []
        assert result.dropped_reasons == set()
        assert "dropped" not in result.summary


class TestContradictionSuppressionIsReported:
    """The third silent reducer, and the one the friction report never reached.

    Found by running `doctor --frames` against a real corpus: eight seeded
    principles rendered three with *nothing* reported as dropped.
    Consolidation flags near-duplicate pairs rather than destroying either
    half (ADR 0010), and retrieval then shows only the higher-confidence one.
    """

    async def test_suppressed_block_is_reported_as_dropped(self, system: MemorySystem):
        """Seeded directly rather than via near-duplicate detection, so the
        assertion tests recall's reporting instead of the mock embedder's
        similarity behaviour."""
        from elfmem.db.queries import insert_contradiction

        async with system.session():
            keep = await system.remember(
                "Alpha governs decision class one.",
                tags=["self/constitutional"], cue="alpha",
            )
            drop = await system.remember(
                "Beta governs decision class two.",
                tags=["self/constitutional"], cue="beta",
            )
            await system.consolidate()
            await system.consolidate()
            # Higher confidence wins suppression; make `keep` the winner.
            await system.outcome([keep.block_id], 1.0)

        async with system._engine.begin() as conn:
            await insert_contradiction(
                conn,
                block_a_id=keep.block_id,
                block_b_id=drop.block_id,
                score=0.95,
                kind="near_duplicate",
            )

        system._frame_cache.clear()
        result = await system.frame("self")

        rendered_ids = {b.id for b in result.blocks}
        assert len(rendered_ids) == 1, "suppression must remove one half of the pair"
        suppressed = [d for d in result.dropped if d.reason == "contradiction"]
        assert suppressed, "the suppressed half must be reported, not silently removed"
        assert "contradiction" in result.dropped_reasons
        assert suppressed[0].id not in rendered_ids


# ── Stage 3: consolidation ───────────────────────────────────────────────────


class TestHostAnalysesOverflow:
    async def test_unapplied_analyses_are_reported(self, system: MemorySystem):
        """Silently substituting LLM analysis for caller-supplied analysis
        inverts an explicit instruction -- the caller passed host_analyses
        precisely to stop the rewrite that a later pass will now perform."""
        async with system.session():
            ids = [
                (await system.remember(f"Fact {i} about widgets.", cue=f"widget {i}")).block_id
                for i in range(8)
            ]
            analyses = {
                bid: {"alignment_score": 0.9, "tags": ["self/value"], "summary": "verbatim"}
                for bid in ids
            }
            result = await system.consolidate(max_inbox_per_run=3, host_analyses=analyses)

        assert result.processed == 3
        assert len(result.analyses_unused) == 5
        assert set(result.analyses_unused) <= set(ids)
        assert "NOT applied" in result.summary
        assert result.to_dict()["analyses_unused"] == result.analyses_unused

    async def test_no_report_when_everything_applied(self, system: MemorySystem):
        async with system.session():
            ids = [
                (await system.remember(f"Fact {i}.", cue=f"f{i}")).block_id
                for i in range(2)
            ]
            analyses = {
                bid: {"alignment_score": 0.9, "tags": [], "summary": "verbatim"}
                for bid in ids
            }
            result = await system.consolidate(host_analyses=analyses)
        assert result.analyses_unused == []
        assert "NOT applied" not in result.summary


# ── Entry point ──────────────────────────────────────────────────────────────


class TestFromConfigTypeCheck:
    async def test_non_path_db_argument_raises_typeerror(self):
        """Passing a config object first produced `OSError: [Errno 63] File
        name too long: "Config(raw={'llm'...` — a genuinely baffling minute."""

        class Config:
            pass

        with pytest.raises(TypeError, match="db_path must be str or os.PathLike"):
            await MemorySystem.from_config(Config())  # type: ignore[arg-type]

    async def test_error_names_the_actual_type_and_the_fix(self):
        class Config:
            pass

        with pytest.raises(TypeError) as exc:
            await MemorySystem.from_config(Config())  # type: ignore[arg-type]
        assert "Config" in str(exc.value)
        assert "second argument" in str(exc.value)


class TestPreviewDoesNotMutate:
    """`doctor --frames` renders every frame; if that reinforced, the
    diagnostic would inflate the scores of exactly the blocks it reports on,
    worse on every re-run — and `doctor` documents itself as read-only."""

    async def test_reinforce_false_leaves_scoring_untouched(self, system: MemorySystem):
        from elfmem.db.queries import get_active_blocks

        await _seed_constitution(system, 4)

        async with system._engine.begin() as conn:
            before = {b["id"]: b["reinforcement_count"] for b in await get_active_blocks(conn)}

        system._frame_cache.clear()
        await system.frame("self", reinforce=False)

        async with system._engine.begin() as conn:
            after = {b["id"]: b["reinforcement_count"] for b in await get_active_blocks(conn)}

        assert before == after

    async def test_default_still_reinforces(self, system: MemorySystem):
        """Guard against over-applying the preview: normal retrieval must
        still strengthen what it returns, or memory stops learning from use."""
        from elfmem.db.queries import get_active_blocks

        await _seed_constitution(system, 4)

        async with system._engine.begin() as conn:
            before = {b["id"]: b["reinforcement_count"] for b in await get_active_blocks(conn)}

        system._frame_cache.clear()
        result = await system.frame("self")

        async with system._engine.begin() as conn:
            after = {b["id"]: b["reinforcement_count"] for b in await get_active_blocks(conn)}

        rendered = {b.id for b in result.blocks}
        assert rendered, "fixture must render something for this to mean anything"
        assert any(after[bid] > before[bid] for bid in rendered)
