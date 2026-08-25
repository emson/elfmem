"""Attribution: which retrieved blocks actually made it into the answer.

The design constraint these tests protect is the asymmetry. Missing a real
use costs a reinforcement that was never guaranteed; crediting a block that
contributed nothing feeds the ranking a signal indistinguishable from real
evidence. Every ambiguous case here is pinned to the miss.
"""


import pytest

from elfmem import ElfmemConfig, MemorySystem
from elfmem.config import MemoryConfig
from elfmem.memory import ledger as _ledger
from elfmem.memory.attribution import (
    USE_THRESHOLD,
    attributed_ids,
    attribution_score,
    distinctive_terms,
)


class TestDistinctiveTerms:
    def test_drops_stopwords_and_short_tokens(self):
        terms = distinctive_terms("The cache is keyed on the frame name and top_k")
        assert "cache" in terms
        assert "frame" in terms
        assert "top_k" in terms
        for dropped in ("the", "is", "on", "and"):
            assert dropped not in terms

    def test_case_and_punctuation_insensitive(self):
        assert distinctive_terms("Reinforcement, decay.") == distinctive_terms(
            "reinforcement decay"
        )

    def test_all_stopwords_yields_nothing(self):
        assert distinctive_terms("that would have been about them") == frozenset()


class TestAttributionScore:
    def test_verbatim_reuse_scores_one(self):
        block = "Peer trust decays five percent per curate when inactive"
        assert attribution_score(block, f"As it happens, {block}.") == 1.0

    def test_unrelated_response_scores_zero(self):
        block = "Peer trust decays five percent per curate when inactive"
        assert attribution_score(block, "I fixed the cache key so top_k works.") == 0.0

    def test_partial_reuse_scores_between(self):
        block = "Reciprocal rank fusion merges vector and keyword rankings"
        score = attribution_score(block, "RRF merges the vector and keyword lists")
        assert 0.0 < score < 1.0

    def test_unscoreable_block_is_never_credited(self):
        """A block with no distinctive terms must score 0.0, not 1.0.

        Set containment of the empty set is vacuously total; returning 1.0
        here would credit the emptiest blocks in the corpus on every turn.
        """
        assert attribution_score("that would have been", "anything at all") == 0.0
        assert attribution_score("", "anything at all") == 0.0


class TestAttributedIds:
    def test_selects_above_threshold_only(self):
        blocks = {
            "used": "Reciprocal rank fusion merges vector and keyword rankings",
            "ignored": "Peer trust decays five percent per curate when inactive",
        }
        response = "Reciprocal rank fusion merges the vector and keyword rankings."
        assert attributed_ids(blocks, response) == ["used"]

    def test_orders_by_score_descending(self):
        blocks = {
            "weak": "alpha beta gamma delta epsilon zeta",
            "strong": "alpha beta gamma delta",
        }
        response = "alpha beta gamma delta"
        assert attributed_ids(blocks, response, threshold=0.5) == ["strong", "weak"]

    def test_no_blocks_used_returns_empty(self):
        blocks = {"a": "Peer trust decays five percent per curate"}
        assert attributed_ids(blocks, "Unrelated answer about widgets.") == []

    def test_threshold_is_inclusive(self):
        blocks = {"a": "alpha beta gamma delta"}
        # exactly half the terms reappear
        assert attributed_ids(blocks, "alpha beta", threshold=0.5) == ["a"]

    def test_default_threshold_is_the_calibrated_constant(self):
        assert 0.0 < USE_THRESHOLD < 1.0


@pytest.fixture
async def system(test_engine, mock_llm, mock_embedding, tmp_path) -> MemorySystem:
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=ElfmemConfig(memory=MemoryConfig(inbox_threshold=3)),
        project_root=str(tmp_path),
    )


async def _reinforcement(system: MemorySystem, block_id: str) -> int:
    from sqlalchemy import select

    from elfmem.db.models import blocks
    async with system._engine.begin() as conn:
        result = await conn.execute(
            select(blocks.c.reinforcement_count).where(blocks.c.id == block_id)
        )
        return result.scalar_one()


