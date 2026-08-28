"""Tests for elfmem.memory.blockfile — U-001 (block format + frontmatter parser).

USE WHEN criteria per docs/plans/v2_substrate/plan/build-plan.md U-001 Done when:
test_parses_wellformed_block_file, test_malformed_frontmatter_reported_not_skipped,
test_edit_preserves_id_and_history, test_duplicate_id_in_same_file_errors, plus
round-trip stability on: no frontmatter, malformed frontmatter, duplicate id,
pinned: true, and a constitution-mode file with no block headings.
"""

from __future__ import annotations

import pytest

from elfmem.memory.blockfile import (
    Block,
    BlockFileError,
    parse_blocks,
    read_raw,
    write_blocks,
)

WELLFORMED = """\
## Minimum force on commands
<!-- id: 8f3a2b1c  tags: [self/value, cli]  pinned: true  created: 2026-05-08 -->

Before adding a new top-level command, apply the test: does this extend an
existing verb? If yes, extend it.

## Second block
<!-- id: 1a2b3c4d  tags: [attention]  pinned: false  created: 2026-06-01 -->

Some other content here.
"""


class TestParsesWellformedBlockFile:
    def test_parses_wellformed_block_file(self):
        result = parse_blocks(WELLFORMED)
        assert result.errors == []
        assert len(result.blocks) == 2

        first = result.blocks[0]
        assert first.title == "Minimum force on commands"
        assert first.id == "8f3a2b1c"
        assert first.tags == ["self/value", "cli"]
        assert first.pinned is True
        assert first.created == "2026-05-08"
        assert "does this extend an" in first.content

        second = result.blocks[1]
        assert second.title == "Second block"
        assert second.id == "1a2b3c4d"
        assert second.pinned is False


class TestEmbeddedHeadingsAreContentNotBoundaries:
    """Regression test for a real bug found via a production-data migration
    dry run: a naive "every ## line is a boundary" parser mis-split 15 of
    140 blocks in a real corpus, because legitimate block content (Theory-
    of-Mind blocks especially) contains its own ## sub-headings."""

    def test_embedded_headings_stay_inside_the_block(self):
        text = """\
## Mind Model: ben-emson
<!-- id: mind001  tags: [mind] -->

# Mind Model: ben-emson

## Goals
- Build elfmem as the definitive adaptive memory library.
- Grow a builder community.

## Beliefs
- Infrastructure before products.

## A real second block
<!-- id: real002 -->

Genuinely separate content.
"""
        result = parse_blocks(text)
        assert result.errors == []
        assert len(result.blocks) == 2  # not 4 -- Goals/Beliefs are content

        first = result.blocks[0]
        assert first.id == "mind001"
        assert "## Goals" in first.content
        assert "## Beliefs" in first.content
        assert "Build elfmem as the definitive" in first.content

        second = result.blocks[1]
        assert second.id == "real002"
        assert second.content == "Genuinely separate content."

    def test_multiple_bare_blocks_still_split_correctly(self):
        # No frontmatter anywhere -- nothing to disambiguate against, so
        # every ## line splits, matching the original (pre-fix) behaviour
        # for genuinely hand-authored, metadata-free files.
        text = """\
## Bare one

Content one.

## Bare two

Content two.
"""
        result = parse_blocks(text)
        assert len(result.blocks) == 2
        assert result.blocks[0].content == "Content one."
        assert result.blocks[1].content == "Content two."


class TestMalformedFrontmatter:
    def test_malformed_frontmatter_reported_not_skipped(self):
        text = """\
## Broken block
<!-- id: badid  tags: [unterminated, list -->

This block has malformed frontmatter but should still be recoverable.

## Good block
<!-- id: goodid  tags: [ok] -->

This one is fine.
"""
        result = parse_blocks(text)
        assert len(result.errors) == 1
        assert result.errors[0].title == "Broken block"
        assert "unbalanced" in result.errors[0].reason

        # Not silently dropped: the block still appears with its content,
        # just without structured frontmatter fields.
        titles = [b.title for b in result.blocks]
        assert "Broken block" in titles
        assert "Good block" in titles
        broken = next(b for b in result.blocks if b.title == "Broken block")
        assert broken.id is None
        assert "malformed frontmatter" in broken.content


