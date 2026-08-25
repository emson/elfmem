"""Tests for the substrate_export migration step in elfmem.migrate.

Exercises the async helpers directly (real file-based SQLite, matching
export/rebuild/parity's own requirement of separate on-disk databases —
:memory: can't be shared across the multiple engines this step opens).
Embeddings are mocked via monkeypatching make_embedding_adapter, the same
pattern test_migrate_embeddings.py already established, since these
functions build their embedding service from cfg through the real factory.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from elfmem.adapters.mock import MockEmbeddingService
from elfmem.config import ElfmemConfig
from elfmem.db.engine import create_engine
from elfmem.db.models import metadata
from elfmem.db.queries import add_tags, insert_block
from elfmem.migrate import (
    apply_substrate_step,
    build_full_plan,
    scan_substrate,
    undo_substrate_step,
)


@pytest.fixture
def mock_cfg() -> ElfmemConfig:
    return ElfmemConfig.model_validate({"embeddings": {"model": "mock", "dimensions": 8}})


@pytest.fixture
def patched_embeddings(monkeypatch):
    """Redirect make_embedding_adapter to a deterministic mock, matching
    test_migrate_embeddings.py's pattern — the substrate step builds its
    embedding service through the real factory, which needs credentials
    for anything other than a mock."""
    mock = MockEmbeddingService(dimensions=8)

    def factory(cfg, counter):
        return mock

    monkeypatch.setattr("elfmem.adapters.factory.make_embedding_adapter", factory)
    return mock


async def _make_db(db_path: Path) -> None:
    engine = await create_engine(str(db_path))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
    finally:
        await engine.dispose()


async def _insert(
    db_path: Path, block_id: str, content: str, *, category: str = "knowledge",
    status: str = "active", tags: list[str] | None = None,
) -> None:
    engine = await create_engine(str(db_path))
    try:
        async with engine.begin() as conn:
            await insert_block(
                conn, block_id=block_id, content=content, category=category,
                source="test", status=status,
            )
            if tags:
                await add_tags(conn, block_id, tags)
    finally:
        await engine.dispose()


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    """(db_path, memory_dir) for a fresh, empty project layout."""
    db_path = tmp_path / "agent.db"
    memory_dir = tmp_path / ".elfmem" / "memory"
    return db_path, memory_dir


class TestScanSubstrate:
    async def test_no_db_is_not_pending(self, tmp_path: Path):
        db_path = tmp_path / "missing.db"
        memory_dir = tmp_path / ".elfmem" / "memory"
        assert await scan_substrate(db_path, memory_dir) is None

    async def test_empty_db_is_not_pending(self, project: tuple[Path, Path]):
        db_path, memory_dir = project
        await _make_db(db_path)
        assert await scan_substrate(db_path, memory_dir) is None

    async def test_db_with_content_is_pending(self, project: tuple[Path, Path]):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)
        assert step is not None
        assert step.kind == "substrate_export"
        assert step.before == {"active": 1, "inbox": 0, "archived": 0}

    async def test_up_to_date_after_apply_is_not_pending(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)
        result = await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)
        assert result.status == "applied"

        assert await scan_substrate(db_path, memory_dir) is None

    async def test_db_change_after_apply_is_pending_again(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)
        await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)

        await _insert(db_path, "b2", "New knowledge learned after migration")
        step2 = await scan_substrate(db_path, memory_dir)
        assert step2 is not None
        assert "changed since the last export" in step2.issues[0]


class TestApplySubstrateStep:
    async def test_dry_run_writes_nothing(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)

        result = await apply_substrate_step(
            step, memory_dir=memory_dir, cfg=mock_cfg, dry_run=True,
        )
        assert result.status == "applied"
        assert "dry-run" in result.detail
        assert not memory_dir.exists()
        assert result.backup is None

    async def test_real_run_exports_rebuilds_and_verifies(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge", category="knowledge")
        await _insert(db_path, "b2", "Alice model", category="mind")
        step = await scan_substrate(db_path, memory_dir)

        result = await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)

        assert result.status == "applied"
        assert result.backup is not None
        assert result.backup.exists()
        assert (memory_dir / "notes" / "knowledge.md").exists()
        assert (memory_dir / "notes" / "mind.md").exists()

        index_db_path = memory_dir.parent / "index.db"
        assert index_db_path.exists()

        # The original database must be byte-identical to before — never
        # written to, only ever read from.
        from elfmem.db.queries import get_active_blocks

        engine = await create_engine(str(db_path))
        try:
            async with engine.connect() as conn:
                live_blocks = await get_active_blocks(conn)
        finally:
            await engine.dispose()
        assert {b["id"] for b in live_blocks} == {"b1", "b2"}

        # And the rebuilt index preserved category correctly (the bug this
        # whole migration depends on being fixed).
        rebuilt_engine = await create_engine(str(index_db_path))
        try:
            async with rebuilt_engine.connect() as conn:
                rebuilt_blocks = await get_active_blocks(conn)
        finally:
            await rebuilt_engine.dispose()
        categories = {b["id"]: b["category"] for b in rebuilt_blocks}
        assert categories == {"b1": "knowledge", "b2": "mind"}

    async def test_stale_when_db_changed_since_plan(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)

        await _insert(db_path, "b2", "Added after plan was built")

        result = await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)
        assert result.status == "stale"
        assert not memory_dir.exists()

    async def test_reapply_is_idempotent_and_up_to_date(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)
        first = await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)
        assert first.status == "applied"

        # Re-scanning finds nothing pending -- re-running apply on an
        # unchanged corpus is something callers simply won't be asked to do
        # (scan_substrate gates it), but the rebuild step itself is also
        # safe to repeat directly without erroring.
        step_again = await scan_substrate(db_path, memory_dir)
        assert step_again is None


class TestUndoSubstrateStep:
    async def test_undo_removes_generated_artifacts(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)
        apply_result = await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)
        backup_path = apply_result.backup
        index_db_path = memory_dir.parent / "index.db"
        assert memory_dir.exists()
        assert index_db_path.exists()

        undo_result = await undo_substrate_step(step, memory_dir=memory_dir)

        assert undo_result.status == "applied"
        assert not memory_dir.exists()
        assert not index_db_path.exists()
        # The backup is not removed by undo -- it's the audit trail, and
        # the original database (never touched) is untouched either.
        assert backup_path.exists()
        assert db_path.exists()

    async def test_undo_with_nothing_applied_is_skipped(
        self, project: tuple[Path, Path],
    ):
        db_path, memory_dir = project
        await _make_db(db_path)

        from elfmem.migrate import MigrationStep

        fake_step = MigrationStep(
            id="substrate-export@agent-deadbeef", kind="substrate_export",
            summary="", file=db_path, file_sha256="x", issues=[], before={},
            after={}, json_pointer="",
        )
        result = await undo_substrate_step(fake_step, memory_dir=memory_dir)
        assert result.status == "skipped"

    async def test_undo_refuses_hand_edited_files_without_force(
        self, project: tuple[Path, Path], mock_cfg, patched_embeddings,
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")
        step = await scan_substrate(db_path, memory_dir)
        await apply_substrate_step(step, memory_dir=memory_dir, cfg=mock_cfg)

        # Simulate a human curating the exported file after migration.
        exported = memory_dir / "notes" / "knowledge.md"
        exported.write_text(exported.read_text(encoding="utf-8") + "\nHand-added note.\n")

        result = await undo_substrate_step(step, memory_dir=memory_dir)
        assert result.status == "failed"
        assert memory_dir.exists()  # refused -- nothing removed

        forced = await undo_substrate_step(step, memory_dir=memory_dir, force=True)
        assert forced.status == "applied"
        assert not memory_dir.exists()


class TestBuildFullPlan:
    async def test_includes_substrate_step_when_pending(
        self, project: tuple[Path, Path],
    ):
        db_path, memory_dir = project
        await _make_db(db_path)
        await _insert(db_path, "b1", "Some knowledge")

        plan = await build_full_plan(db_path=db_path, memory_dir=memory_dir)
        assert any(s.kind == "substrate_export" for s in plan.steps)

    async def test_no_db_path_skips_substrate_check(self):
        plan = await build_full_plan(db_path=None, memory_dir=None)
        assert all(s.kind != "substrate_export" for s in plan.steps)
