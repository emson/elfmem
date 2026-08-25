"""Tests for accept_amendment / revert_amendment / list_amendments (v0.18).

All tests use in-memory SQLite + MockLLMService + MockEmbeddingService per
``tests/CLAUDE.md``. Verify content updates, audit trail, embedding refresh,
frame-cache invalidation, exception paths, and the explicit one-step revert
semantics.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig, ReviewConfig
from elfmem.db.queries import (
    add_tags,
    bytes_to_embedding,
    embedding_to_bytes,
    insert_block,
)
from elfmem.exceptions import (
    AmendmentAlreadyReverted,
    AmendmentNotFound,
    BlockNotFound,
)
from elfmem.operations.review import CONSTITUTIONAL_TAG
from elfmem.types import FrameResult

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_review_config(**overrides: object) -> ReviewConfig:
    defaults: dict[str, object] = {
        "drift_threshold": 0.35,
        "min_recent_reinforced_blocks": 3,
        "window_hours": 10_000.0,
        "min_reinforcement": 0,
        "top_n": 50,
        "cooldown_hours": 90.0 * 24.0,
        "max_proposals": 5,
        "min_block_evidence": 0.0,
        "min_age_days": 0.0,
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)  # type: ignore[arg-type]


async def _seed_constitutional(
    conn,
    *,
    block_id: str,
    content: str,
    embedding: np.ndarray,
    success_count: float = 3.0,
    failure_count: float = 1.0,
    reinforcement_count: int = 4,
    last_reinforced_at: float = 50.0,
    summary: str | None = "Stale pre-amendment summary.",
    last_scored_at: str | None = "2026-04-01T00:00:00+00:00",
) -> None:
    await insert_block(
        conn,
        block_id=block_id,
        content=content,
        category="self",
        source="setup",
        status="active",
        last_reinforced_at=last_reinforced_at,
    )
    await conn.execute(
        text(
            "UPDATE blocks SET "
            "reinforcement_count = :rc, success_count = :sc, "
            "failure_count = :fc, "
            "confidence = :sc / (:sc + :fc), "
            "embedding = :emb, summary = :sm, last_scored_at = :ls "
            "WHERE id = :id"
        ),
        {
            "rc": reinforcement_count,
            "sc": success_count,
            "fc": failure_count,
            "emb": embedding_to_bytes(embedding),
            "sm": summary,
            "ls": last_scored_at,
            "id": block_id,
        },
    )
    await add_tags(conn, block_id, [CONSTITUTIONAL_TAG])


@pytest.fixture
async def system(test_engine, mock_embedding) -> MemorySystem:
    mock_llm = MockLLMService()
    cfg = ElfmemConfig(
        memory=MemoryConfig(inbox_threshold=3),
        review=_make_review_config(),
    )
    return MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
        config=cfg,
    )


async def _read_block(engine, block_id: str) -> dict:
    from elfmem.db.queries import get_block
    async with engine.connect() as conn:
        block = await get_block(conn, block_id)
    assert block is not None, f"block {block_id} should exist"
    return block


async def _read_amendments(engine, block_id: str | None = None) -> list[dict]:
    async with engine.connect() as conn:
        if block_id is None:
            rows = await conn.execute(text(
                "SELECT * FROM block_amendments ORDER BY id ASC"
            ))
        else:
            rows = await conn.execute(text(
                "SELECT * FROM block_amendments WHERE block_id = :b "
                "ORDER BY id ASC"
            ), {"b": block_id})
        return [dict(r) for r in rows.mappings()]


def _aligned() -> np.ndarray:
    return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)


# ── accept_amendment ─────────────────────────────────────────────────────────


class TestAcceptAmendment:
    async def test_updates_content(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Old principle.",
                embedding=_aligned(),
            )
        await system.accept_amendment("c1", "New principle.", "test rationale")
        block = await _read_block(system._engine, "c1")
        assert block["content"] == "New principle."

    async def test_regenerates_embedding(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Old principle.",
                embedding=_aligned(),
            )
        pre = await _read_block(system._engine, "c1")
        pre_emb = bytes_to_embedding(pre["embedding"])

        await system.accept_amendment("c1", "Entirely different content.", None)

        post = await _read_block(system._engine, "c1")
        post_emb = bytes_to_embedding(post["embedding"])
        assert not np.array_equal(pre_emb, post_emb)

    async def test_clears_last_scored_at(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Old.", embedding=_aligned(),
            )
        await system.accept_amendment("c1", "New content.", None)
        block = await _read_block(system._engine, "c1")
        assert block["last_scored_at"] is None

    async def test_clears_summary(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Old.", embedding=_aligned(),
            )
        await system.accept_amendment("c1", "New content.", None)
        block = await _read_block(system._engine, "c1")
        assert block["summary"] is None

    async def test_creates_audit_row(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Old principle.",
                embedding=_aligned(), summary="old summary",
            )
        result = await system.accept_amendment(
            "c1", "New principle.", "evidence said X",
            drift_score=0.55, acceptor="user",
        )
        rows = await _read_amendments(system._engine, "c1")
        assert len(rows) == 1
        row = rows[0]
        assert row["block_id"] == "c1"
        assert row["pre_content"] == "Old principle."
        assert row["post_content"] == "New principle."
        assert row["pre_summary"] == "old summary"
        assert row["post_summary"] is None
        assert row["drift_score"] == pytest.approx(0.55)
        assert row["rationale"] == "evidence said X"
        assert row["acceptor"] == "user"
        assert row["reverted_at"] is None
        assert row["id"] == result.amendment_id

    async def test_preserves_alpha_beta(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
                success_count=7.5, failure_count=2.5,
            )
        await system.accept_amendment("c1", "y", None)
        block = await _read_block(system._engine, "c1")
        assert block["success_count"] == pytest.approx(7.5)
        assert block["failure_count"] == pytest.approx(2.5)
        # confidence view should match α/(α+β) = 0.75
        assert block["confidence"] == pytest.approx(0.75, abs=1e-6)

    async def test_preserves_reinforcement_count(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
                reinforcement_count=11,
            )
        await system.accept_amendment("c1", "y", None)
        block = await _read_block(system._engine, "c1")
        assert block["reinforcement_count"] == 11

    async def test_preserves_last_reinforced_at(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
                last_reinforced_at=123.5,
            )
        await system.accept_amendment("c1", "y", None)
        block = await _read_block(system._engine, "c1")
        assert block["last_reinforced_at"] == pytest.approx(123.5)

    async def test_invalidates_frame_cache(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        # Seed the cache through its public API so this test does not depend
        # on the internal key shape (which is (frame, top_k) since v0.17.1).
        sentinel = FrameResult(text="stale", blocks=[], frame_name="self")
        system._frame_cache.set("self", sentinel, ttl_seconds=3600, top_k=5)
        await system.accept_amendment("c1", "y", None)
        assert system._frame_cache.get("self", 5) is None

    async def test_idempotent_block_content_double_accept(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Old.", embedding=_aligned(),
            )
        await system.accept_amendment("c1", "Same new.", "r1")
        await system.accept_amendment("c1", "Same new.", "r2")
        block = await _read_block(system._engine, "c1")
        assert block["content"] == "Same new."
        rows = await _read_amendments(system._engine, "c1")
        assert len(rows) == 2  # two audit rows
        # First captured the original; second captured the already-amended content.
        assert rows[0]["pre_content"] == "Old."
        assert rows[1]["pre_content"] == "Same new."

    async def test_raises_block_not_found(self, system):
        with pytest.raises(BlockNotFound) as exc:
            await system.accept_amendment("nonexistent-id", "anything", None)
        assert exc.value.recovery
        assert "block_id" in exc.value.recovery

    async def test_uses_provided_drift_when_given(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        await system.accept_amendment("c1", "y", None, drift_score=0.87)
        rows = await _read_amendments(system._engine, "c1")
        assert rows[0]["drift_score"] == pytest.approx(0.87)

    async def test_recomputes_drift_when_none(self, system, mock_embedding):
        """Without a drift_score arg, accept_amendment queries the centroid."""
        async with system._engine.begin() as conn:
            # Seed enough recent activity for centroid to be computable
            base = _aligned()
            for i in range(5):
                v = base.copy()
                v[1] = 0.01 * (i + 1)
                v = (v / np.linalg.norm(v)).astype(np.float32)
                await insert_block(
                    conn, block_id=f"ord-{i}", content=f"r{i}",
                    category="knowledge", source="t", status="active",
                    last_reinforced_at=100.0 + i,
                )
                await conn.execute(text(
                    "UPDATE blocks SET reinforcement_count=3, embedding=:e "
                    "WHERE id=:i"
                ), {"e": embedding_to_bytes(v), "i": f"ord-{i}"})
            # Constitutional block opposed → high drift
            opp = -_aligned()
            await _seed_constitutional(
                conn, block_id="c1", content="opposite principle",
                embedding=opp,
            )
        await system.accept_amendment("c1", "new", None)
        rows = await _read_amendments(system._engine, "c1")
        # Drift was computed (not 0.0 from short-circuit)
        assert rows[0]["drift_score"] > 0.5


# ── revert_amendment ─────────────────────────────────────────────────────────


class TestRevertAmendment:
    async def test_restores_pre_content(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Original principle.",
                embedding=_aligned(),
            )
        accept = await system.accept_amendment(
            "c1", "New principle.", "r",
        )
        await system.revert_amendment(accept.amendment_id)
        block = await _read_block(system._engine, "c1")
        assert block["content"] == "Original principle."

    async def test_regenerates_embedding(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Original.",
                embedding=_aligned(),
            )
        accept = await system.accept_amendment("c1", "Edit.", None)
        after_accept = await _read_block(system._engine, "c1")
        await system.revert_amendment(accept.amendment_id)
        after_revert = await _read_block(system._engine, "c1")
        a = bytes_to_embedding(after_accept["embedding"])
        b = bytes_to_embedding(after_revert["embedding"])
        assert not np.array_equal(a, b)

    async def test_marks_reverted_at(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        accept = await system.accept_amendment("c1", "y", None)
        await system.revert_amendment(accept.amendment_id)
        rows = await _read_amendments(system._engine, "c1")
        assert rows[0]["reverted_at"] is not None

    async def test_clears_last_scored_at(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        accept = await system.accept_amendment("c1", "y", None)
        # Stamp a non-null last_scored_at after accept to prove revert clears it
        async with system._engine.begin() as conn:
            await conn.execute(text(
                "UPDATE blocks SET last_scored_at = '2026-05-01T00:00:00' "
                "WHERE id = 'c1'"
            ))
        await system.revert_amendment(accept.amendment_id)
        block = await _read_block(system._engine, "c1")
        assert block["last_scored_at"] is None

    async def test_clears_summary(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        accept = await system.accept_amendment("c1", "y", None)
        async with system._engine.begin() as conn:
            await conn.execute(text(
                "UPDATE blocks SET summary = 'after-accept summary' "
                "WHERE id = 'c1'"
            ))
        await system.revert_amendment(accept.amendment_id)
        block = await _read_block(system._engine, "c1")
        assert block["summary"] is None

    async def test_invalidates_frame_cache(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        accept = await system.accept_amendment("c1", "y", None)
        sentinel = FrameResult(text="stale", blocks=[], frame_name="self")
        system._frame_cache.set("self", sentinel, ttl_seconds=3600, top_k=5)
        await system.revert_amendment(accept.amendment_id)
        assert system._frame_cache.get("self", 5) is None

    async def test_raises_amendment_not_found(self, system):
        with pytest.raises(AmendmentNotFound) as exc:
            await system.revert_amendment(99999)
        assert exc.value.recovery

    async def test_raises_already_reverted(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
            )
        accept = await system.accept_amendment("c1", "y", None)
        await system.revert_amendment(accept.amendment_id)
        with pytest.raises(AmendmentAlreadyReverted) as exc:
            await system.revert_amendment(accept.amendment_id)
        assert exc.value.recovery

    async def test_one_step_undo(self, system):
        """A1: Original → V1. A2: V1 → V2. A3: V2 → V3.

        Reverting A3 restores V2, not Original.
        """
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="Original.",
                embedding=_aligned(),
            )
        a1 = await system.accept_amendment("c1", "V1", "r1")
        await system.accept_amendment("c1", "V2", "r2")
        a3 = await system.accept_amendment("c1", "V3", "r3")

        await system.revert_amendment(a3.amendment_id)
        block = await _read_block(system._engine, "c1")
        assert block["content"] == "V2"

        # A1 still intact; reverting it would go back to Original.
        rows = await _read_amendments(system._engine, "c1")
        a1_row = next(r for r in rows if r["id"] == a1.amendment_id)
        assert a1_row["reverted_at"] is None
        assert a1_row["pre_content"] == "Original."

    async def test_does_not_resurrect_alpha_beta(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="x", embedding=_aligned(),
                success_count=4.0, failure_count=2.0,
            )
        accept = await system.accept_amendment("c1", "y", None)
        # Manually change α, β between accept and revert; revert must not
        # rewind sufficient stats — content edits are separate from outcomes.
        async with system._engine.begin() as conn:
            await conn.execute(text(
                "UPDATE blocks SET success_count = 5.5, failure_count = 2.5, "
                "confidence = 5.5/8.0 WHERE id = 'c1'"
            ))
        await system.revert_amendment(accept.amendment_id)
        block = await _read_block(system._engine, "c1")
        assert block["success_count"] == pytest.approx(5.5)
        assert block["failure_count"] == pytest.approx(2.5)


# ── list_amendments ──────────────────────────────────────────────────────────


class TestListAmendments:
    async def test_all_blocks_newest_first(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="a", content="A0", embedding=_aligned(),
            )
            await _seed_constitutional(
                conn, block_id="b", content="B0", embedding=_aligned(),
            )
        await system.accept_amendment("a", "A1", None)
        await system.accept_amendment("b", "B1", None)
        await system.accept_amendment("a", "A2", None)
        recs = await system.list_amendments()
        assert len(recs) == 3
        # Three amendments, ordered newest first by timestamp DESC, id DESC.
        ids = [r.id for r in recs]
        assert ids == sorted(ids, reverse=True)

    async def test_filtered_by_block_id(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="a", content="A", embedding=_aligned(),
            )
            await _seed_constitutional(
                conn, block_id="b", content="B", embedding=_aligned(),
            )
        await system.accept_amendment("a", "A1", None)
        await system.accept_amendment("b", "B1", None)
        await system.accept_amendment("a", "A2", None)
        recs = await system.list_amendments(block_id="a")
        assert len(recs) == 2
        assert all(r.block_id == "a" for r in recs)

    async def test_respects_limit(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="a", content="A0", embedding=_aligned(),
            )
        for i in range(5):
            await system.accept_amendment("a", f"A{i + 1}", None)
        recs = await system.list_amendments(limit=3)
        assert len(recs) == 3

    async def test_includes_reverted(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="a", content="A0", embedding=_aligned(),
            )
        accept = await system.accept_amendment("a", "A1", None)
        await system.revert_amendment(accept.amendment_id)
        recs = await system.list_amendments(block_id="a")
        assert len(recs) == 1
        assert recs[0].reverted_at is not None

    async def test_empty_for_unknown_block(self, system):
        recs = await system.list_amendments(block_id="does-not-exist")
        assert recs == []


# ── Integration / round-trip ─────────────────────────────────────────────────


class TestIntegration:
    async def test_review_accept_then_re_review_skips_cooled_block(
        self, test_engine, mock_embedding,
    ):
        """After accept, the amended block is in cooldown — not re-proposed.

        Other drifted blocks still surface.
        """
        mock_llm = MockLLMService()
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=ElfmemConfig(
                memory=MemoryConfig(inbox_threshold=3),
                review=_make_review_config(
                    cooldown_hours=24.0 * 30.0,  # 30d cooldown is plenty
                ),
            ),
        )
        async with system._engine.begin() as conn:
            base = _aligned()
            for i in range(5):
                v = base.copy()
                v[1] = 0.01 * (i + 1)
                v = (v / np.linalg.norm(v)).astype(np.float32)
                await insert_block(
                    conn, block_id=f"ord-{i}", content=f"r{i}",
                    category="knowledge", source="t", status="active",
                    last_reinforced_at=100.0 + i,
                )
                await conn.execute(text(
                    "UPDATE blocks SET reinforcement_count=3, embedding=:e "
                    "WHERE id=:i"
                ), {"e": embedding_to_bytes(v), "i": f"ord-{i}"})
            opp = -_aligned()
            await _seed_constitutional(
                conn, block_id="drift-a", content="A", embedding=opp,
            )
            await _seed_constitutional(
                conn, block_id="drift-b", content="B", embedding=opp,
            )

        first = await system.review_constitutional(min_recent_reinforced_blocks=3)
        ids_first = {p.block_id for p in first.proposals}
        assert ids_first == {"drift-a", "drift-b"}

        await system.accept_amendment("drift-a", "A-new", None)

        second = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        ids_second = {p.block_id for p in second.proposals}
        assert "drift-a" not in ids_second
        assert "drift-b" in ids_second

    async def test_amendment_does_not_break_recall(self, system):
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1",
                content="Original constitutional content.",
                embedding=_aligned(),
            )
        await system.accept_amendment(
            "c1", "Revised constitutional content.", None,
        )
        # The block is still retrievable — recall should not crash and the
        # row stays active. (Full retrieval-quality verification is out of
        # scope; this is a smoke check that the apply path didn't archive
        # or corrupt the block.)
        block = await _read_block(system._engine, "c1")
        assert block["status"] == "active"
        assert block["content"] == "Revised constitutional content."

    async def test_concurrent_amendments_serialize(self, system):
        """Two sequential accepts on the same block both land; last wins."""
        async with system._engine.begin() as conn:
            await _seed_constitutional(
                conn, block_id="c1", content="V0", embedding=_aligned(),
            )
        # SQLite serialises writes — "concurrent" here means rapid succession
        # without yielding to other DB work. Both should produce audit rows.
        await system.accept_amendment("c1", "V1", "r1")
        await system.accept_amendment("c1", "V2", "r2")
        block = await _read_block(system._engine, "c1")
        assert block["content"] == "V2"
        rows = await _read_amendments(system._engine, "c1")
        assert len(rows) == 2
        # Audit chain reflects the order of application.
        assert rows[0]["pre_content"] == "V0"
        assert rows[0]["post_content"] == "V1"
        assert rows[1]["pre_content"] == "V1"
        assert rows[1]["post_content"] == "V2"
