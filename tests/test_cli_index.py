"""Tests for `elfmem export --to-markdown` / `elfmem index check|rebuild|parity`
— the v2 substrate CLI wiring (docs/plans/v2_substrate).

These commands were library-only (blockfile, index_rebuild, migration/export,
migration/parity) before this pass: built, tested, verified, but unreachable
from the terminal. This file tests the CLI layer that wires them up.

Async helpers are exercised directly for the heavy-lifting cases (mirrors
tests/test_migrate_embeddings.py's pattern: real file-backed engines, no
LLM cost) and a handful of CliRunner smoke tests confirm the terminal-facing
wiring itself, each isolated into a throwaway "project" via
`monkeypatch.chdir` + a `.git` marker so path auto-discovery never reaches
this repo's own real .elfmem/config.yaml.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from elfmem.adapters.mock import MockEmbeddingService
from elfmem.cli import (
    _export_markdown_async,
    _fresh_index_engine,
    _index_check,
    _index_parity_async,
    _index_rebuild_async,
    app,
)
from elfmem.config import ElfmemConfig
from elfmem.db.engine import create_engine
from elfmem.db.queries import (
    add_tags,
    get_active_blocks,
    get_inbox_blocks,
    insert_block,
    update_block_scoring,
)
from elfmem.exceptions import ElfmemError

runner = CliRunner()


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def file_db_path():
    """A file-backed test DB — these commands work on paths, not in-memory
    engines, so a real file is unavoidable (matches test_migrate_embeddings.py)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    engine = await create_engine(path)
    async with engine.begin() as conn:
        from elfmem.db.models import metadata

        await conn.run_sync(metadata.create_all)
    await engine.dispose()
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def cfg() -> ElfmemConfig:
    return ElfmemConfig.model_validate({"embeddings": {"model": "mock", "dimensions": 64}})


@pytest.fixture
def patched_factory(monkeypatch):
    """Redirect make_embedding_adapter to one deterministic mock instance
    shared across every call in a test — required for parity checks, where
    the "before" and "after" embeddings must come from the same model."""
    instance = MockEmbeddingService(dimensions=64)

    def factory(cfg, counter):
        return instance

    monkeypatch.setattr("elfmem.adapters.factory.make_embedding_adapter", factory)
    return instance


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── _index_check ──────────────────────────────────────────────────────────────


class TestIndexCheck:
    def test_reports_block_count_and_no_errors_for_wellformed_files(self, tmp_path):
        _write(
            tmp_path / "notes" / "principles.md",
            "## A block\n<!-- id: abc123 -->\n\nSome content.\n",
        )
        blocks, errors = _index_check(tmp_path)
        assert blocks == 1
        assert errors == []

    def test_reports_frontmatter_errors_without_dropping_the_block(self, tmp_path):
        _write(
            tmp_path / "log" / "2026-08.md",
            "## Broken\n<!-- id: bad  tags: [unterminated -->\n\nStill here.\n",
        )
        blocks, errors = _index_check(tmp_path)
        assert blocks == 1
        assert len(errors) == 1
        assert errors[0][1].title == "Broken"

    def test_empty_memory_dir_reports_zero(self, tmp_path):
        blocks, errors = _index_check(tmp_path)
        assert blocks == 0
        assert errors == []


# ── _export_markdown_async ──────────────────────────────────────────────────


class TestExportMarkdownAsync:
    async def test_exports_active_and_inbox_blocks_to_separate_files(
        self, file_db_path, tmp_path,
    ):
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            await insert_block(
                conn, block_id="a1", content="An active fact.",
                category="knowledge", source="test", status="active",
            )
            await add_tags(conn, "a1", ["self/value"])
            await insert_block(
                conn, block_id="i1", content="An unreviewed fact.",
                category="knowledge", source="test", status="inbox",
            )
        await engine.dispose()

        memory_dir = tmp_path / "memory"
        result = await _export_markdown_async(file_db_path, memory_dir)

        assert result.blocks_exported == 2
        assert (memory_dir / "notes" / "knowledge.md").exists()
        assert (memory_dir / "log" / "knowledge.md").exists()

        from elfmem.memory.blockfile import parse_blocks

        active_parsed = parse_blocks((memory_dir / "notes" / "knowledge.md").read_text())
        assert active_parsed.blocks[0].id == "a1"
        assert active_parsed.blocks[0].tags == ["self/value"]

    async def test_export_is_read_only_on_the_database(self, file_db_path, tmp_path):
        """Exporting must not mutate the source DB — running it twice must
        yield the same block set both times."""
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            await insert_block(
                conn, block_id="a1", content="Stable fact.",
                category="knowledge", source="test", status="active",
            )
        await engine.dispose()

        first = await _export_markdown_async(file_db_path, tmp_path / "run1")
        second = await _export_markdown_async(file_db_path, tmp_path / "run2")
        assert first.blocks_exported == second.blocks_exported == 1


