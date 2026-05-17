"""Tests for `elfmem migrate-embeddings` (Phase 2 of the embedding-lock work).

These tests exercise the async helpers directly rather than invoking the
typer CLI, so they can run fast in-memory and assert observable state
against the DB.

Key correctness property tested: **the migration bypasses the
LockedEmbeddingService wrapper**. A test sets up a DB with a lock to
model A, points the config at model B, and runs `_migrate_embeddings_execute`.
If the wrapper applied, this would deadlock with EmbeddingLockError.
The test passing proves the bypass is honoured.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockEmbeddingService
from elfmem.cli import _migrate_embeddings_estimate, _migrate_embeddings_execute
from elfmem.db.engine import create_engine
from elfmem.db.queries import (
    embedding_to_bytes,
    get_config,
    seed_builtin_data,
)

# ── Test helpers ────────────────────────────────────────────────────────────


async def _insert_active_block(
    conn,
    *,
    block_id: str,
    content: str,
    embedding: np.ndarray,
    embedding_model: str | None,
    summary: str | None = None,
) -> None:
    """Direct insert bypassing learn/consolidate for fixture setup."""
    await conn.execute(text(
        "INSERT INTO blocks (id, content, category, source, status, "
        "confidence, reinforcement_count, decay_lambda, last_reinforced_at, "
        "outcome_evidence, created_at, embedding, embedding_model, summary) "
        "VALUES (:id, :content, 'knowledge', 'test', 'active', "
        "0.7, 0, 0.01, 0.0, 0.0, :ts, :emb, :model, :summary)"
    ), {
        "id": block_id, "content": content,
        "ts": datetime.now(UTC).isoformat(),
        "emb": embedding_to_bytes(embedding),
        "model": embedding_model,
        "summary": summary,
    })


async def _insert_edge(conn, *, from_id: str, to_id: str, origin: str) -> None:
    await conn.execute(text(
        "INSERT INTO edges (from_id, to_id, weight, reinforcement_count, "
        "created_at, relation_type, origin) VALUES "
        "(:from_id, :to_id, 0.5, 0, :ts, 'similar', :origin)"
    ), {
        "from_id": from_id, "to_id": to_id,
        "ts": datetime.now(UTC).isoformat(),
        "origin": origin,
    })


@pytest.fixture
async def file_db_path():
    """A file-backed test DB. The migration command works on a path, not an
    in-memory engine, so we need a real file. tmpfile cleans up after."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    engine = await create_engine(path)
    async with engine.begin() as conn:
        from elfmem.db.models import metadata
        await conn.run_sync(metadata.create_all)
        await seed_builtin_data(conn)
    await engine.dispose()
    yield path
    Path(path).unlink(missing_ok=True)


def _mock_cfg(model_name: str = "mock", dimensions: int = 64):
    """A minimal ElfmemConfig-ish object that satisfies make_embedding_adapter.

    But the migration uses make_embedding_adapter which expects a real
    ElfmemConfig; we patch the embedding service in via monkeypatching the
    factory function. Simpler: just construct ElfmemConfig with the mock
    model name and tests rely on the test-only adapter resolution.

    Since make_embedding_adapter doesn't recognise "mock" as a provider,
    we instead inject a MockEmbeddingService directly by monkey-patching
    in the test. See _patch_make_embedding_adapter below.
    """
    from elfmem.config import ElfmemConfig
    return ElfmemConfig.model_validate({"embeddings": {"model": model_name, "dimensions": dimensions}})


@pytest.fixture
def patched_factory(monkeypatch):
    """Replace make_embedding_adapter so tests use MockEmbeddingService.

    The migration constructs its EmbeddingService via the factory; we
    redirect that to a deterministic mock so tests don't need real
    provider credentials.
    """
    def _patch(model_name: str, dimensions: int = 64) -> MockEmbeddingService:
        mock = MockEmbeddingService(dimensions=dimensions)
        # The mock's model_name is hardcoded as "mock"; override per-test
        # via a property descriptor that reads from a per-instance attribute.
        mock_class = type(mock)

        def _make_mock(name: str) -> MockEmbeddingService:
            instance = MockEmbeddingService(dimensions=dimensions)
            # Use a closure-set name via attribute injection (mocks should allow this)
            instance._test_model_name = name  # type: ignore[attr-defined]
            return instance

        new_instance = _make_mock(model_name)

        # Monkey-patch the model_name property on the instance via class override
        # — easiest path: replace the descriptor with a lambda for this instance.
        def model_name_override(self) -> str:
            return getattr(self, "_test_model_name", "mock")

        monkeypatch.setattr(mock_class, "model_name", property(model_name_override))

        def factory(cfg, counter):
            return new_instance

        monkeypatch.setattr(
            "elfmem.adapters.factory.make_embedding_adapter",
            factory,
        )
        return new_instance

    return _patch


