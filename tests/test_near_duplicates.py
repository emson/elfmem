"""Near-duplicate pairs are recorded, not resolved by deletion.

Automatic supersession archived the existing block whenever an incoming one
matched it above 0.90 cosine. On the maintainer's instance that destroyed 41
of the 187 blocks ever created (21.9%), six of them constitutional, with no
audit row and no undo -- and it destroyed the evidence needed to evaluate it:
0 of 42 superseded rows recorded which block replaced them.

Keeping both costs about 11% more corpus tokens on that same corpus. These
tests pin the properties that make the trade sound: nothing is destroyed, the
pair is recorded, recall does not show both halves at once, and both halves
stay reachable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text as sa_text

from elfmem.context.contradiction import suppress_contradictions
from elfmem.db.queries import (
    get_active_blocks,
    get_unresolved_pairs,
    insert_block,
    insert_contradiction,
)
from elfmem.operations.consolidate import _cue_similarity
from elfmem.types import ScoredBlock


class TestCueSimilarity:
    def test_missing_cue_on_either_side_yields_none(self):
        """Absent evidence is reported as absent, never as zero -- a pair with
        no cues is unknown, not known-to-differ."""
        assert _cue_similarity(None, "when choosing a sync strategy") is None
        assert _cue_similarity("when choosing a sync strategy", None) is None
        assert _cue_similarity(None, None) is None

    def test_identical_cues_score_one(self):
        cue = "when choosing a sync strategy"
        assert _cue_similarity(cue, cue) == pytest.approx(1.0)

    def test_disjoint_cues_score_zero(self):
        assert _cue_similarity("picking storage", "deploy rollback failed") == 0.0

    def test_partial_overlap_is_between(self):
        score = _cue_similarity(
            "when choosing a sync strategy", "when choosing a merge strategy"
        )
        assert 0.0 < score < 1.0


class TestPairRecording:
    async def test_pair_is_recorded_with_kind_and_cue_evidence(self, db_conn):
        for bid in ("aaa11111", "bbb22222"):
            await insert_block(
                db_conn, block_id=bid, content=f"Content {bid}.",
                category="knowledge", source="agent", status="active",
            )
        await insert_contradiction(
            db_conn, block_a_id="aaa11111", block_b_id="bbb22222",
            score=0.92, kind="near_duplicate", cue_similarity=0.25,
        )
        pairs = await get_unresolved_pairs(db_conn, kind="near_duplicate")
        assert len(pairs) == 1
        assert pairs[0]["score"] == pytest.approx(0.92)
        assert pairs[0]["cue_similarity"] == pytest.approx(0.25)

    async def test_recording_the_same_pair_twice_is_a_no_op(self, db_conn):
        """A later consolidation re-encountering the pair must not raise, and
        must not overwrite a review decision already recorded against it."""
        for bid in ("aaa11111", "bbb22222"):
            await insert_block(
                db_conn, block_id=bid, content=f"Content {bid}.",
                category="knowledge", source="agent", status="active",
            )
        await insert_contradiction(
            db_conn, block_a_id="aaa11111", block_b_id="bbb22222",
            score=0.92, kind="near_duplicate",
        )
        await db_conn.execute(sa_text("UPDATE contradictions SET resolved = 1"))
        await insert_contradiction(
            db_conn, block_a_id="aaa11111", block_b_id="bbb22222",
            score=0.99, kind="near_duplicate",
        )
        rows = (await db_conn.execute(
            sa_text("SELECT score, resolved FROM contradictions")
        )).mappings().all()
        assert len(rows) == 1
        assert rows[0]["resolved"] == 1
        assert rows[0]["score"] == pytest.approx(0.92)

    async def test_existing_rows_default_to_contradiction_kind(self, db_conn):
        """The column default must not silently reclassify the 14 real
        contradictions already on the maintainer's instance."""
        for bid in ("aaa11111", "bbb22222"):
            await insert_block(
                db_conn, block_id=bid, content=f"Content {bid}.",
                category="knowledge", source="agent", status="active",
            )
        await insert_contradiction(
            db_conn, block_a_id="aaa11111", block_b_id="bbb22222", score=0.85,
        )
        assert (await get_unresolved_pairs(db_conn, kind="near_duplicate")) == []
        assert len(await get_unresolved_pairs(db_conn, kind="contradiction")) == 1


class TestBothHalvesStayReachable:
    async def test_suppression_hides_one_half_but_archives_neither(self, db_conn):
        """Suppression is the reversible substitute for deletion: one half is
        kept out of a single frame, both remain active and retrievable."""
        for bid in ("aaa11111", "bbb22222"):
            await insert_block(
                db_conn, block_id=bid, content=f"Content {bid}.",
                category="knowledge", source="agent", status="active",
                confidence=0.9 if bid == "aaa11111" else 0.4,
            )
        await insert_contradiction(
            db_conn, block_a_id="aaa11111", block_b_id="bbb22222",
            score=0.92, kind="near_duplicate",
        )
        def _scored(bid: str, confidence: float) -> ScoredBlock:
            return ScoredBlock(
                id=bid, content=f"Content {bid}.", tags=[], similarity=0.5,
                confidence=confidence, recency=0.5, centrality=0.1,
                reinforcement=0.0, score=0.5,
            )

        candidates = [_scored("aaa11111", 0.9), _scored("bbb22222", 0.4)]
        kept = await suppress_contradictions(db_conn, candidates)
        assert [c.id for c in kept] == ["aaa11111"]

        # Neither was archived -- the lower-confidence half is still there.
        active = {b["id"] for b in await get_active_blocks(db_conn)}
        assert active == {"aaa11111", "bbb22222"}