class TestEditPreservesIdAndHistory:
    def test_edit_preserves_id_and_history(self):
        original = parse_blocks(WELLFORMED)
        block = original.blocks[0]
        original_id = block.id

        block.content = "Completely different content after an edit."
        rewritten = write_blocks(original.blocks)
        reparsed = parse_blocks(rewritten)

        edited = reparsed.blocks[0]
        assert edited.id == original_id
        assert edited.content == "Completely different content after an edit."


class TestDuplicateId:
    def test_duplicate_id_in_same_file_errors(self):
        text = """\
## First
<!-- id: sameid  tags: [] -->

First content.

## Second
<!-- id: sameid  tags: [] -->

Second content — should not silently shadow the first.
"""
        with pytest.raises(BlockFileError) as exc_info:
            parse_blocks(text)
        assert "sameid" in str(exc_info.value)
        assert exc_info.value.recovery


class TestReadRaw:
    def test_read_raw_returns_content_unchanged(self):
        text = "# Identity\n\nSome constitution prose with a ## that is not a block heading marker on its own line context.\n"
        assert read_raw(text) == text

    def test_parse_blocks_on_heading_less_file_returns_empty(self):
        # A constitution-mode file with no `##` headings at all is inert
        # under block-mode parsing too — proves self.md-shaped content
        # never accidentally yields blocks even if misrouted.
        text = "# Identity\n\nJust prose. No block headings here.\n"
        result = parse_blocks(text)
        assert result.blocks == []
        assert result.errors == []


class TestRoundTripStability:
    """Parse -> write -> parse must reproduce identical field values."""

    def _assert_round_trip_stable(self, text: str) -> None:
        first = parse_blocks(text)
        rewritten = write_blocks(first.blocks)
        second = parse_blocks(rewritten)
        assert first.blocks == second.blocks

    def test_round_trip_no_frontmatter(self):
        text = "## Bare block\n\nNo frontmatter comment at all.\n"
        first = parse_blocks(text)
        assert first.blocks[0].id is None  # not yet assigned

        rewritten = write_blocks(first.blocks)
        # write_blocks assigns an id in place (Invariant 3, write-time seed)
        assert first.blocks[0].id is not None

        second = parse_blocks(rewritten)
        assert second.blocks[0].id == first.blocks[0].id
        assert second.blocks[0].content == "No frontmatter comment at all."

    def test_round_trip_malformed_frontmatter_block_survives(self):
        text = """\
## Broken block
<!-- id: badid  tags: [unterminated, list -->

Content survives even though frontmatter didn't parse.
"""
        first = parse_blocks(text)
        assert len(first.errors) == 1
        # The block itself round-trips fine once written back out (its
        # frontmatter is now well-formed, since write_blocks always emits
        # valid syntax) — this is expected: malformed input is recoverable
        # on write, not preserved as malformed.
        rewritten = write_blocks(first.blocks)
        second = parse_blocks(rewritten)
        assert second.errors == []
        assert second.blocks[0].content == first.blocks[0].content

    def test_round_trip_pinned_true(self):
        self._assert_round_trip_stable(WELLFORMED)

    def test_round_trip_constitution_mode_file_has_no_blocks(self):
        text = "# Identity\n\nNothing to see here.\n"
        first = parse_blocks(text)
        assert first.blocks == []
        # Nothing to round-trip — write_blocks([]) is the empty string.
        assert write_blocks(first.blocks) == ""

    def test_round_trip_extra_fields_preserved(self):
        text = """\
## Peer-received fact
<!-- id: abc123  tags: [knowledge]  source_peer: alv  msg_id: m_deadbeef -->

Something Alv told us.
"""
        first = parse_blocks(text)
        assert first.blocks[0].extra == {"source_peer": "alv", "msg_id": "m_deadbeef"}
        self._assert_round_trip_stable(text)


class TestBlockDefaults:
    def test_fresh_block_has_sensible_defaults(self):
        b = Block(title="New", content="content")
        assert b.id is None
        assert b.tags == []
        assert b.pinned is False
        assert b.created is None
        assert b.extra == {}