# ── Estimate mode ────────────────────────────────────────────────────────────


async def test_estimate_reports_counts_correctly(file_db_path, capsys) -> None:
    """Agent-perspective: estimate output includes block count, char count,
    target model — observable via stdout."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        for i in range(3):
            await _insert_active_block(
                conn,
                block_id=f"b{i}",
                content=f"content number {i}",
                embedding=np.ones(64, dtype=np.float32),
                embedding_model="old-model",
            )
    await engine.dispose()

    await _migrate_embeddings_estimate(file_db_path, "new-model", from_model=None)

    out = capsys.readouterr().out
    assert "blocks to re-embed:    3" in out
    assert "new-model" in out
    assert "Run with --execute to proceed" in out


async def test_estimate_zero_when_all_blocks_match_target(file_db_path, capsys) -> None:
    """When existing blocks already use the target model, estimate reports 0."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        await _insert_active_block(
            conn,
            block_id="b0",
            content="already migrated",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="new-model",
        )
    await engine.dispose()

    await _migrate_embeddings_estimate(file_db_path, "new-model", from_model=None)
    out = capsys.readouterr().out
    assert "Nothing to migrate" in out


async def test_estimate_counts_null_embedding_model_rows(file_db_path, capsys) -> None:
    """SQL NULL trap: rows with embedding_model=NULL must be counted as
    needing migration. A naive `!= :target` filter would silently skip them."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        await _insert_active_block(
            conn,
            block_id="null-row",
            content="legacy",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model=None,  # the trap
        )
    await engine.dispose()

    await _migrate_embeddings_estimate(file_db_path, "new-model", from_model=None)
    out = capsys.readouterr().out
    assert "blocks to re-embed:    1" in out


# ── Execute mode ─────────────────────────────────────────────────────────────


async def test_execute_migrates_blocks_and_updates_lock(
    file_db_path, patched_factory, capsys
) -> None:
    """End-to-end execute: blocks get new embedding_model, lock points at
    target, similarity edges dropped."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        for i in range(3):
            await _insert_active_block(
                conn,
                block_id=f"b{i}",
                content=f"content {i}",
                embedding=np.ones(64, dtype=np.float32),
                embedding_model="old-model",
            )
        # Edges: similarity (drop), co_retrieval (drop), user (preserve)
        await _insert_edge(conn, from_id="b0", to_id="b1", origin="similarity")
        await _insert_edge(conn, from_id="b1", to_id="b2", origin="co_retrieval")
        await _insert_edge(conn, from_id="b0", to_id="b2", origin="user")
        # Seed lock to old-model so we can verify it changes
        await conn.execute(text(
            "INSERT INTO system_config (key, value) VALUES "
            "('embedding_model_lock', 'old-model'), "
            "('embedding_dimensions_lock', '64')"
        ))
    await engine.dispose()

    cfg = _mock_cfg(model_name="new-model", dimensions=64)
    patched_factory("new-model", dimensions=64)

    await _migrate_embeddings_execute(
        file_db_path, cfg, target="new-model", from_model=None, batch_size=2
    )

    engine = await create_engine(file_db_path)
    async with engine.connect() as conn:
        # All blocks now at new-model
        rows = (await conn.execute(text(
            "SELECT id, embedding_model FROM blocks WHERE status='active'"
        ))).all()
        models = {r[0]: r[1] for r in rows}
        assert all(v == "new-model" for v in models.values())

        # Lock updated
        lock = await get_config(conn, "embedding_model_lock")
        assert lock == "new-model"

        # Similarity-derived edges dropped; user edge preserved
        edges = (await conn.execute(text(
            "SELECT from_id, to_id, origin FROM edges"
        ))).all()
        origins = {(r[0], r[1]): r[2] for r in edges}
        assert ("b0", "b2") in origins and origins[("b0", "b2")] == "user"
        assert ("b0", "b1") not in origins  # similarity dropped
        assert ("b1", "b2") not in origins  # co_retrieval dropped
    await engine.dispose()