# ── _fresh_index_engine ──────────────────────────────────────────────────────


class TestFreshIndexEngine:
    async def test_refuses_nonempty_target_without_force(self, file_db_path):
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            from elfmem.db.models import metadata

            await conn.run_sync(metadata.create_all)
            await insert_block(
                conn, block_id="a1", content="Already here.",
                category="knowledge", source="test", status="active",
            )
        await engine.dispose()

        with pytest.raises(ElfmemError) as exc_info:
            await _fresh_index_engine(file_db_path, force=False)
        assert exc_info.value.recovery

    async def test_force_wipes_existing_blocks(self, file_db_path):
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            from elfmem.db.models import metadata

            await conn.run_sync(metadata.create_all)
            await insert_block(
                conn, block_id="a1", content="Will be wiped.",
                category="knowledge", source="test", status="active",
            )
        await engine.dispose()

        fresh = await _fresh_index_engine(file_db_path, force=True)
        try:
            async with fresh.connect() as conn:
                assert await get_active_blocks(conn) == []
        finally:
            await fresh.dispose()


# ── _index_rebuild_async ─────────────────────────────────────────────────────


class TestIndexRebuildAsync:
    async def test_rebuilds_target_db_from_memory_dir(
        self, tmp_path, cfg, patched_factory,
    ):
        memory_dir = tmp_path / "memory"
        _write(
            memory_dir / "notes" / "principles.md",
            "## Minimum force\n<!-- id: 8f3a2b1c  tags: [self/value] -->\n\nApply the smallest change.\n",
        )
        _write(
            memory_dir / "log" / "2026-08.md",
            "## Fresh note\n<!-- id: 1a2b3c4d -->\n\nNot yet reviewed.\n",
        )
        target_db = str(tmp_path / "target.db")

        result = await _index_rebuild_async(target_db, memory_dir, cfg, force=False)
        assert result.blocks_written == 2

        engine = await create_engine(target_db)
        try:
            async with engine.connect() as conn:
                assert len(await get_active_blocks(conn)) == 1
                assert len(await get_inbox_blocks(conn)) == 1
        finally:
            await engine.dispose()

    async def test_refuses_to_rebuild_into_an_already_populated_target(
        self, tmp_path, cfg, patched_factory,
    ):
        memory_dir = tmp_path / "memory"
        _write(
            memory_dir / "notes" / "principles.md",
            "## A block\n<!-- id: abc123 -->\n\nContent.\n",
        )
        target_db = str(tmp_path / "target.db")

        await _index_rebuild_async(target_db, memory_dir, cfg, force=False)
        with pytest.raises(ElfmemError):
            await _index_rebuild_async(target_db, memory_dir, cfg, force=False)

    async def test_missing_memory_dir_fails_loudly(self, tmp_path, cfg, patched_factory):
        from elfmem.memory.index_rebuild import MemoryDirNotFoundError

        target_db = str(tmp_path / "target.db")
        with pytest.raises(MemoryDirNotFoundError):
            await _index_rebuild_async(target_db, tmp_path / "does-not-exist", cfg, force=False)


# ── _index_parity_async ──────────────────────────────────────────────────────


