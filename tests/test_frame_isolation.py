"""Identity and knowledge must not compete for the same slots or the same scores.

Both failures in docs/frames_and_credit_assignment_report.md are the same
category error: `self/constitutional` was granting three privileges when only
two were designed — PERMANENT decay, a guaranteed SELF slot, and unrestricted
competition in every other frame plus immunity from nothing at all.

Measured before fixing (see the report and the frames.py comment): a seeded
ten-principle constitution took 4 of 5 ATTENTION slots and dropped every
market fact including the agent's own open position; one losing trade moved a
principle's confidence from 0.50 to 0.275, and six took it to 0.114.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as _sql

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.queries import get_block

PRINCIPLES = [
    "Recent direction is evidence about the recent past, not about structure.",
    "When live data contradicts a thesis's premise, the thesis is void.",
    "When evidence conflicts with something I hold, name the conflict.",
    "Context serves the decision at hand, not general interest.",
    "A pattern learned in one regime is a hypothesis in another, not a rule.",
    "Name uncertainty before acting on a belief.",
]
KNOWLEDGE = [
    "The agent holds a short 755 put on SPY expiring in 14 days.",
    "SPY implied volatility rank sat at 22 percent when the spread was opened.",
    "Bull put spreads on SPY have filled best between 09:45 and 10:15 ET.",
    "The 755 strike sits just below the 50-day moving average on SPY.",
    "Assignment risk on SPY puts rises sharply inside 5 days to expiry.",
]


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding) -> MemorySystem:
    return MemorySystem(
        engine=test_engine, llm_service=mock_llm, embedding_service=mock_embedding,
        config=ElfmemConfig(memory=MemoryConfig(inbox_threshold=3)),
    )


async def _seed(system: MemorySystem, *, principles=True, knowledge=True) -> None:
    async with system.session():
        if principles:
            for t in PRINCIPLES:
                await system.remember(t, tags=["self/constitutional"], cue="principle")
        if knowledge:
            for k in KNOWLEDGE:
                await system.remember(k, tags=["market/spy"], cue="spy")
        for _ in range(12):
            await system.consolidate()


# ── Issue 1: a constitution must not starve ATTENTION ────────────────────────


class TestAttentionExcludesIdentity:
    async def test_knowledge_wins_its_own_frame_back(self, system: MemorySystem):
        await _seed(system)
        result = await system.frame("attention", query="SPY bull put spread, thesis intact")
        assert result.blocks, "ATTENTION must still return knowledge"
        assert not any("self/constitutional" in b.tags for b in result.blocks)
        assert result.excluded_by_filter == len(PRINCIPLES)

    async def test_self_frame_is_unaffected(self, system: MemorySystem):
        """The exclusion moves identity out of ATTENTION, it does not delete it:
        SELF is queryless and injected on its own, so the principles still
        reach the prompt exactly once instead of twice."""
        await _seed(system)
        self_frame = await system.frame("self")
        assert len(self_frame.blocks) == len(PRINCIPLES)

    async def test_no_block_is_served_by_both_frames(self, system: MemorySystem):
        await _seed(system)
        attention = await system.frame("attention", query="SPY spread")
        self_frame = await system.frame("self")
        overlap = {b.id for b in attention.blocks} & {b.id for b in self_frame.blocks}
        assert overlap == set(), "double-served blocks cost budget twice"

    async def test_empty_result_explains_itself(self, system: MemorySystem):
        """Edge case: the corpus is entirely constitutional, so ATTENTION has
        nothing left. Returning nothing is correct — SELF still carries the
        identity — but 'no blocks found' alone is the confusing-empty-result
        the first report was written about."""
        await _seed(system, knowledge=False)
        result = await system.frame("attention", query="what do I know about SPY")
        assert result.blocks == []
        assert result.excluded_by_filter == len(PRINCIPLES)
        assert "excluded by this frame's tag filter" in result.summary

    async def test_peer_letters_are_exempt(self, system: MemorySystem):
        """`self/constitutional` is also assigned by the consolidating LLM, so
        on a mature instance it sits on peer letters that are real knowledge.
        Excluding those too made a peer-trust query measurably worse."""
        async with system.session():
            await system.remember(
                "Alv here. The outcome closure gap is the failure mode I fear most.",
                tags=["self/constitutional", "peer/inbound", "peer/from:elf:alv"],
                cue="peer letter on outcome closure",
            )
            await system.remember(
                "Recent direction is evidence about the recent past.",
                tags=["self/constitutional"], cue="principle",
            )
            for _ in range(6):
                await system.consolidate()

        result = await system.frame("attention", query="outcome closure gap failure mode")
        assert any("peer/from:elf:alv" in b.tags for b in result.blocks), (
            "a peer letter is knowledge, not identity — it keeps its slot"
        )
        assert result.excluded_by_filter == 1, "only the plain principle is excluded"

    async def test_excluded_blocks_are_never_candidates(self, system: MemorySystem):
        """The invariant, stated the way an integrator can check it: an excluded
        block appears in NEITHER `blocks` NOR `dropped`.

        Regression for a leak found in production use (report addendum,
        b079660d): the stage-1 prefilter is not the only way into the candidate
        pool. Stage 3 expands the graph by fetching neighbours from the database
        by id, so an excluded block that neighboured a seed walked back in
        behind the filter -- arriving with similarity=0.0 and still ranking,
        because constitutional blocks carry high confidence, high centrality
        (they are unusually well connected, which is what put them in reach of
        expansion), and a recency PERMANENT decay never erodes. A block dropped
        for `top_k` was by definition still a candidate, which is what made the
        count and the contents look like they disagreed.
        """
        async with system.session():
            principle = await system.remember(
                "A pattern learned in one regime is a hypothesis in another.",
                tags=["self/constitutional"], cue="regimes")
            fact = await system.remember(
                "The agent holds a short 755 put on SPY expiring in 14 days.",
                tags=["market/spy"], cue="spy position")
            for _ in range(4):
                await system.consolidate()
            # Consolidation builds these from similarity and a principle
            # co-occurs with everything, so an edge here is the normal case,
            # not a contrivance. Declared explicitly to keep the test
            # deterministic rather than dependent on the mock embedder.
            await system.connect(fact.block_id, principle.block_id, relation="similar")

        result = await system.frame("attention", query="SPY short put position")
        assert principle.block_id not in {b.id for b in result.blocks}
        assert principle.block_id not in {d.id for d in result.dropped}

    async def test_recall_agrees_with_frame(self, system: MemorySystem):
        """`recall(frame=...)` is documented as raw block data *without
        rendering* — raw means unrendered, not a different retrieval."""
        await _seed(system)
        blocks = await system.recall("SPY bull put spread", frame="attention", top_k=10)
        assert not any("self/constitutional" in b.tags for b in blocks)

    async def test_guarantee_beats_exclusion(self, system: MemorySystem):
        """A frame declaring both for the same block resolves toward the
        guarantee — the more specific, more deliberate declaration.

        Built by hand rather than through a built-in frame: no shipped frame
        both guarantees and excludes the same tag, so asserting against one
        would pass whether or not the precedence rule existed.
        """
        from dataclasses import replace

        from elfmem.context.frames import ATTENTION_FRAME
        from elfmem.operations.recall import recall as _recall

        await _seed(system)
        conflicted = replace(ATTENTION_FRAME, guarantees=["self/constitutional"])
        async with system._engine.begin() as conn:
            result = await _recall(
                conn,
                embedding_svc=system._embedding,
                frame_def=conflicted,
                query="SPY spread",
                current_active_hours=1.0,
                reinforce=False,
            )
        assert any("self/constitutional" in b.tags for b in result.blocks), (
            "the guarantee must win over the frame's own exclusion"
        )


# ── Issue 2: a task outcome must not score the constitution ──────────────────


class TestOutcomeProtectsIdentity:
    async def test_constitutional_block_is_not_scored(self, system: MemorySystem):
        async with system.session():
            principle = await system.remember(
                "A pattern in one regime is a hypothesis in another.",
                tags=["self/constitutional"], cue="regimes")
            fact = await system.remember(
                "SPY IV rank was 22 percent at entry.", tags=["market/spy"], cue="iv")
            await system.consolidate()
            await system.consolidate()

        async def confidence(block_id: str) -> float:
            async with system._engine.begin() as conn:
                return round(float((await get_block(conn, block_id))["confidence"]), 4)

        before = await confidence(principle.block_id)
        result = await system.outcome(
            [principle.block_id, fact.block_id], signal=0.05, source="losing-trade")

        assert await confidence(principle.block_id) == before, "identity untouched"
        assert await confidence(fact.block_id) < before, "the fact is still scored"
        assert result.skipped_constitutional == [principle.block_id]
        assert result.blocks_updated == 1
        assert "Not scored" in result.summary
        assert "constitutional" in result.summary

    async def test_repeated_losses_never_erode_identity(self, system: MemorySystem):
        """The measured pre-fix path: 0.50 -> 0.114 over six losing trades,
        which then drops the principle out of a budget-bound SELF frame."""
        async with system.session():
            principle = await system.remember(
                "Name uncertainty before acting.", tags=["self/constitutional"], cue="x")
            await system.consolidate()
            await system.consolidate()
        for _ in range(6):
            await system.outcome([principle.block_id], signal=0.05, source="loss")
        async with system._engine.begin() as conn:
            assert float((await get_block(conn, principle.block_id))["confidence"]) == 0.5

    async def test_escape_hatch_scores_deliberately(self, system: MemorySystem):
        async with system.session():
            principle = await system.remember(
                "A principle judged on its own merits.",
                tags=["self/constitutional"], cue="x")
            await system.consolidate()
            await system.consolidate()
        result = await system.outcome(
            [principle.block_id], signal=0.05, allow_constitutional=True)
        assert result.blocks_updated == 1
        assert result.skipped_constitutional == []

    async def test_peer_trust_still_moves_for_skipped_blocks(self, system: MemorySystem):
        """The edge case a naive fix breaks. 7 of 40 blocks on a real instance
        are peer letters carrying `self/constitutional`. Trust is a judgement
        about the peer's contribution, not about the block's standing as a
        principle, so it must survive the skip."""
        await system.peer_init("elf:me")
        await system.peer_add("elf:alv", "Alv")
        async with system.session():
            letter = await system.remember(
                "Alv's letter about outcome closure.",
                tags=["self/constitutional", "peer/from:elf:alv"], cue="alv")
            await system.consolidate()
            await system.consolidate()
        async with system._engine.begin() as conn:
            await conn.execute(
                _sql("UPDATE blocks SET source_peer=:p WHERE id=:i"),
                {"p": "elf:alv", "i": letter.block_id})

        before = (await system.peer_trust("elf:alv")).trust
        result = await system.outcome([letter.block_id], signal=0.95, source="useful")
        after = (await system.peer_trust("elf:alv")).trust

        assert result.skipped_constitutional == [letter.block_id]
        assert after > before, "peer trust must not be collateral damage"

    async def test_mind_calibration_is_not_blocked(self, system: MemorySystem):
        """mind_outcome scores one deliberately named block, so it bypasses the
        guard — and a mind block CAN accrete `self/constitutional` during
        consolidation, which would otherwise silently stop calibrating it."""
        async with system.session():
            mind = await system.mind_create("customer", goals=["Ship fast"])
            async with system._engine.begin() as conn:
                await conn.execute(
                    _sql("INSERT INTO block_tags (block_id, tag) VALUES (:i, :t)"),
                    {"i": mind.block_id, "t": "self/constitutional"})
            prediction = await system.mind_predict(
                mind.block_id, "Will pay 49/mo", verify_at="2026-06-30")
            result = await system.mind_outcome(
                prediction.decision_block_id, hit=True, reason="signed up")
        assert result.hit is True
        assert result.mind_confidence_delta != 0.0, (
            'a constitutional-tagged mind must still calibrate'
        )


class TestOutcomeNamesEverySkip:
    """Three different outcomes used to return the same shape: a typo'd id, a
    block still in the inbox, and a constitutional skip all gave
    blocks_updated=0 with nothing naming which. The inbox case silently ate
    real signal — remember() then outcome() before a dream() is ordinary
    whenever work resolves faster than the consolidation cycle.
    """

    async def test_inbox_block_is_reported_not_silently_dropped(
        self, system: MemorySystem
    ):
        async with system.session():
            pending = await system.remember("A thesis, just recorded.", cue="thesis")
            result = await system.outcome([pending.block_id], 0.9, source="same-day-fill")

        assert result.blocks_updated == 0
        assert result.skipped_for("pending_inbox") == [pending.block_id]
        assert "still in the inbox" in result.summary
        assert "dream()" in result.summary

    async def test_signal_lands_after_consolidation(self, system: MemorySystem):
        """The documented recovery has to actually work."""
        async with system.session():
            block = await system.remember("A thesis, just recorded.", cue="thesis")
            await system.consolidate()
            await system.consolidate()
            result = await system.outcome([block.block_id], 0.9, source="retry")
        assert result.blocks_updated == 1
        assert result.skipped == []

    async def test_unknown_id_is_distinguishable_from_pending(
        self, system: MemorySystem
    ):
        async with system.session():
            pending = await system.remember("A thesis.", cue="thesis")
            result = await system.outcome(
                [pending.block_id, "deadbeefdeadbeef"], 0.9, source="mixed")
        assert result.skipped_for("pending_inbox") == [pending.block_id]
        assert result.skipped_for("unmatched") == ["deadbeefdeadbeef"]

    async def test_archived_is_its_own_reason(self, system: MemorySystem):
        async with system.session():
            block = await system.remember("A fact to forget.", cue="doomed")
            await system.consolidate()
            await system.consolidate()
            await system.forget(block.block_id)
            result = await system.outcome([block.block_id], 0.9, source="late")
        assert result.skipped_for("archived") == [block.block_id]

    async def test_to_dict_carries_the_skips(self, system: MemorySystem):
        async with system.session():
            pending = await system.remember("A thesis.", cue="thesis")
            result = await system.outcome([pending.block_id], 0.9)
        assert result.to_dict()["skipped"] == [
            {"id": pending.block_id, "reason": "pending_inbox"}
        ]