class TestRecordUse:
    async def test_reinforces_the_blocks_named(self, system):
        stored = await system.remember("Reciprocal rank fusion merges rankings")
        await system.consolidate()
        before = await _reinforcement(system, stored.block_id)

        result = await system.record_use([stored.block_id], source="test")

        assert result.blocks_reinforced == 1
        assert await _reinforcement(system, stored.block_id) == before + 1

    async def test_empty_is_a_no_op_not_an_error(self, system):
        """A turn that drew on nothing is normal, and informative."""
        result = await system.record_use([])
        assert result.blocks_reinforced == 0
        assert "no blocks" in result.summary

    async def test_writes_a_use_event_to_the_ledger(self, system, tmp_path):
        stored = await system.remember("Reciprocal rank fusion merges rankings")
        await system.consolidate()
        await system.record_use([stored.block_id], source="claude-code")

        replay = _ledger.replay(tmp_path / ".elfmem" / "ledger")
        assert stored.block_id in replay.blocks

    async def test_use_reinforces_on_replay_but_forms_no_co_retrieval(self, tmp_path):
        """Co-retrieval belongs to assembly; a use event must not re-count it.

        A `use` event names a subset of an assembly that already fired this
        pass. Counting it again would inflate the association of exactly the
        pairs that are already strongest.
        """
        ledger_dir = tmp_path / "ledger"
        ledger_dir.mkdir()
        _ledger.record_assembly(ledger_dir, ["a", "b"], active_hours=1.0)
        _ledger.record_use(ledger_dir, ["a", "b"], active_hours=2.0, source="test")

        replay = _ledger.replay(ledger_dir)

        assert replay.blocks["a"].reinforcement_count == 2  # asm + use
        assert replay.co_retrieval[("a", "b")] == 1  # asm only

    def test_record_use_without_a_ledger_is_silent(self):
        """A global instance has nowhere to put a ledger; that is not an error."""
        _ledger.record_use(None, ["a"], active_hours=1.0)


class TestCalibration:
    """The threshold is a measurement, so a regression in it must fail loudly."""

    def test_unrelated_corpus_rarely_clears_the_threshold(self):
        """Guards the false-positive rate the threshold was chosen for.

        Twenty blocks on unrelated subjects, scored against a long response
        about something else entirely. Over-crediting is the error that
        actually corrupts the ranking, so it is the one pinned here.
        """
        corpus = [
            f"Subject {i} concerns {topic} and its particular characteristics"
            for i, topic in enumerate([
                "photosynthesis", "harbour dredging", "viola tuning",
                "sourdough fermentation", "glacial moraines", "tax residency",
                "kiln firing", "orbital mechanics", "wool dyeing", "tide tables",
                "beekeeping", "typesetting", "crop rotation", "sail rigging",
                "bell founding", "ice climbing", "cheese ageing", "lens grinding",
                "well drilling", "reed weaving",
            ])
        ]
        response = (
            "I fixed the frame cache so the key includes top_k, and made the "
            "SELF frame queryless. Retrieval now drops the query before "
            "anything reads it, which means no embedding call, no graph "
            "expansion and no MMR reordering. Peer correspondence forfeits "
            "its guaranteed slot. The characteristics of each subject in "
            "memory are unchanged. " * 12
        )
        credited = sum(
            attribution_score(block, response) >= USE_THRESHOLD for block in corpus
        )
        assert credited <= 2, f"{credited}/20 unrelated blocks credited"


class TestActivityClock:
    """Reinforcement must never run on the 0.0 baseline of an unopened session.

    `_current_active_hours()` returns `_session_base_hours`, which is 0.0
    until begin_session() reads the real total from the database. Any
    operation that writes `last_reinforced_at` therefore has to open a
    session first, or it stamps the blocks it touched as maximally aged --
    rewarding a block by destroying its recency.
    """

    async def test_frame_does_not_zero_the_decay_clock(self, system):
        stored = await system.remember("Reciprocal rank fusion merges rankings")
        await system.consolidate()
        await system.end_session()

        await system.frame("attention", query="fusion")

        from sqlalchemy import select

        from elfmem.db.models import blocks
        async with system._engine.begin() as conn:
            rows = await conn.execute(
                select(blocks.c.last_reinforced_at).where(blocks.c.id == stored.block_id)
            )
            assert rows.scalar_one() > 0.0

    async def test_record_use_does_not_zero_the_decay_clock(self, system):
        stored = await system.remember("Reciprocal rank fusion merges rankings")
        await system.consolidate()
        await system.end_session()

        await system.record_use([stored.block_id], source="test")

        from sqlalchemy import select

        from elfmem.db.models import blocks
        async with system._engine.begin() as conn:
            rows = await conn.execute(
                select(blocks.c.last_reinforced_at).where(blocks.c.id == stored.block_id)
            )
            assert rows.scalar_one() > 0.0
