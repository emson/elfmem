"""Tests for the embedding-model lock (LockedEmbeddingService + backfill).

Covers the agent-perspective and backwards-compat obligations stored in
elf's memory: every public behaviour is asserted via observable state
(``to_dict``-equivalents — here ``get_config()`` of the system_config row);
every change to hashed/serialized state has an upgrade-path test written
before the implementation.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from elfmem.adapters.locked import LockedEmbeddingService
from elfmem.adapters.mock import MockEmbeddingService
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import (
    backfill_embedding_lock_if_needed,
    embedding_to_bytes,
    get_config,
    seed_builtin_data,
)
from elfmem.exceptions import EmbeddingLockError

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def engine():
    eng = await create_test_engine()
    async with eng.begin() as conn:
        await seed_builtin_data(conn)
    yield eng
    await eng.dispose()


async def _insert_active_block(
    conn,
    *,
    block_id: str,
    content: str,
    embedding: np.ndarray,
    embedding_model: str | None,
) -> None:
    """Direct insert bypassing learn/consolidate for fixture setup."""
    from datetime import UTC, datetime

    await conn.execute(text(
        "INSERT INTO blocks (id, content, category, source, status, "
        "confidence, reinforcement_count, decay_lambda, last_reinforced_at, "
        "outcome_evidence, created_at, embedding, embedding_model) VALUES "
        "(:id, :content, 'knowledge', 'test', 'active', "
        "0.7, 0, 0.01, 0.0, 0.0, :ts, :emb, :model)"
    ), {
        "id": block_id, "content": content,
        "ts": datetime.now(UTC).isoformat(),
        "emb": embedding_to_bytes(embedding),
        "model": embedding_model,
    })


# ── Wrapper behaviour ────────────────────────────────────────────────────────


async def test_wrapper_sets_lock_on_first_embed(engine) -> None:
    """Agent-perspective: after a single embed, lock is observable via get_config."""
    inner = MockEmbeddingService(dimensions=64)
    wrapper = LockedEmbeddingService(inner, engine)

    await wrapper.embed("first text")

    async with engine.connect() as conn:
        model = await get_config(conn, "embedding_model_lock")
        dims = await get_config(conn, "embedding_dimensions_lock")
    assert model == inner.model_name  # "mock"
    assert dims == "64"


async def test_wrapper_passes_when_lock_matches_adapter(engine) -> None:
    """Second embed with matching adapter never raises."""
    inner = MockEmbeddingService(dimensions=64)
    wrapper = LockedEmbeddingService(inner, engine)
    await wrapper.embed("first")
    # Should not raise:
    await wrapper.embed("second")
    await wrapper.embed_batch(["third", "fourth"])


async def test_wrapper_raises_on_model_mismatch(engine) -> None:
    """Pre-seed lock to one model, wrap a different adapter, embed → raise.

    Asserts both the exception type and that .recovery points at the
    migration command (agent-first contract: every raise is the question
    'what's the next command the user runs?').
    """
    # Pre-seed lock as if previous installs were on a different model
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO system_config (key, value) VALUES "
            "('embedding_model_lock', 'text-embedding-3-small'), "
            "('embedding_dimensions_lock', '1536')"
        ))

    inner = MockEmbeddingService(dimensions=64)  # different model_name ('mock') AND dims
    wrapper = LockedEmbeddingService(inner, engine)

    with pytest.raises(EmbeddingLockError) as exc_info:
        await wrapper.embed("anything")

    # Recovery must mention the migration command (the actionable next step).
    assert "migrate-embeddings" in exc_info.value.recovery
    # And the error explains the mismatch concretely.
    assert "text-embedding-3-small" in str(exc_info.value)
    assert "mock" in str(exc_info.value)


async def test_wrapper_raises_on_dimension_mismatch_same_model(engine) -> None:
    """Same model name but different dim (provider-truncation case)."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO system_config (key, value) VALUES "
            "('embedding_model_lock', 'mock'), "
            "('embedding_dimensions_lock', '128')"
        ))

    inner = MockEmbeddingService(dimensions=64)
    wrapper = LockedEmbeddingService(inner, engine)

    with pytest.raises(EmbeddingLockError) as exc_info:
        await wrapper.embed("anything")
    assert "64-dim" in str(exc_info.value)
    assert "128-dim" in str(exc_info.value)


async def test_wrapper_intercepts_embed_batch(engine) -> None:
    """consolidate.py calls embed_batch first — verification must trigger there too."""
    inner = MockEmbeddingService(dimensions=64)
    wrapper = LockedEmbeddingService(inner, engine)

    # Only call embed_batch, never embed
    await wrapper.embed_batch(["a", "b", "c"])

    async with engine.connect() as conn:
        model = await get_config(conn, "embedding_model_lock")
    assert model == "mock"


async def test_wrapper_no_session_cache_catches_external_lock_change(engine) -> None:
    """Long-lived wrapper instance: simulate a concurrent migration that
    changes the lock externally. Next embed must catch it.

    This is the key correctness property — earlier draft used a
    _session_verified cache that would have missed this.
    """
    inner = MockEmbeddingService(dimensions=64)
    wrapper = LockedEmbeddingService(inner, engine)

    # First embed sets the lock to "mock"
    await wrapper.embed("first")

    # External "migration" overwrites the lock to a different model
    async with engine.begin() as conn:
        await conn.execute(text(
            "UPDATE system_config SET value = 'some-other-model' "
            "WHERE key = 'embedding_model_lock'"
        ))

    # The same wrapper instance, on its next call, must catch this.
    with pytest.raises(EmbeddingLockError):
        await wrapper.embed("second")


