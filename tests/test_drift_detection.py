"""Drift detection — pure math + DB helpers for v0.18 constitutional review.

Pure math tests stand alone (no DB). DB-helper tests use the project's
shared ``test_engine`` fixture per ``tests/CLAUDE.md`` rules.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from elfmem.db.queries import add_tags, embedding_to_bytes, insert_block
from elfmem.operations.review import (
    CONSTITUTIONAL_TAG,
    compute_drift,
    fetch_constitutional_blocks,
    fetch_recent_reinforced_embeddings,
    recent_self_centroid,
)

# ── Pure math: compute_drift ─────────────────────────────────────────────────


class TestComputeDrift:
    def test_aligned_returns_zero(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        assert compute_drift(v, v) == pytest.approx(0.0, abs=1e-6)

    def test_opposed_returns_one(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = -a
        assert compute_drift(a, b) == pytest.approx(1.0, abs=1e-6)

    def test_orthogonal_returns_half(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        assert compute_drift(a, b) == pytest.approx(0.5, abs=1e-6)

    def test_range_is_zero_one_for_random_inputs(self):
        rng = np.random.default_rng(seed=42)
        for _ in range(50):
            a = rng.standard_normal(64).astype(np.float32)
            b = rng.standard_normal(64).astype(np.float32)
            d = compute_drift(a, b)
            assert 0.0 <= d <= 1.0

    def test_symmetric(self):
        rng = np.random.default_rng(seed=7)
        a = rng.standard_normal(32).astype(np.float32)
        b = rng.standard_normal(32).astype(np.float32)
        assert compute_drift(a, b) == pytest.approx(
            compute_drift(b, a), abs=1e-9,
        )

    def test_scale_invariant(self):
        """Drift only depends on direction — magnitude is normalised out."""
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 1.0, 0.0], dtype=np.float32)
        d1 = compute_drift(a, b)
        d2 = compute_drift(a * 17.3, b * 0.01)
        assert d1 == pytest.approx(d2, abs=1e-6)

    def test_zero_vector_returns_half(self):
        """Undefined direction → maximally uncertain (0.5), not NaN."""
        a = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        assert compute_drift(a, b) == 0.5


# ── Pure math: recent_self_centroid ──────────────────────────────────────────


class TestRecentSelfCentroid:
    def test_empty_returns_none(self):
        assert recent_self_centroid([]) is None

    def test_single_block_returns_normalized(self):
        v = np.array([3.0, 4.0, 0.0], dtype=np.float32)  # |v| = 5
        out = recent_self_centroid([v])
        assert out is not None
        assert np.linalg.norm(out) == pytest.approx(1.0, abs=1e-6)
        # Direction preserved
        assert out[0] == pytest.approx(0.6, abs=1e-6)
        assert out[1] == pytest.approx(0.8, abs=1e-6)

    def test_two_blocks_returns_unit_mean(self):
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([0.0, 1.0], dtype=np.float32)
        out = recent_self_centroid([a, b])
        assert out is not None
        assert np.linalg.norm(out) == pytest.approx(1.0, abs=1e-6)
        # Mean direction is (0.5, 0.5) → normalised (√½, √½)
        assert out[0] == pytest.approx(out[1], abs=1e-6)

    def test_order_invariant(self):
        rng = np.random.default_rng(seed=11)
        vecs = [rng.standard_normal(16).astype(np.float32) for _ in range(5)]
        a = recent_self_centroid(vecs)
        b = recent_self_centroid(list(reversed(vecs)))
        assert a is not None and b is not None
        assert np.allclose(a, b, atol=1e-6)

    def test_output_dtype_is_float32(self):
        v = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        out = recent_self_centroid([v])
        assert out is not None
        assert out.dtype == np.float32

    def test_cancelling_vectors_return_zero_vector(self):
        """Symmetric pair sums to zero — degenerate but representable."""
        a = np.array([1.0, 0.0], dtype=np.float32)
        b = np.array([-1.0, 0.0], dtype=np.float32)
        out = recent_self_centroid([a, b])
        assert out is not None
        assert np.linalg.norm(out) == pytest.approx(0.0, abs=1e-6)


# ── DB helpers — fetch_recent_reinforced_embeddings ──────────────────────────


def _emb(seed: int, dim: int = 8) -> np.ndarray:
    rng = np.random.default_rng(seed=seed)
    return rng.standard_normal(dim).astype(np.float32)


async def _seed_block(
    conn,
    *,
    block_id: str,
    reinforcement_count: int = 0,
    last_reinforced_at: float = 0.0,
    status: str = "active",
    tags: list[str] | None = None,
    embedding: np.ndarray | None = None,
) -> None:
    await insert_block(
        conn,
        block_id=block_id,
        content=f"content-{block_id}",
        category="knowledge",
        source="test",
        status=status,
        last_reinforced_at=last_reinforced_at,
    )
    if reinforcement_count:
        await conn.execute(text(
            "UPDATE blocks SET reinforcement_count = :rc WHERE id = :id"
        ), {"rc": reinforcement_count, "id": block_id})
    if embedding is not None:
        await conn.execute(text(
            "UPDATE blocks SET embedding = :e WHERE id = :id"
        ), {"e": embedding_to_bytes(embedding), "id": block_id})
    if tags:
        await add_tags(conn, block_id, tags)


class TestFetchRecentReinforcedEmbeddings:
    async def test_filters_constitutional(self, db_conn):
        await _seed_block(
            db_conn, block_id="const", reinforcement_count=5,
            last_reinforced_at=100.0, embedding=_emb(1),
            tags=[CONSTITUTIONAL_TAG],
        )
        await _seed_block(
            db_conn, block_id="ord", reinforcement_count=5,
            last_reinforced_at=100.0, embedding=_emb(2),
        )

        out = await fetch_recent_reinforced_embeddings(
            db_conn, window_hours=1000.0, min_reinforcement=0,
            top_n=10, current_active_hours=200.0,
        )
        assert len(out) == 1  # only 'ord'

    async def test_filters_by_window(self, db_conn):
        await _seed_block(
            db_conn, block_id="old", reinforcement_count=5,
            last_reinforced_at=10.0, embedding=_emb(1),
        )
        await _seed_block(
            db_conn, block_id="new", reinforcement_count=5,
            last_reinforced_at=150.0, embedding=_emb(2),
        )
        out = await fetch_recent_reinforced_embeddings(
            db_conn, window_hours=50.0, min_reinforcement=0,
            top_n=10, current_active_hours=160.0,
        )
        # Cutoff = 110.0; 'new' (150.0) included, 'old' (10.0) excluded.
        assert len(out) == 1

    async def test_filters_by_min_reinforcement(self, db_conn):
        await _seed_block(
            db_conn, block_id="low", reinforcement_count=1,
            last_reinforced_at=100.0, embedding=_emb(1),
        )
        await _seed_block(
            db_conn, block_id="high", reinforcement_count=10,
            last_reinforced_at=100.0, embedding=_emb(2),
        )
        out = await fetch_recent_reinforced_embeddings(
            db_conn, window_hours=1000.0, min_reinforcement=5,
            top_n=10, current_active_hours=200.0,
        )
        assert len(out) == 1  # only 'high'

    async def test_top_n_limit(self, db_conn):
        for i in range(5):
            await _seed_block(
                db_conn, block_id=f"b{i}", reinforcement_count=5,
                last_reinforced_at=100.0 + i, embedding=_emb(i),
            )
        out = await fetch_recent_reinforced_embeddings(
            db_conn, window_hours=1000.0, min_reinforcement=0,
            top_n=3, current_active_hours=200.0,
        )
        assert len(out) == 3

    async def test_excludes_blocks_without_embeddings(self, db_conn):
        # block with no embedding shouldn't appear
        await _seed_block(
            db_conn, block_id="no_emb", reinforcement_count=5,
            last_reinforced_at=100.0,
        )
        await _seed_block(
            db_conn, block_id="with_emb", reinforcement_count=5,
            last_reinforced_at=100.0, embedding=_emb(1),
        )
        out = await fetch_recent_reinforced_embeddings(
            db_conn, window_hours=1000.0, min_reinforcement=0,
            top_n=10, current_active_hours=200.0,
        )
        assert len(out) == 1

    async def test_excludes_inactive_blocks(self, db_conn):
        await _seed_block(
            db_conn, block_id="archived", reinforcement_count=5,
            last_reinforced_at=100.0, embedding=_emb(1), status="archived",
        )
        await _seed_block(
            db_conn, block_id="active", reinforcement_count=5,
            last_reinforced_at=100.0, embedding=_emb(2),
        )
        out = await fetch_recent_reinforced_embeddings(
            db_conn, window_hours=1000.0, min_reinforcement=0,
            top_n=10, current_active_hours=200.0,
        )
        assert len(out) == 1


# ── DB helpers — fetch_constitutional_blocks ─────────────────────────────────


class TestFetchConstitutionalBlocks:
    async def test_returns_tagged_only(self, db_conn):
        await _seed_block(
            db_conn, block_id="const-a", tags=[CONSTITUTIONAL_TAG],
            embedding=_emb(1),
        )
        await _seed_block(
            db_conn, block_id="const-b", tags=[CONSTITUTIONAL_TAG, "extra"],
            embedding=_emb(2),
        )
        await _seed_block(db_conn, block_id="ord", embedding=_emb(3))

        out = await fetch_constitutional_blocks(db_conn)
        ids = {b["id"] for b in out}
        assert ids == {"const-a", "const-b"}

    async def test_no_cooldown_returns_all(self, db_conn):
        await _seed_block(
            db_conn, block_id="c1", tags=[CONSTITUTIONAL_TAG],
        )
        # Add an amendment row dated NOW
        await db_conn.execute(text(
            "INSERT INTO block_amendments "
            "(block_id, pre_content, post_content, drift_score, acceptor) "
            "VALUES ('c1', 'old', 'new', 0.5, 'agent')"
        ))
        out = await fetch_constitutional_blocks(
            db_conn, exclude_recent_amendments_within_hours=None,
        )
        assert len(out) == 1

    async def test_excludes_recent_amendments_cooldown(self, db_conn):
        await _seed_block(
            db_conn, block_id="recent", tags=[CONSTITUTIONAL_TAG],
        )
        await _seed_block(
            db_conn, block_id="clean", tags=[CONSTITUTIONAL_TAG],
        )
        # 'recent' amended just now — within any positive cooldown window.
        await db_conn.execute(text(
            "INSERT INTO block_amendments "
            "(block_id, pre_content, post_content, drift_score, acceptor) "
            "VALUES ('recent', 'old', 'new', 0.5, 'agent')"
        ))
        out = await fetch_constitutional_blocks(
            db_conn,
            exclude_recent_amendments_within_hours=24.0,
            current_active_hours=999.0,
        )
        ids = {b["id"] for b in out}
        assert ids == {"clean"}

    async def test_reverted_amendments_do_not_block(self, db_conn):
        """A reverted amendment is not a real recent edit — block should be eligible."""
        await _seed_block(
            db_conn, block_id="c1", tags=[CONSTITUTIONAL_TAG],
        )
        await db_conn.execute(text(
            "INSERT INTO block_amendments "
            "(block_id, pre_content, post_content, drift_score, acceptor, "
            "reverted_at) "
            "VALUES ('c1', 'old', 'new', 0.5, 'agent', CURRENT_TIMESTAMP)"
        ))
        out = await fetch_constitutional_blocks(
            db_conn, exclude_recent_amendments_within_hours=24.0,
        )
        assert len(out) == 1

    async def test_status_filter(self, db_conn):
        await _seed_block(
            db_conn, block_id="active", tags=[CONSTITUTIONAL_TAG],
        )
        await _seed_block(
            db_conn, block_id="archived", tags=[CONSTITUTIONAL_TAG],
            status="archived",
        )
        out = await fetch_constitutional_blocks(
            db_conn, include_status=("active",),
        )
        ids = {b["id"] for b in out}
        assert ids == {"active"}

    async def test_empty_status_returns_empty(self, db_conn):
        await _seed_block(
            db_conn, block_id="c1", tags=[CONSTITUTIONAL_TAG],
        )
        out = await fetch_constitutional_blocks(db_conn, include_status=())
        assert out == []
