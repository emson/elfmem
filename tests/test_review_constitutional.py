"""Tests for review_constitutional() — drift-driven amendment proposals (v0.18).

All tests use in-memory SQLite + MockLLMService + MockEmbeddingService per
``tests/CLAUDE.md``. The orchestration's partial-failure path is exercised
via the ``amendment_raise_for`` knob on MockLLMService.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text

from elfmem.adapters.mock import MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig, ReviewConfig
from elfmem.db.queries import add_tags, embedding_to_bytes, insert_block
from elfmem.operations.review import CONSTITUTIONAL_TAG

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_review_config(**overrides: object) -> ReviewConfig:
    """Build a ReviewConfig tuned for fast tests. Defaults match production
    *shape* but lower thresholds where the test would otherwise be tedious.
    """
    defaults: dict[str, object] = {
        "drift_threshold": 0.35,
        "min_recent_reinforced_blocks": 3,  # test-sized history
        "window_hours": 10_000.0,
        "min_reinforcement": 0,
        "top_n": 50,
        "cooldown_hours": 90.0 * 24.0,
        "max_proposals": 5,
        "min_block_evidence": 0.0,    # disabled by default for simpler tests
        "min_age_days": 0.0,          # disabled by default for simpler tests
    }
    defaults.update(overrides)
    return ReviewConfig(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def aligned_vec() -> np.ndarray:
    v = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture
def opposed_vec() -> np.ndarray:
    v = np.array([-1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    return v / np.linalg.norm(v)


async def _seed(
    conn,
    *,
    block_id: str,
    content: str = "content",
    embedding: np.ndarray | None = None,
    reinforcement_count: int = 0,
    last_reinforced_at: float = 0.0,
    success_count: float = 1.0,
    failure_count: float = 1.0,
    tags: list[str] | None = None,
    created_at_days_ago: float = 0.0,
    summary: str | None = None,
) -> None:
    await insert_block(
        conn,
        block_id=block_id,
        content=content,
        category="knowledge",
        source="test",
        status="active",
        last_reinforced_at=last_reinforced_at,
    )
    updates: dict[str, object] = {
        "rc": reinforcement_count,
        "sc": success_count,
        "fc": failure_count,
        "id": block_id,
    }
    set_clauses = (
        "reinforcement_count = :rc, "
        "success_count = :sc, failure_count = :fc, "
        "confidence = :sc / (:sc + :fc)"
    )
    if embedding is not None:
        updates["emb"] = embedding_to_bytes(embedding)
        set_clauses += ", embedding = :emb"
    if summary is not None:
        updates["sm"] = summary
        set_clauses += ", summary = :sm"
    if created_at_days_ago > 0:
        ts = (datetime.now(UTC) - timedelta(days=created_at_days_ago)).isoformat()
        updates["ca"] = ts
        set_clauses += ", created_at = :ca"
    await conn.execute(
        text(f"UPDATE blocks SET {set_clauses} WHERE id = :id"), updates,
    )
    if tags:
        await add_tags(conn, block_id, tags)


async def _seed_recent_history(
    conn, *, count: int, base_vec: np.ndarray, prefix: str = "ord",
) -> None:
    """Seed `count` recently-reinforced non-constitutional blocks aligned with base_vec."""
    for i in range(count):
        jitter = np.zeros_like(base_vec)
        jitter[1] = 0.01 * (i + 1)
        vec = base_vec + jitter
        vec = (vec / np.linalg.norm(vec)).astype(np.float32)
        await _seed(
            conn,
            block_id=f"{prefix}-{i}",
            content=f"recent activity #{i}",
            embedding=vec,
            reinforcement_count=3,
            last_reinforced_at=100.0 + i,
            summary=f"Recent activity summary #{i}.",
        )


@pytest.fixture
async def system(test_engine, mock_embedding) -> MemorySystem:
    mock_llm = MockLLMService()
    # Test config: permissive evidence/age floors so tests can target a single
    # behaviour at a time. Production defaults are validated separately in
    # tests that override these explicitly.
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


# ── Cold start ───────────────────────────────────────────────────────────────


class TestColdStart:
    async def test_empty_corpus_returns_insufficient_history(self, system):
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=20,
        )
        assert result.insufficient_history is True
        assert result.proposals == []
        # No LLM calls
        assert system._llm.propose_amendment_calls == 0  # type: ignore[attr-defined]

    async def test_below_min_recent_blocks_is_insufficient(
        self, system, aligned_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=19, base_vec=aligned_vec)
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=20,
        )
        assert result.insufficient_history is True
        assert result.reviewed_count == 0
        assert system._llm.propose_amendment_calls == 0  # type: ignore[attr-defined]


# ── No drift ─────────────────────────────────────────────────────────────────


class TestNoDrift:
    async def test_stable_identity_no_proposals(
        self, system, aligned_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            # Constitutional block aligned with recent activity
            await _seed(
                conn, block_id="const-1",
                content="Constitutional principle aligned with activity.",
                embedding=aligned_vec,
                tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        assert result.insufficient_history is False
        assert result.proposals == []
        assert result.reviewed_count == 1

    async def test_drift_below_threshold_no_proposals(
        self, system, aligned_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            # Slightly off-axis: drift ≈ 0.05, well below 0.35
            mild = np.array([0.99, 0.14, 0.0, 0.0], dtype=np.float32)
            mild = mild / np.linalg.norm(mild)
            await _seed(
                conn, block_id="const-mild",
                embedding=mild, tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
            drift_threshold=0.35,
        )
        assert result.proposals == []
        assert result.skipped_count == 1


# ── Drift detected ───────────────────────────────────────────────────────────


class TestDriftDetected:
    async def test_proposes_amendment_for_drifted_block(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="drifted",
                content="An outdated principle.",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        assert len(result.proposals) == 1
        prop = result.proposals[0]
        assert prop.block_id == "drifted"
        assert prop.drift_score > 0.9  # opposed_vec → ~1.0
        assert prop.original_content == "An outdated principle."

    async def test_proposals_sorted_by_drift_desc(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            # Two drifted blocks at different drift levels.
            orthogonal = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
            await _seed(
                conn, block_id="moderate",
                embedding=orthogonal, tags=[CONSTITUTIONAL_TAG],
            )
            await _seed(
                conn, block_id="severe",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        assert len(result.proposals) == 2
        assert result.proposals[0].block_id == "severe"
        assert result.proposals[1].block_id == "moderate"
        assert result.proposals[0].drift_score >= result.proposals[1].drift_score

    async def test_respects_max_proposals_cap(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            for i in range(10):
                # Slight per-block variation so drift values aren't identical.
                v = opposed_vec.copy()
                v[1] = 0.001 * i
                v = (v / np.linalg.norm(v)).astype(np.float32)
                await _seed(
                    conn, block_id=f"drift-{i}",
                    embedding=v, tags=[CONSTITUTIONAL_TAG],
                )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
            max_proposals=3,
        )
        assert len(result.proposals) == 3
        assert result.reviewed_count == 10
        # 10 eligible - 3 returned = 7 skipped (overflow)
        assert result.skipped_count == 7

    async def test_respects_min_block_evidence(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="cold",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
                success_count=0.5, failure_count=0.5,  # α+β = 1.0
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
            min_block_evidence=2.0,
        )
        assert result.proposals == []
        assert result.skipped_count == 1

    async def test_respects_min_age_days(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="young",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
                success_count=5.0, failure_count=1.0,
                created_at_days_ago=1.0,
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
            min_age_days=30.0,
        )
        assert result.proposals == []
        assert result.skipped_count == 1

    async def test_respects_cooldown(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="recently_amended",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
            await conn.execute(text(
                "INSERT INTO block_amendments "
                "(block_id, pre_content, post_content, drift_score, acceptor) "
                "VALUES ('recently_amended', 'old', 'new', 0.5, 'agent')"
            ))
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
            cooldown_hours=24.0,
        )
        assert result.proposals == []
        # Cooldown excludes the block before review even sees it.
        assert result.reviewed_count == 0


# ── LLM failure path ─────────────────────────────────────────────────────────


class TestLLMFailure:
    async def test_continues_on_single_llm_failure(
        self, test_engine, mock_embedding, aligned_vec, opposed_vec,
    ):
        mock_llm = MockLLMService(amendment_raise_for=["FAILING_BLOCK"])
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=ElfmemConfig(
                memory=MemoryConfig(inbox_threshold=3),
                review=_make_review_config(),
            ),
        )
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="ok-1",
                content="A constitutional principle.",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
            await _seed(
                conn, block_id="ok-2",
                content="Another principle FAILING_BLOCK here.",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        assert len(result.proposals) == 1
        assert result.proposals[0].block_id == "ok-1"
        assert result.failed_proposal_count == 1

    async def test_all_llm_failures_returns_no_proposals_no_exception(
        self, test_engine, mock_embedding, aligned_vec, opposed_vec,
    ):
        mock_llm = MockLLMService(amendment_raise_for=["principle"])
        system = MemorySystem(
            engine=test_engine,
            llm_service=mock_llm,
            embedding_service=mock_embedding,
            config=ElfmemConfig(
                memory=MemoryConfig(inbox_threshold=3),
                review=_make_review_config(),
            ),
        )
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="a",
                content="principle A",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
            await _seed(
                conn, block_id="b",
                content="principle B",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        assert result.proposals == []
        assert result.failed_proposal_count == 2
        assert result.insufficient_history is False


# ── Idempotency ──────────────────────────────────────────────────────────────


class TestIdempotency:
    async def test_review_is_idempotent_under_mock_llm(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            await _seed(
                conn, block_id="d1",
                content="Drifted principle one.",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
            await _seed(
                conn, block_id="d2",
                content="Drifted principle two.",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
        r1 = await system.review_constitutional(min_recent_reinforced_blocks=3)
        r2 = await system.review_constitutional(min_recent_reinforced_blocks=3)
        assert [(p.block_id, p.proposed_content) for p in r1.proposals] == (
            [(p.block_id, p.proposed_content) for p in r2.proposals]
        )


# ── Skip counting ────────────────────────────────────────────────────────────


class TestSkipCounting:
    async def test_skipped_count_matches_filtered(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            # 1 below-threshold (kept-aligned)
            await _seed(
                conn, block_id="aligned-const",
                embedding=aligned_vec, tags=[CONSTITUTIONAL_TAG],
            )
            # 1 over-threshold (proposed)
            await _seed(
                conn, block_id="drift-const",
                embedding=opposed_vec, tags=[CONSTITUTIONAL_TAG],
            )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3,
        )
        assert result.reviewed_count == 2
        assert len(result.proposals) == 1
        assert result.skipped_count == 1


# ── Per-call overrides ───────────────────────────────────────────────────────


class TestPerCallOverrides:
    async def test_drift_threshold_override(
        self, system, aligned_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            orthogonal = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
            await _seed(
                conn, block_id="ortho",
                embedding=orthogonal, tags=[CONSTITUTIONAL_TAG],
            )
        # drift ≈ 0.5 — above 0.35 default
        r_low = await system.review_constitutional(
            min_recent_reinforced_blocks=3, drift_threshold=0.35,
        )
        assert len(r_low.proposals) == 1
        # ...but below 0.9 override
        r_high = await system.review_constitutional(
            min_recent_reinforced_blocks=3, drift_threshold=0.9,
        )
        assert r_high.proposals == []

    async def test_max_proposals_override(
        self, system, aligned_vec, opposed_vec,
    ):
        async with system._engine.begin() as conn:
            await _seed_recent_history(conn, count=5, base_vec=aligned_vec)
            for i in range(4):
                v = opposed_vec.copy()
                v[1] = 0.001 * i
                v = (v / np.linalg.norm(v)).astype(np.float32)
                await _seed(
                    conn, block_id=f"d-{i}", embedding=v,
                    tags=[CONSTITUTIONAL_TAG],
                )
        result = await system.review_constitutional(
            min_recent_reinforced_blocks=3, max_proposals=2,
        )
        assert len(result.proposals) == 2
