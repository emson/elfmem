"""Tests for elfmem.migration.export — U-004 (migration export, Phases 0-2)."""

from __future__ import annotations

from pathlib import Path

from elfmem.db.queries import add_tags, insert_block
from elfmem.memory.blockfile import parse_blocks
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
        assert "confidence" in block.extra


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