class TestIndexParityAsync:
    async def test_passes_when_rebuilt_state_matches_the_live_db(
        self, file_db_path, tmp_path, cfg, patched_factory,
    ):
        content = "A stable fact worth keeping."
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            await insert_block(
                conn, block_id="a1", content=content,
                category="knowledge", source="test", status="active",
            )
            # ATTENTION/TASK/SIMULATE frames have no tag filter, so they
            # prefilter on embeddings — an unembedded live block is
            # invisible to them and would show as a spurious divergence
            # against the rebuilt copy (which rebuild_index always embeds).
            vec = await patched_factory.embed(content.strip().lower())
            await update_block_scoring(
                conn, "a1", embedding=vec, embedding_model=patched_factory.model_name,
            )
        await engine.dispose()

        memory_dir = tmp_path / "memory"
        await _export_markdown_async(file_db_path, memory_dir)

        result = await _index_parity_async(file_db_path, memory_dir, cfg, [])
        assert result.block_count_matches
        assert result.passed

    async def test_fails_when_live_db_has_a_block_the_files_do_not(
        self, file_db_path, tmp_path, cfg, patched_factory,
    ):
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            await insert_block(
                conn, block_id="a1", content="Exported before the second write.",
                category="knowledge", source="test", status="active",
            )
        await engine.dispose()

        memory_dir = tmp_path / "memory"
        await _export_markdown_async(file_db_path, memory_dir)

        # A block lands in the live DB *after* the export ran — the rebuilt
        # index (built from the files) can never see it.
        engine = await create_engine(file_db_path)
        async with engine.begin() as conn:
            await insert_block(
                conn, block_id="a2", content="Never exported.",
                category="knowledge", source="test", status="active",
            )
        await engine.dispose()

        result = await _index_parity_async(file_db_path, memory_dir, cfg, [])
        assert not result.block_count_matches
        assert not result.passed


# ── CLI wiring smoke tests ───────────────────────────────────────────────────
# Each isolates into a throwaway "project" (a fresh tmp_path with its own
# .git marker) so path auto-discovery can never reach this repo's own real
# .elfmem/config.yaml / production database.


class TestCliWiring:
    def test_index_check_command(self, tmp_path, monkeypatch):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        memory_dir = tmp_path / ".elfmem" / "memory"
        _write(
            memory_dir / "notes" / "principles.md",
            "## A block\n<!-- id: abc123 -->\n\nContent.\n",
        )
        # --memory-dir explicit: config-based default resolution legitimately
        # falls through to the real global ~/.elfmem/config.yaml when this
        # throwaway project has no .elfmem/config.yaml of its own — same
        # chain every other command uses, not something to special-case here.
        result = runner.invoke(app, ["index", "check", "--memory-dir", str(memory_dir), "--json"])
        assert result.exit_code == 0
        assert '"blocks": 1' in result.output

    def test_export_to_markdown_command(self, tmp_path, monkeypatch, file_db_path_sync):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        memory_dir = tmp_path / "out"
        result = runner.invoke(
            app,
            [
                "export", "--to-markdown",
                "--db", file_db_path_sync,
                "--memory-dir", str(memory_dir),
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        assert '"blocks_exported": 1' in result.output
        assert (memory_dir / "notes" / "knowledge.md").exists()

    def test_index_rebuild_command(self, tmp_path, monkeypatch, patched_factory):
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        memory_dir = tmp_path / ".elfmem" / "memory"
        _write(
            memory_dir / "notes" / "principles.md",
            "## A block\n<!-- id: abc123 -->\n\nContent.\n",
        )
        target = tmp_path / "target.db"
        result = runner.invoke(
            app,
            ["index", "rebuild", "--memory-dir", str(memory_dir), "--to", str(target), "--json"],
        )
        assert result.exit_code == 0, result.output
        assert '"blocks_written": 1' in result.output
        assert target.exists()


@pytest.fixture
def file_db_path_sync(tmp_path):
    """Synchronous wrapper: a file-backed DB with one active, tagged block,
    for the CliRunner smoke test (which cannot await fixtures directly)."""
    import asyncio

    path = str(tmp_path / "source.db")

    async def _setup() -> None:
        engine = await create_engine(path)
        async with engine.begin() as conn:
            from elfmem.db.models import metadata

            await conn.run_sync(metadata.create_all)
            await insert_block(
                conn, block_id="a1", content="A fact to export.",
                category="knowledge", source="test", status="active",
            )
        await engine.dispose()

    asyncio.run(_setup())
    return path
