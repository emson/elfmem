"""Tests for the file-native peer landing path — U-012.

Covers the resolved peer-bundle defect (model.md D-002): append-only log
landing plus rebuild-time, msg_id-deduplicated reconciliation. Does not
touch the existing DB-native import_bundle()/_build_bundle() (unchanged,
still live — see build-plan.md's U-012 revision note).
"""

from __future__ import annotations

from pathlib import Path

from elfmem.db.queries import (
    get_active_blocks,
    get_inbox_blocks,
    insert_peer,
    update_peer_trust,
)
from elfmem.memory.blockfile import parse_blocks
from elfmem.operations.peer import fold_peer_log, land_peer_log_entry


class TestPeerLogEntryAppendedNotMerged:
    async def test_peer_log_entry_appended_not_merged(self, tmp_path: Path):
        memory_dir = tmp_path / ".elfmem" / "memory"
        await land_peer_log_entry(
            memory_dir,
            content="Fact one from Alv.",
            tags=["knowledge"],
            from_peer="alv",
            msg_id="m_aaa111",
            remote_alpha=0.9,
            remote_beta=0.1,
        )
        await land_peer_log_entry(
            memory_dir,
            content="Fact two from Alv.",
            tags=["knowledge"],
            from_peer="alv",
            msg_id="m_bbb222",
        )

        # One file per message -- never a shared file to read-modify-write,
        # and this sidesteps U-001's per-file duplicate-id invariant when
        # two messages share content (see the class below).
        log_files = sorted((memory_dir / "log" / "peer").glob("*.md"))
        assert len(log_files) == 2
        all_blocks = [
            b
            for f in log_files
            for b in parse_blocks(f.read_text(encoding="utf-8")).blocks
        ]
        assert len(all_blocks) == 2  # both entries present, neither merged
        assert {b.content for b in all_blocks} == {
            "Fact one from Alv.",
            "Fact two from Alv.",
        }
        first = next(b for b in all_blocks if b.content == "Fact one from Alv.")
        assert first.extra["msg_id"] == "m_aaa111"
        assert first.extra["source_peer"] == "alv"


class TestResentMsgIdDeduplicated:
    async def test_resent_msg_id_deduplicated_before_merge(
        self, db_conn, tmp_path: Path, mock_embedding
    ):
        memory_dir = tmp_path / ".elfmem" / "memory"
        await insert_peer(db_conn, did="alv", name="Alv")
        await update_peer_trust(db_conn, "alv", 1.0)

        # The SAME message, landed twice (simulating a re-sent envelope).
        for _ in range(2):
            await land_peer_log_entry(
                memory_dir,
                content="A fact worth 0.9 confidence.",
                tags=["knowledge"],
                from_peer="alv",
                msg_id="m_same",
                remote_alpha=0.9,
                remote_beta=0.1,
            )

        written = await fold_peer_log(
            db_conn, mock_embedding, mock_embedding.model_name, memory_dir=memory_dir
        )
        assert written == 1

        blocks = await get_inbox_blocks(db_conn)
        assert len(blocks) == 1
        block = blocks[0]
        # Merged exactly ONCE: alpha should reflect one merge_peer_evidence
        # application from the Jeffreys prior (0.5, 0.5), not two.
        expected_confidence = (0.5 + 0.9 * 1.0) / (
            (0.5 + 0.9 * 1.0) + (0.5 + 0.1 * 1.0)
        )
        assert abs(block["confidence"] - expected_confidence) < 0.0001


class TestDistinctMessagesSameContentBothCounted:
    async def test_distinct_messages_same_content_both_counted(
        self, db_conn, tmp_path: Path, mock_embedding
    ):
        memory_dir = tmp_path / ".elfmem" / "memory"
        await insert_peer(db_conn, did="alv", name="Alv")
        await update_peer_trust(db_conn, "alv", 1.0)

        # Two DIFFERENT messages (distinct msg_id) that happen to have
        # identical content -- the exact ADR 0005 gap. Both must count.
        for msg_id in ("m_first", "m_second"):
            await land_peer_log_entry(
                memory_dir,
                content="Repeated content, sent twice for real reasons.",
                tags=["knowledge"],
                from_peer="alv",
                msg_id=msg_id,
                remote_alpha=0.7,
                remote_beta=0.1,
            )

        written = await fold_peer_log(
            db_conn, mock_embedding, mock_embedding.model_name, memory_dir=memory_dir
        )
        assert written == 1  # one block id (content-hash-derived), two contributions folded in

        blocks = await get_inbox_blocks(db_conn)
        block = blocks[0]
        # Two merge_peer_evidence applications, not one -- confidence should
        # be higher than a single 0.7/0.1 application would produce, since
        # evidence accumulated twice.
        alpha1, beta1 = 0.5 + 0.7, 0.5 + 0.1
        alpha2, beta2 = alpha1 + 0.7, beta1 + 0.1
        expected_confidence = alpha2 / (alpha2 + beta2)
        assert abs(block["confidence"] - expected_confidence) < 0.0001


class TestFoldProducesCorrectAlphaBeta:
    async def test_fold_produces_correct_alpha_beta(
        self, db_conn, tmp_path: Path, mock_embedding
    ):
        memory_dir = tmp_path / ".elfmem" / "memory"
        await insert_peer(db_conn, did="alv", name="Alv")
        await update_peer_trust(db_conn, "alv", 0.5)  # partial trust

        await land_peer_log_entry(
            memory_dir,
            content="Trust-scaled fact.",
            tags=[],
            from_peer="alv",
            msg_id="m_trust",
            remote_alpha=1.0,
            remote_beta=0.0,
        )

        await fold_peer_log(
            db_conn, mock_embedding, mock_embedding.model_name, memory_dir=memory_dir
        )
        blocks = await get_inbox_blocks(db_conn)
        block = blocks[0]
        # trust=0.5 halves the remote evidence's weight before merging.
        expected_alpha = 0.5 + 1.0 * 0.5
        expected_beta = 0.5 + 0.0 * 0.5
        assert abs(block["success_count"] - expected_alpha) < 0.0001
        assert abs(block["failure_count"] - expected_beta) < 0.0001

    async def test_fold_with_no_log_dir_returns_zero(
        self, db_conn, tmp_path: Path, mock_embedding
    ):
        memory_dir = tmp_path / ".elfmem" / "memory"
        written = await fold_peer_log(
            db_conn, mock_embedding, mock_embedding.model_name, memory_dir=memory_dir
        )
        assert written == 0

    async def test_folded_blocks_land_as_inbox_not_active(
        self, db_conn, tmp_path: Path, mock_embedding
    ):
        memory_dir = tmp_path / ".elfmem" / "memory"
        await insert_peer(db_conn, did="alv", name="Alv")
        await update_peer_trust(db_conn, "alv", 1.0)
        await land_peer_log_entry(
            memory_dir,
            content="Unreviewed peer fact.",
            tags=[],
            from_peer="alv",
            msg_id="m_x",
        )
        await fold_peer_log(
            db_conn, mock_embedding, mock_embedding.model_name, memory_dir=memory_dir
        )
        assert len(await get_inbox_blocks(db_conn)) == 1
        assert len(await get_active_blocks(db_conn)) == 0
