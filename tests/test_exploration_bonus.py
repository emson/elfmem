"""Tests for the v0.17 exploration bonus on compute_score.

The exploration term is ``EXPLORATION_KAPPA × sqrt(Var(Beta(α, β)))``, applied
uniformly across all frames. The variance ``α·β / ((α+β)²·(α+β+1))`` self-
extinguishes as evidence accumulates: maximal on a Jeffreys prior, negligible
on a mature block — so no frame gating is required (the math handles it).

The pinned numeric values are the substance of this commit and the regression
fixture for any future κ tuning. See ADR 0002 and
docs/plans/plan_memory_scoring.md.
"""

from __future__ import annotations

import math

import pytest

from elfmem.adapters.mock import MockEmbeddingService, MockLLMService
from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig, MemoryConfig
from elfmem.db.engine import create_test_engine
from elfmem.db.queries import seed_builtin_data
from elfmem.scoring import (
    ATTENTION_WEIGHTS,
    EXPLORATION_KAPPA,
    compute_score,
)

# ── The κ constant ───────────────────────────────────────────────────────────


def test_kappa_is_zero_point_zero_five() -> None:
    """Pin κ = 0.05 (ADR 0002). Defensible constant, NOT a config knob."""
    assert EXPLORATION_KAPPA == 0.05


# ── Pure compute_score behaviour ─────────────────────────────────────────────


def _zero_base_kwargs() -> dict[str, float]:
    """Base-zero kwargs so the bonus is the entire score."""
    return dict(
        similarity=0.0, confidence=0.0, recency=0.0,
        centrality=0.0, reinforcement=0.0,
    )


def test_fresh_block_exploration_bonus_value() -> None:
    """α=β=0.5 → variance = 0.125; bonus = 0.05·√0.125 ≈ 0.017677669."""
    score = compute_score(
        **_zero_base_kwargs(),
        weights=ATTENTION_WEIGHTS,
        success_count=0.5,
        failure_count=0.5,
    )
    expected = 0.05 * math.sqrt(0.125)
    assert abs(score - expected) < 1e-9
    assert abs(score - 0.01767767) < 1e-6  # the doc-pinned value


def test_mature_block_exploration_bonus_negligible() -> None:
    """α=β=50 → variance ≈ 0.00248; bonus ≈ 0.0025 — natural fadeout."""
    score = compute_score(
        **_zero_base_kwargs(),
        weights=ATTENTION_WEIGHTS,
        success_count=50.0,
        failure_count=50.0,
    )
    # Var = 2500 / (10000 × 101) ≈ 0.00247525; bonus = 0.05 × √0.00247525.
    expected = 0.05 * math.sqrt(2500.0 / (10000.0 * 101.0))
    assert abs(score - expected) < 1e-9
    assert abs(score - 0.002487) < 1e-5  # the doc-pinned value


def test_high_variance_block_outranks_low_variance() -> None:
    """Same confidence and base, different (α, β): the uncertain one wins."""
    base_kwargs = dict(
        similarity=0.5, confidence=0.5, recency=0.5,
        centrality=0.5, reinforcement=0.5,
        weights=ATTENTION_WEIGHTS,
    )
    high_variance = compute_score(
        **base_kwargs, success_count=0.5, failure_count=0.5,
    )
    low_variance = compute_score(
        **base_kwargs, success_count=50.0, failure_count=50.0,
    )
    assert high_variance > low_variance
    # Same confidence (0.5) feeds the base in both calls; the gap is the
    # exploration delta — about 0.015.
    assert (high_variance - low_variance) > 0.01


def test_exploration_bonus_monotonic_in_uncertainty() -> None:
    """For fixed mean p=0.5, decreasing (α+β) strictly increases the bonus.

    Property test across several total-mass values.
    """
    base = _zero_base_kwargs()
    previous = -math.inf
    for total in [200.0, 50.0, 20.0, 10.0, 5.0, 2.0, 1.0]:
        alpha = total / 2.0
        beta = total / 2.0
        score = compute_score(
            **base, weights=ATTENTION_WEIGHTS,
            success_count=alpha, failure_count=beta,
        )
        assert score > previous, f"non-monotonic at total={total}"
        previous = score


def test_compute_score_backward_compatible_defaults() -> None:
    """Callers that don't pass (α, β) get the Jeffreys bonus, not a crash."""
    score_default = compute_score(
        similarity=0.5, confidence=0.5, recency=0.5,
        centrality=0.5, reinforcement=0.5,
        weights=ATTENTION_WEIGHTS,
    )
    score_explicit = compute_score(
        similarity=0.5, confidence=0.5, recency=0.5,
        centrality=0.5, reinforcement=0.5,
        weights=ATTENTION_WEIGHTS,
        success_count=0.5, failure_count=0.5,
    )
    assert abs(score_default - score_explicit) < 1e-9


# ── Integration: retrieval picks the more uncertain block ────────────────────


@pytest.fixture
async def system_with_engine():
    engine = await create_test_engine()
    async with engine.begin() as conn:
        await seed_builtin_data(conn)
    llm = MockLLMService(default_alignment=0.65)
    embedding = MockEmbeddingService(dimensions=64)
    cfg = ElfmemConfig(memory=MemoryConfig(inbox_threshold=3))
    system = MemorySystem(
        engine=engine, llm_service=llm, embedding_service=embedding, config=cfg,
    )
    yield system, engine
    await engine.dispose()


async def test_retrieval_includes_exploration_in_ranking(system_with_engine):
    """Two blocks with similar retrieval features but different evidence
    masses rank in expected order (uncertain block first).

    We learn + consolidate two blocks (so they get real embeddings via the
    mock embedding service), then surgically rewrite their (α, β) to set up
    the variance contrast. The mock embedding produces deterministic vectors
    derived from the content, so the same query has the same cosine to both
    blocks if their contents are chosen carefully.
    """
    from sqlalchemy import text
    system, engine = system_with_engine

    await system.learn("alpha topic block content")
    await system.learn("beta topic block content")
    await system.learn("filler block to flush inbox")
    await system.consolidate()

    # Identify the two we care about by content prefix, then force one to
    # be mature (α=β=50) and the other to stay fresh (α=β=0.5).
    async with engine.begin() as conn:
        rows = (await conn.execute(text(
            "SELECT id, content FROM blocks WHERE status='active' "
            "AND content LIKE '%topic block content%'"
        ))).fetchall()
        ids_by_content = {r[1]: r[0] for r in rows}
        fresh_id = ids_by_content["alpha topic block content"]
        mature_id = ids_by_content["beta topic block content"]
        await conn.execute(text(
            "UPDATE blocks SET success_count=0.5, failure_count=0.5, "
            "confidence=0.5 WHERE id=:id"
        ), {"id": fresh_id})
        await conn.execute(text(
            "UPDATE blocks SET success_count=50.0, failure_count=50.0, "
            "confidence=0.5 WHERE id=:id"
        ), {"id": mature_id})

    result = await system.recall("topic block content", top_k=5, frame="attention")
    scores_by_id = {b.id: b.score for b in result}
    assert fresh_id in scores_by_id, "fresh block missing from recall"
    assert mature_id in scores_by_id, "mature block missing from recall"
    # The exploration bonus is the only differentiator between the two
    # blocks' scores (same confidence, same recency window, similar
    # similarity from the mock embedding). The fresh block must outscore
    # the mature one.
    assert scores_by_id[fresh_id] > scores_by_id[mature_id]
