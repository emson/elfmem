"""Tests for elfmem.memory.index_rebuild — U-002 (`elfmem index` rebuild, L1 -> L2).

Two of the four contracted test names (test_self_md_appears_in_self_frame,
test_self_md_absent_from_ls_listing) are implemented against RebuildResult
and the blocks table directly rather than through frame()/ls() — neither
exists yet (frame() integration and ls() belong to units not yet built; see
results/U-002.md "Missing context"). What each name promises — self.md is
available for the self frame, and self.md never enters the block table — is
still exactly what's asserted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elfmem.db.queries import get_active_blocks, get_tags
from elfmem.memory.index_rebuild import MemoryDirNotFoundError, rebuild_index


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    root = tmp_path / ".elfmem" / "memory"
    _write(
        root / "self.md",
        "# Identity\n\nMinimum force. Apply the smallest change that solves it.\n",
    )
    _write(
        root / "notes" / "principles.md",
        "## Minimum force on commands\n"
        "<!-- id: 8f3a2b1c  tags: [self/value, cli]  pinned: true  created: 2026-05-08 -->\n"
        "\n"
        "Before adding a new top-level command, apply the test: does this "
        "extend an existing verb?\n",
    )
    _write(
        root / "log" / "2026-08.md",
        "## Fresh observation\n"
        "<!-- id: 1a2b3c4d  tags: [attention] -->\n"
        "\n"
        "Something learned this session, not yet reviewed.\n",
    )
    return root


class TestRebuildMatchesReferenceFixture:
    async def test_rebuild_matches_reference_fixture(
        self, db_conn, memory_dir, mock_embedding, mock_llm
    ):
        result = await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )

        assert result.blocks_written == 2
        assert result.parse_errors == []

        active = await get_active_blocks(db_conn)
        by_id = {b["id"]: b for b in active}
        assert "8f3a2b1c" in by_id
        assert by_id["8f3a2b1c"]["status"] == "active"
        assert by_id["8f3a2b1c"]["embedding"] is not None
        assert await get_tags(db_conn, "8f3a2b1c") == ["cli", "self/value"]

        # log/ lands as inbox, not active -- get_active_blocks won't see it.
        assert "1a2b3c4d" not in by_id

        # Zero LLM calls -- embeddings are a distinct, expected cost.
        assert mock_llm.process_block_calls == 0
        assert mock_llm.propose_amendment_calls == 0
        assert mock_embedding.embed_calls > 0


class TestMissingMemoryDir:
    async def test_missing_memory_dir_fails_loudly(
        self, db_conn, tmp_path, mock_embedding
    ):
        missing = tmp_path / "does-not-exist"
        with pytest.raises(MemoryDirNotFoundError) as exc_info:
            await rebuild_index(
                db_conn, missing, mock_embedding, mock_embedding.model_name
            )
        assert exc_info.value.recovery


class TestSelfMdNeverEntersBlockTable:
    async def test_self_md_appears_in_self_frame(
        self, db_conn, memory_dir, mock_embedding
    ):
        # "Appears in the self frame" -- frame() wiring doesn't exist yet
        # (belongs to a later unit), but the content this unit is
        # responsible for making available is present and correct.
        result = await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        assert result.self_content is not None
        assert "Minimum force" in result.self_content

    async def test_self_md_absent_from_ls_listing(
        self, db_conn, memory_dir, mock_embedding
    ):
        # "Absent from ls()" -- ls() doesn't exist yet, so asserted directly
        # against the table it would list from: self.md's content must
        # never appear as a block row (Invariant 2).
        await rebuild_index(
            db_conn, memory_dir, mock_embedding, mock_embedding.model_name
        )
        active = await get_active_blocks(db_conn)
        contents = [b["content"] for b in active]
        assert not any("Minimum force" in c for c in contents)


class TestNoSelfMd:
    async def test_missing_self_md_is_not_an_error(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "x.md",
            "## Only block\n<!-- id: onlyone -->\n\nJust this.\n",
        )
        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name
        )
        assert result.self_content is None
        assert result.blocks_written == 1


class TestMalformedFrontmatterSurfaced:
    async def test_malformed_frontmatter_collected_not_silent(
        self, db_conn, tmp_path, mock_embedding
    ):
        root = tmp_path / ".elfmem" / "memory"
        _write(
            root / "notes" / "broken.md",
            "## Broken\n<!-- id: bad  tags: [unterminated -->\n\nStill has content.\n",
        )
        result = await rebuild_index(
            db_conn, root, mock_embedding, mock_embedding.model_name
        )
        assert result.blocks_written == 1  # still written, just flagged
        assert len(result.parse_errors) == 1
        path, err = result.parse_errors[0]
        assert path.name == "broken.md"
        assert err.title == "Broken"


class TestExtensionPoint:
    async def test_additional_fold_steps_contribute_to_count(
        self, db_conn, memory_dir, mock_embedding
    ):
        async def fake_peer_fold(conn, embedding_service, embedding_model):
            del conn, embedding_service, embedding_model
            return 3

        result = await rebuild_index(
            db_conn,
            memory_dir,
            mock_embedding,
            mock_embedding.model_name,
            additional_fold_steps=[fake_peer_fold],
        )
        assert result.blocks_written == 2 + 3