async def test_wrapper_embed_batch_with_empty_list_does_not_verify(engine) -> None:
    """An empty embed_batch should not produce a vector and not set the lock."""
    inner = MockEmbeddingService(dimensions=64)
    wrapper = LockedEmbeddingService(inner, engine)
    out = await wrapper.embed_batch([])
    assert out == []
    async with engine.connect() as conn:
        model = await get_config(conn, "embedding_model_lock")
    assert model is None  # no lock set


# ── Backfill behaviour ────────────────────────────────────────────────────────


async def test_backfill_no_op_when_lock_already_set(engine) -> None:
    """Idempotent: re-running backfill with an existing lock is a no-op."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO system_config (key, value) VALUES "
            "('embedding_model_lock', 'pre-existing'), "
            "('embedding_dimensions_lock', '99')"
        ))
        await backfill_embedding_lock_if_needed(conn)
        # Unchanged
        model = await get_config(conn, "embedding_model_lock")
    assert model == "pre-existing"


async def test_backfill_no_op_on_fresh_db(engine) -> None:
    """No active blocks, no lock → backfill leaves both unset."""
    async with engine.begin() as conn:
        await backfill_embedding_lock_if_needed(conn)
        model = await get_config(conn, "embedding_model_lock")
    assert model is None


async def test_backfill_homogeneous_sets_lock_silently(engine) -> None:
    """All active blocks agree on a model → lock is set, no error."""
    async with engine.begin() as conn:
        for i, content in enumerate(["one", "two", "three"]):
            await _insert_active_block(
                conn,
                block_id=f"block-{i}",
                content=content,
                embedding=np.ones(128, dtype=np.float32),
                embedding_model="text-embedding-3-small",
            )
        await backfill_embedding_lock_if_needed(conn)
        model = await get_config(conn, "embedding_model_lock")
        dims = await get_config(conn, "embedding_dimensions_lock")
    assert model == "text-embedding-3-small"
    assert dims == "128"


async def test_backfill_legacy_rows_get_backfilled_to_dominant_model(engine) -> None:
    """Mix of known + NULL + 'unknown' → lock set, legacy rows updated."""
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="known", content="x",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="text-embedding-3-small",
        )
        await _insert_active_block(
            conn, block_id="null-model", content="y",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model=None,
        )
        await _insert_active_block(
            conn, block_id="unknown-model", content="z",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="unknown",
        )
        await backfill_embedding_lock_if_needed(conn)

        # Lock set from the known row
        model = await get_config(conn, "embedding_model_lock")
        assert model == "text-embedding-3-small"

        # Legacy rows updated
        result = await conn.execute(text(
            "SELECT id, embedding_model FROM blocks WHERE status = 'active'"
        ))
        updated = {r[0]: r[1] for r in result.all()}
    assert all(v == "text-embedding-3-small" for v in updated.values())


async def test_backfill_heterogeneous_refuses_with_recovery(engine) -> None:
    """Two different known models → refuse with --from --to recovery."""
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="a", content="x",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="model-A",
        )
        await _insert_active_block(
            conn, block_id="b", content="y",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="model-B",
        )
        with pytest.raises(EmbeddingLockError) as exc_info:
            await backfill_embedding_lock_if_needed(conn)

    assert "migrate-embeddings" in exc_info.value.recovery
    assert "--from" in exc_info.value.recovery
    assert "--to" in exc_info.value.recovery


async def test_backfill_all_legacy_refuses_with_recovery(engine) -> None:
    """All active blocks have NULL/'unknown' → refuse rather than guess."""
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="a", content="x",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model=None,
        )
        await _insert_active_block(
            conn, block_id="b", content="y",
            embedding=np.ones(64, dtype=np.float32),
            embedding_model="unknown",
        )
        with pytest.raises(EmbeddingLockError) as exc_info:
            await backfill_embedding_lock_if_needed(conn)

    assert "migrate-embeddings" in exc_info.value.recovery
    assert "--execute" in exc_info.value.recovery


# ── Backwards-compat (upgrade path) ─────────────────────────────────────────


async def test_pre_feature_db_with_homogeneous_data_upgrades_silently(engine) -> None:
    """Backwards-compat: a v0.14.x install upgrading sees no error if their
    embedding_model column is populated consistently. The lock just appears,
    derived from existing state.
    """
    async with engine.begin() as conn:
        for i in range(5):
            await _insert_active_block(
                conn,
                block_id=f"existing-{i}",
                content=f"content {i}",
                embedding=np.ones(1536, dtype=np.float32),
                embedding_model="text-embedding-3-small",
            )
        # Simulate from_config() backfill on first upgrade boot
        await backfill_embedding_lock_if_needed(conn)
        model = await get_config(conn, "embedding_model_lock")
        dims = await get_config(conn, "embedding_dimensions_lock")
    assert model == "text-embedding-3-small"
    assert dims == "1536"


async def test_pre_feature_db_with_mixed_models_refuses_loudly(engine) -> None:
    """The case the lock is meant to expose: an install where the user
    already swapped models silently has heterogeneous state. Upgrade
    refuses, surfacing the existing corruption rather than perpetuating it.
    """
    async with engine.begin() as conn:
        await _insert_active_block(
            conn, block_id="old-1", content="from old model",
            embedding=np.ones(1536, dtype=np.float32),
            embedding_model="text-embedding-ada-002",
        )
        await _insert_active_block(
            conn, block_id="new-1", content="from new model",
            embedding=np.ones(1536, dtype=np.float32),
            embedding_model="text-embedding-3-small",
        )
        with pytest.raises(EmbeddingLockError):
            await backfill_embedding_lock_if_needed(conn)
