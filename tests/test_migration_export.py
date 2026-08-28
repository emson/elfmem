"""Tests for elfmem.migration.export — U-004 (migration export, Phases 0-2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from elfmem.db.queries import add_tags, insert_block
from elfmem.memory.blockfile import Link, parse_blocks
from elfmem.migration.export import export_to_markdown


class TestExportActiveBlockToNotes:
    async def test_active_block_lands_in_notes(self, db_conn, tmp_path: Path):
        await insert_block(
            db_conn,
            block_id="abc123",
            content="Apply the minimum force that solves the problem.",
            category="knowledge",
            source="api",
            status="active",
            confidence=0.8,
        )
        await add_tags(db_conn, "abc123", ["self/value", "cli"])

        memory_dir = tmp_path / ".elfmem" / "memory"
        result = await export_to_markdown(db_conn, memory_dir)

        assert result.blocks_exported == 1
        dest = memory_dir / "notes" / "knowledge.md"
        assert dest in result.files_written
        parsed = parse_blocks(dest.read_text(encoding="utf-8"))
        assert len(parsed.blocks) == 1
        block = parsed.blocks[0]
        assert block.id == "abc123"
        assert block.content == "Apply the minimum force that solves the problem."
        assert set(block.tags) == {"self/value", "cli"}
        assert block.pinned is False
        # Derived state is deliberately absent from frontmatter: confidence,
        # alpha and beta are history, and history lives in the ledger where it
        # keeps its full event trail instead of a hand-editable aggregate.
        assert "confidence" not in block.extra
        assert "alpha" not in block.extra
        assert "beta" not in block.extra
        # Declared block-format-v2 fields are present.
        # self/value alone is 'project': 'identity' is reserved for
        # self/constitutional, or the class covers two thirds of the corpus.
        assert block.volatility == "project"


class TestExportInboxBlockToLog:
    async def test_inbox_block_lands_in_log(self, db_conn, tmp_path: Path):
        await insert_block(
            db_conn,
            block_id="def456",
            content="Not yet reviewed.",
            category="observation",
            source="api",
            status="inbox",
        )
        memory_dir = tmp_path / ".elfmem" / "memory"
        result = await export_to_markdown(db_conn, memory_dir)

        assert result.blocks_exported == 1
        dest = memory_dir / "log" / "observation.md"
        assert dest.exists()
        assert (memory_dir / "notes" / "observation.md").exists() is False


class TestExportArchivedBlockToArchive:
    async def test_archived_block_lands_in_archive_recoverable(
        self, db_conn, tmp_path: Path
    ):
        await insert_block(
            db_conn,
            block_id="ghi789",
            content="Superseded content.",
            category="knowledge",
            source="api",
            status="archived",
        )
        memory_dir = tmp_path / ".elfmem" / "memory"
        result = await export_to_markdown(db_conn, memory_dir)

        assert result.blocks_exported == 1
        dest = memory_dir / "archive" / "knowledge.md"
        assert dest.exists()


class TestConstitutionalTagBecomesPinned:
    async def test_self_constitutional_tag_becomes_pinned_additively(
        self, db_conn, tmp_path: Path
    ):
        await insert_block(
            db_conn,
            block_id="const1",
            content="Core identity statement.",
            category="knowledge",
            source="api",
            status="active",
        )
        await add_tags(db_conn, "const1", ["self/constitutional", "self/role/writer"])

        memory_dir = tmp_path / ".elfmem" / "memory"
        await export_to_markdown(db_conn, memory_dir)

        parsed = parse_blocks(
            (memory_dir / "notes" / "knowledge.md").read_text(encoding="utf-8")
        )
        block = parsed.blocks[0]
        assert block.pinned is True
        # Additive, not a replacement -- frame('self', ...) still matches
        # on the tag pattern until that retrieval path is itself migrated.
        assert "self/constitutional" in block.tags


class TestExportEmptyDatabase:
    async def test_export_with_no_blocks_writes_nothing(self, db_conn, tmp_path: Path):
        memory_dir = tmp_path / ".elfmem" / "memory"
        result = await export_to_markdown(db_conn, memory_dir)
        assert result.blocks_exported == 0
        assert result.files_written == []


class TestExportGroupsByCategory:
    async def test_multiple_categories_get_separate_files(
        self, db_conn, tmp_path: Path
    ):
        await insert_block(
            db_conn,
            block_id="k1",
            content="A knowledge block.",
            category="knowledge",
            source="api",
            status="active",
        )
        await insert_block(
            db_conn,
            block_id="o1",
            content="An observation block.",
            category="observation",
            source="api",
            status="active",
        )
        memory_dir = tmp_path / ".elfmem" / "memory"
        result = await export_to_markdown(db_conn, memory_dir)

        assert result.blocks_exported == 2
        assert (memory_dir / "notes" / "knowledge.md").exists()
        assert (memory_dir / "notes" / "observation.md").exists()


class TestFormatV2Export:
    """Block format v2: declared fields go to the file, derived state goes to
    the ledger. The two must not overlap."""

    async def test_ledger_seed_reproduces_alpha_beta_exactly(
        self, db_conn, tmp_path: Path
    ):
        from elfmem.memory.ledger import replay

        await insert_block(
            db_conn, block_id="seed001", content="Evidence accumulated here.",
            category="knowledge", source="agent", status="active",
            success_count=4.1, failure_count=0.9,
            reinforcement_count=7, last_reinforced_at=3.25,
            created_at="2026-05-08T09:14:22+00:00",
        )
        ledger_dir = tmp_path / ".elfmem" / "ledger"
        result = await export_to_markdown(
            db_conn, tmp_path / ".elfmem" / "memory", ledger_dir=ledger_dir
        )
        assert result.blocks_seeded == 1

        state = replay(ledger_dir).blocks["seed001"]
        assert state.alpha == pytest.approx(4.1)
        assert state.beta == pytest.approx(0.9)
        assert state.reinforcement_count == 7
        assert state.last_reinforced_at == pytest.approx(3.25)
        assert state.created_at == "2026-05-08T09:14:22+00:00"

    async def test_block_with_no_evidence_still_seeds_neutral_priors(
        self, db_conn, tmp_path: Path
    ):
        from elfmem.memory.ledger import replay

        await insert_block(
            db_conn, block_id="seed002", content="No outcomes ever recorded.",
            category="knowledge", source="agent", status="active",
            success_count=0.5, failure_count=0.5,
        )
        ledger_dir = tmp_path / ".elfmem" / "ledger"
        await export_to_markdown(
            db_conn, tmp_path / ".elfmem" / "memory", ledger_dir=ledger_dir
        )
        state = replay(ledger_dir).blocks["seed002"]
        assert state.alpha == pytest.approx(0.5)
        assert state.beta == pytest.approx(0.5)

    async def test_learned_edges_are_not_written_into_files(
        self, db_conn, tmp_path: Path
    ):
        """Similarity and co-retrieval edges are computed, not authored.
        Writing them into hand-editable files invites editing a number the
        system owns."""
        from elfmem.db.queries import insert_edge

        for bid in ("aaa11111", "bbb22222"):
            await insert_block(
                db_conn, block_id=bid, content=f"Content {bid}.",
                category="knowledge", source="agent", status="active",
            )
        await insert_edge(
            db_conn, from_id="aaa11111", to_id="bbb22222", weight=0.8,
            relation_type="similar", origin="similarity",
        )
        memory_dir = tmp_path / ".elfmem" / "memory"
        await export_to_markdown(db_conn, memory_dir)
        parsed = parse_blocks(
            (memory_dir / "notes" / "knowledge.md").read_text(encoding="utf-8")
        )
        assert all(b.links == [] for b in parsed.blocks)

    async def test_declared_edge_round_trips_with_its_direction(
        self, db_conn, tmp_path: Path
    ):
        from elfmem.db.queries import insert_edge

        for bid in ("zzz99999", "mmm55555"):
            await insert_block(
                db_conn, block_id=bid, content=f"Content {bid}.",
                category="knowledge", source="agent", status="active",
            )
        # Canonical ordering puts mmm first, but zzz is what declared it.
        await insert_edge(
            db_conn, from_id="mmm55555", to_id="zzz99999", weight=0.9,
            relation_type="refines", origin="agent", declared_by="zzz99999",
        )
        memory_dir = tmp_path / ".elfmem" / "memory"
        await export_to_markdown(db_conn, memory_dir)
        parsed = parse_blocks(
            (memory_dir / "notes" / "knowledge.md").read_text(encoding="utf-8")
        )
        declaring = next(b for b in parsed.blocks if b.id == "zzz99999")
        assert declaring.links == [Link(relation="refines", target="mmm55555")]
        other = next(b for b in parsed.blocks if b.id == "mmm55555")
        assert other.links == []
