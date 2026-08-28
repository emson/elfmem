"""Tests for elfmem.memory.file_mutation — U-003 (file-native mutation primitives).

Pure filesystem operations, no DB/async fixtures needed — unlike U-002, these
never touch .elfmem/index.db.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elfmem.exceptions import ElfmemError
from elfmem.memory.file_mutation import (
    BlockNotFoundInFilesError,
    edit_block,
    find_block,
    forget_block,
    list_blocks,
    promote_block,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    root = tmp_path / ".elfmem" / "memory"
    _write(
        root / "notes" / "principles.md",
        "## Minimum force\n"
        "<!-- id: aaa111  tags: [self/value]  pinned: true  created: 2026-05-08 -->\n"
        "\n"
        "Apply the minimum force that solves the problem.\n"
        "\n"
        "## Second principle\n"
        "<!-- id: bbb222  tags: [self/value] -->\n"
        "\n"
        "Extend, don't duplicate.\n",
    )
    _write(
        root / "log" / "2026-08.md",
        "## Fresh note\n"
        "<!-- id: ccc333  tags: [attention] -->\n"
        "\n"
        "Something learned this session.\n",
    )
    return root


class TestEditPreservesIdUpdatesContent:
    def test_edit_preserves_id_updates_content(self, memory_dir):
        updated = edit_block(memory_dir, "aaa111", "A rewritten principle.")
        assert updated.id == "aaa111"
        assert updated.content == "A rewritten principle."

        found = find_block(memory_dir, "aaa111")
        assert found is not None
        _, block = found
        assert block.content == "A rewritten principle."
        assert block.tags == ["self/value"]  # frontmatter untouched
        assert block.pinned is True

    def test_edit_missing_block_raises(self, memory_dir):
        with pytest.raises(BlockNotFoundInFilesError) as exc_info:
            edit_block(memory_dir, "nonexistent", "content")
        assert exc_info.value.recovery


class TestForgetRemovesBlockIdempotent:
    def test_forget_removes_block_idempotent(self, memory_dir):
        assert forget_block(memory_dir, "bbb222") is True
        assert find_block(memory_dir, "bbb222") is None

        # Idempotent: forgetting again is not an error, just a no-op.
        assert forget_block(memory_dir, "bbb222") is False

        # The sibling block in the same file is untouched.
        found = find_block(memory_dir, "aaa111")
        assert found is not None

    def test_forget_never_found_returns_false(self, memory_dir):
        assert forget_block(memory_dir, "never-existed") is False


class TestListBlocksFiltersByTag:
    def test_list_blocks_filters_by_tag(self, memory_dir):
        all_blocks = list_blocks(memory_dir)
        assert {b.id for b in all_blocks} == {"aaa111", "bbb222", "ccc333"}

        attention_only = list_blocks(memory_dir, tag="attention")
        assert {b.id for b in attention_only} == {"ccc333"}

    def test_list_blocks_filters_by_subdir(self, memory_dir):
        notes_only = list_blocks(memory_dir, category_subdir="notes")
        assert {b.id for b in notes_only} == {"aaa111", "bbb222"}

        log_only = list_blocks(memory_dir, category_subdir="log")
        assert {b.id for b in log_only} == {"ccc333"}


class TestPromoteMovesBetweenLogAndNotes:
    def test_promote_moves_between_log_and_notes(self, memory_dir):
        promoted = promote_block(memory_dir, "ccc333", "promoted.md")
        assert promoted.id == "ccc333"

        # Gone from log/.
        log_blocks = list_blocks(memory_dir, category_subdir="log")
        assert log_blocks == []

        # Present in the new notes/ file, id and tags unchanged.
        notes_blocks = list_blocks(memory_dir, category_subdir="notes")
        assert "ccc333" in {b.id for b in notes_blocks}
        moved = next(b for b in notes_blocks if b.id == "ccc333")
        assert moved.tags == ["attention"]
        assert moved.content == "Something learned this session."

    def test_promote_appends_to_existing_notes_file(self, memory_dir):
        promote_block(memory_dir, "ccc333", "principles.md")
        notes_blocks = list_blocks(memory_dir, category_subdir="notes")
        assert {b.id for b in notes_blocks} == {"aaa111", "bbb222", "ccc333"}

    def test_promote_block_not_in_log_raises(self, memory_dir):
        with pytest.raises(ElfmemError) as exc_info:
            promote_block(memory_dir, "aaa111", "x.md")
        assert exc_info.value.recovery

    def test_promote_missing_block_raises(self, memory_dir):
        with pytest.raises(BlockNotFoundInFilesError):
            promote_block(memory_dir, "nonexistent", "x.md")