async def test_execute_is_resumable_after_partial_migration(
    file_db_path, patched_factory, capsys
) -> None:
    """Re-running `--execute` after a partial migration should skip
    already-migrated blocks (auto-resume via the WHERE filter)."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        # Block 1: already at target (from a previous partial run)
        await _insert_active_block(
            conn, block_id="done", content="already done",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="new-model",
        )
        # Block 2: still at source
        await _insert_active_block(
            conn, block_id="pending", content="still pending",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="old-model",
        )
    await engine.dispose()

    cfg = _mock_cfg(model_name="new-model", dimensions=64)
    mock = patched_factory("new-model", dimensions=64)

    await _migrate_embeddings_execute(
        file_db_path, cfg, target="new-model", from_model=None, batch_size=10
    )

    # The mock counts embed calls. Should have embedded only the pending block.
    assert mock.embed_calls == 1


async def test_execute_handles_null_embedding_model_rows(
    file_db_path, patched_factory, capsys
) -> None:
    """SQL NULL trap regression test: rows with embedding_model=NULL get migrated."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="null-row", content="legacy",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model=None,
        )
    await engine.dispose()

    cfg = _mock_cfg(model_name="new-model", dimensions=64)
    patched_factory("new-model", dimensions=64)

    await _migrate_embeddings_execute(
        file_db_path, cfg, target="new-model", from_model=None, batch_size=10
    )

    engine = await create_engine(file_db_path)
    async with engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT embedding_model FROM blocks WHERE id = 'null-row'"
        ))).first()
        assert row[0] == "new-model"
    await engine.dispose()


async def test_execute_bypasses_locked_wrapper(
    file_db_path, patched_factory, capsys
) -> None:
    """**The critical correctness property**: migration must not self-block.

    If the migration went through MemorySystem.from_config() it would
    apply the LockedEmbeddingService wrapper, which would refuse to embed
    because the configured (new-model) disagrees with the locked
    (old-model). The test passing proves the bypass is honoured.
    """
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="b0", content="x",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="old-model",
        )
        # Seed lock to old-model so the wrapper would refuse if applied
        await conn.execute(text(
            "INSERT INTO system_config (key, value) VALUES "
            "('embedding_model_lock', 'old-model'), "
            "('embedding_dimensions_lock', '64')"
        ))
    await engine.dispose()

    cfg = _mock_cfg(model_name="new-model", dimensions=64)
    patched_factory("new-model", dimensions=64)

    # If the wrapper applied, this would raise EmbeddingLockError.
    # It must succeed because migration uses the bare adapter.
    await _migrate_embeddings_execute(
        file_db_path, cfg, target="new-model", from_model=None, batch_size=10
    )

    # And the lock should now be updated to the new model.
    engine = await create_engine(file_db_path)
    async with engine.connect() as conn:
        lock = await get_config(conn, "embedding_model_lock")
        assert lock == "new-model"
    await engine.dispose()


async def test_execute_from_model_filter_only_migrates_matching(
    file_db_path, patched_factory, capsys
) -> None:
    """Heterogeneous DB: --from filters which blocks get migrated."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="a", content="model-A",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="model-A",
        )
        await _insert_active_block(
            conn, block_id="b", content="model-B",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="model-B",
        )
    await engine.dispose()

    cfg = _mock_cfg(model_name="new-model", dimensions=64)
    patched_factory("new-model", dimensions=64)

    # Migrate only model-A blocks
    await _migrate_embeddings_execute(
        file_db_path, cfg, target="new-model", from_model="model-A", batch_size=10
    )

    engine = await create_engine(file_db_path)
    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT id, embedding_model FROM blocks WHERE status='active'"
        ))).all()
        models = {r[0]: r[1] for r in rows}
    await engine.dispose()

    assert models["a"] == "new-model"  # migrated
    assert models["b"] == "model-B"  # unchanged


async def test_execute_uses_summary_when_present(
    file_db_path, patched_factory, capsys
) -> None:
    """Blocks with a summary should re-embed the summary text, not content
    (matching what consolidate.py does at line 343-344)."""
    engine = await create_engine(file_db_path)
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="b0",
            content="long original content here",
            summary="short summary",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="old-model",
        )
    await engine.dispose()

    cfg = _mock_cfg(model_name="new-model", dimensions=64)
    mock = patched_factory("new-model", dimensions=64)

    await _migrate_embeddings_execute(
        file_db_path, cfg, target="new-model", from_model=None, batch_size=10
    )

    # The mock's embed call should have used the lowercased summary, not the content.
    # Mock embedding caches by text — check the cache key.
    assert "short summary" in mock._cache
    assert "long original content here" not in mock._cache
