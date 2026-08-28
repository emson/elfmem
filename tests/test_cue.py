"""Tests for the cue line: when a future agent should recall a block.

A cue is a lexical index of retrieval *situations*, which is what
vocabulary-mismatch queries fail on. These tests cover the write path
(`edit(cue=...)`), the round trip through the file substrate, and the fact
that a cue must not be treated as a content change.
"""

from __future__ import annotations

import pytest

from elfmem.db.queries import (
    get_block,
    get_blocks_missing_cue,
    insert_block,
    update_block_status,
)


class TestMissingCueListing:
    async def test_lists_only_active_blocks_without_a_cue(self, db_conn):
        await insert_block(
            db_conn, block_id="hascue01", content="Has one.",
            category="knowledge", source="agent", status="active", cue="when X",
        )
        await insert_block(
            db_conn, block_id="nocue001", content="Has none.",
            category="knowledge", source="agent", status="active",
        )
        await insert_block(
            db_conn, block_id="inbox001", content="Not yet consolidated.",
            category="knowledge", source="agent", status="inbox",
        )
        rows = await get_blocks_missing_cue(db_conn)
        assert [r["id"] for r in rows] == ["nocue001"]

    async def test_empty_string_counts_as_missing(self, db_conn):
        await insert_block(
            db_conn, block_id="blank001", content="Blank cue.",
            category="knowledge", source="agent", status="active", cue="",
        )
        rows = await get_blocks_missing_cue(db_conn)
        assert [r["id"] for r in rows] == ["blank001"]

    async def test_archived_blocks_are_not_listed(self, db_conn):
        await insert_block(
            db_conn, block_id="gone0001", content="Archived.",
            category="knowledge", source="agent", status="active",
        )
        await update_block_status(db_conn, "gone0001", "archived")
        assert await get_blocks_missing_cue(db_conn) == []


class TestCueUpdateSemantics:
    async def test_setting_a_cue_leaves_content_and_scoring_state_alone(
        self, db_conn
    ):
        """A cue says when to recall a block, not what it claims. It must not
        invalidate the embedding or push the block into the rescore queue the
        way a content edit does."""
        from elfmem.db.queries import update_block_cue, update_block_scoring

        await insert_block(
            db_conn, block_id="keep0001", content="Pricing service has a 4s p99.",
            category="knowledge", source="agent", status="active",
        )
        await update_block_scoring(
            db_conn, "keep0001", summary="A summary.",
            last_scored_at="2026-08-01T00:00:00+00:00",
        )
        before = await get_block(db_conn, "keep0001")

        await update_block_cue(
            db_conn, block_id="keep0001",
            cue="when adding a call inside a request handler",
        )
        after = await get_block(db_conn, "keep0001")

        assert after["cue"] == "when adding a call inside a request handler"
        assert after["content"] == before["content"]
        assert after["embedding"] == before["embedding"]
        assert after["summary"] == before["summary"]
        assert after["last_scored_at"] == before["last_scored_at"]

    async def test_cue_can_be_cleared(self, db_conn):
        from elfmem.db.queries import update_block_cue

        await insert_block(
            db_conn, block_id="clear001", content="Something.",
            category="knowledge", source="agent", status="active", cue="when X",
        )
        await update_block_cue(db_conn, block_id="clear001", cue=None)
        assert (await get_block(db_conn, "clear001"))["cue"] is None
        assert [r["id"] for r in await get_blocks_missing_cue(db_conn)] == ["clear001"]


class TestCueReachesRetrieval:
    def test_cue_joins_the_lexical_document(self):
        """A stored cue nothing searches is inert. This is the wiring that
        makes the backfill do anything at all.

        The corpus is deliberately not tiny: BM25Okapi's IDF collapses to
        zero for a term appearing in half a two-document corpus, so a
        minimal fixture would test the degenerate case rather than the
        behaviour."""
        from elfmem.memory.retrieval import _HAS_BM25, _stage_2b_bm25_search

        if not _HAS_BM25:
            pytest.skip("rank_bm25 not installed")

        filler = [
            {"id": f"pad{i}", "summary": None, "cue": None,
             "content": f"Unrelated note number {i} about widgets and shipping."}
            for i in range(6)
        ]
        target_by_content = {
            "id": "aaa", "summary": None, "cue": None,
            "content": "Swallow exceptions silently inside the request path.",
        }
        target_by_cue = {
            "id": "bbb", "summary": None,
            "cue": "when deciding how to do error handling in a request path",
            "content": "Unrelated content about widget dimensions.",
        }
        candidates = [*filler, target_by_content, target_by_cue]

        ranked = _stage_2b_bm25_search(candidates, "error handling", 3)
        assert ranked, "BM25 returned nothing for a query with real term overlap"
        assert ranked[0][0]["id"] == "bbb"

        # Strip the cue and the same query no longer finds it.
        stripped = [{**c, "cue": None} for c in candidates]
        ranked_without = _stage_2b_bm25_search(stripped, "error handling", 3)
        assert not ranked_without or ranked_without[0][0]["id"] != "bbb"
