"""Scoring module test suite — 12 test cases from sim/playgrounds/scoring/scoring.md"""

import math

import pytest
from pydantic import ValidationError

from elfmem.scoring import (
    ATTENTION_WEIGHTS,
    LAMBDA,
    SELF_WEIGHTS,
    TASK_WEIGHTS,
    ScoringWeights,
    compute_lambda_edge,
    compute_recency,
    compute_score,
    log_normalise_reinforcement,
)
from elfmem.types import DecayTier

TOL = 0.001  # tolerance for float assertions


# ---------------------------------------------------------------------------
# TC-S-001: Basic ATTENTION score
# ---------------------------------------------------------------------------
def test_attention_frame_basic_score() -> None:
    """TC-S-001: Weighted sum with ATTENTION weights = 0.625"""
    score = compute_score(
        similarity=0.80,
        confidence=0.70,
        recency=0.60,
        centrality=0.40,
        reinforcement=0.30,
        weights=ATTENTION_WEIGHTS,
    )
    # 0.35×0.80 + 0.15×0.70 + 0.25×0.60 + 0.15×0.40 + 0.10×0.30
    # = 0.280 + 0.105 + 0.150 + 0.060 + 0.030 = 0.625
    assert abs(score - 0.625) < TOL


# ---------------------------------------------------------------------------
# TC-S-002: All-zero components → score = 0.0
# ---------------------------------------------------------------------------
def test_all_zeros_score_zero() -> None:
    """TC-S-002: All input signals 0.0 → composite score 0.0"""
    score = compute_score(
        similarity=0.0,
        confidence=0.0,
        recency=0.0,
        centrality=0.0,
        reinforcement=0.0,
        weights=ATTENTION_WEIGHTS,
    )
    assert abs(score - 0.0) < TOL


# ---------------------------------------------------------------------------
# TC-S-003: Weights must sum to 1.0
# ---------------------------------------------------------------------------
def test_weights_must_sum_to_one_raises() -> None:
    """TC-S-003: ScoringWeights raises ValidationError if weights don't sum to 1.0"""
    with pytest.raises(ValidationError):
        ScoringWeights(
            similarity=0.30,
            confidence=0.20,
            recency=0.20,
            centrality=0.20,
            reinforcement=0.20,
            # sum = 1.10 → should raise
        )


def test_weights_summing_to_one_ok() -> None:
    """TC-S-003 (positive): Weights summing to exactly 1.0 are accepted"""
    w = ScoringWeights(
        similarity=0.20,
        confidence=0.20,
        recency=0.20,
        centrality=0.20,
        reinforcement=0.20,
    )
    total = w.similarity + w.confidence + w.recency + w.centrality + w.reinforcement
    assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# TC-S-004: Queryless renormalisation
# ---------------------------------------------------------------------------
def test_renormalization_drops_similarity() -> None:
    """TC-S-004: renormalized_without_similarity() → similarity=0.0, others scale to 1.0"""
    # ATTENTION: sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10
    # non-sim total = 0.65; scale = 1/0.65 ≈ 1.538
    # new weights: conf=0.231, rec=0.385, cent=0.231, reinf=0.154
    qless = ATTENTION_WEIGHTS.renormalized_without_similarity()
    assert abs(qless.similarity - 0.0) < TOL
    total = qless.confidence + qless.recency + qless.centrality + qless.reinforcement
    assert abs(total - 1.0) < TOL
    assert abs(qless.confidence - 0.231) < TOL
    assert abs(qless.recency - 0.385) < TOL
    assert abs(qless.centrality - 0.231) < TOL
    assert abs(qless.reinforcement - 0.154) < TOL


# ---------------------------------------------------------------------------
# TC-S-005: SELF frame ignores similarity (similarity weight = 0.10, not dominant)
# ---------------------------------------------------------------------------
def test_self_weights_configuration() -> None:
    """TC-S-005: SELF frame weights — reinforcement + confidence dominate"""
    assert SELF_WEIGHTS.similarity == 0.10
    assert SELF_WEIGHTS.reinforcement == 0.30
    assert SELF_WEIGHTS.confidence == 0.30
    assert SELF_WEIGHTS.recency == 0.05
    assert SELF_WEIGHTS.centrality == 0.25


# ---------------------------------------------------------------------------
# TC-S-006: Standard tier recency at various durations
# ---------------------------------------------------------------------------
def test_recency_standard_at_zero() -> None:
    """TC-S-006a: recency at 0 hours = 1.0 (just reinforced)"""
    r = compute_recency(DecayTier.STANDARD, 0.0)
    assert abs(r - 1.0) < TOL


def test_recency_standard_at_100h() -> None:
    """TC-S-006b: standard tier (λ=0.010) at 100h = exp(-1.0) ≈ 0.368"""
    r = compute_recency(DecayTier.STANDARD, 100.0)
    assert abs(r - math.exp(-0.010 * 100)) < TOL


def test_recency_ephemeral_decays_faster() -> None:
    """TC-S-006c: ephemeral (λ=0.050) decays faster than standard (λ=0.010) at same duration"""
    r_eph = compute_recency(DecayTier.EPHEMERAL, 50.0)
    r_std = compute_recency(DecayTier.STANDARD, 50.0)
    assert r_eph < r_std


def test_recency_permanent_stays_high() -> None:
    """TC-S-006d: permanent tier (λ=0.00001) barely decays even at 10000 active hours"""
    r = compute_recency(DecayTier.PERMANENT, 10000.0)
    assert r > 0.90


# ---------------------------------------------------------------------------
# TC-S-007: TASK weights — all components equal (0.20 each)
# ---------------------------------------------------------------------------
def test_task_weights_equal() -> None:
    """TC-S-007: TASK frame has equal weights (0.20 each)"""
    assert abs(TASK_WEIGHTS.similarity - 0.20) < TOL
    assert abs(TASK_WEIGHTS.confidence - 0.20) < TOL
    assert abs(TASK_WEIGHTS.recency - 0.20) < TOL
    assert abs(TASK_WEIGHTS.centrality - 0.20) < TOL
    assert abs(TASK_WEIGHTS.reinforcement - 0.20) < TOL


# ---------------------------------------------------------------------------
# TC-S-008: High similarity dominates ATTENTION vs low similarity
# ---------------------------------------------------------------------------
def test_high_similarity_dominates_attention() -> None:
    """TC-S-008: Block with higher similarity scores higher in ATTENTION frame"""
    score_high_sim = compute_score(
        similarity=0.95,
        confidence=0.50,
        recency=0.50,
        centrality=0.50,
        reinforcement=0.50,
        weights=ATTENTION_WEIGHTS,
    )
    score_low_sim = compute_score(
        similarity=0.20,
        confidence=0.50,
        recency=0.50,
        centrality=0.50,
        reinforcement=0.50,
        weights=ATTENTION_WEIGHTS,
    )
    assert score_high_sim > score_low_sim


# ---------------------------------------------------------------------------
# TC-S-009: Negative weight rejected
# ---------------------------------------------------------------------------
def test_negative_weight_raises() -> None:
    """TC-S-009: Negative weight raises ValidationError"""
    with pytest.raises(ValidationError):
        ScoringWeights(
            similarity=-0.10,
            confidence=0.40,
            recency=0.30,
            centrality=0.20,
            reinforcement=0.20,
        )


# ---------------------------------------------------------------------------
# TC-S-010: Log-normalised reinforcement
# ---------------------------------------------------------------------------
def test_log_normalise_reinforcement_basic() -> None:
    """TC-S-010a: log_normalise(5, 10) = log(6)/log(11)"""
    expected = math.log(6) / math.log(11)
    assert abs(log_normalise_reinforcement(5, 10) - expected) < TOL


def test_log_normalise_reinforcement_max() -> None:
    """TC-S-010b: max count → score = 1.0"""
    assert abs(log_normalise_reinforcement(10, 10) - 1.0) < TOL


def test_log_normalise_reinforcement_zero_max() -> None:
    """TC-S-010c: empty corpus (max=0) → 0.0"""
    assert log_normalise_reinforcement(0, 0) == 0.0


def test_log_normalise_reinforcement_zero_count() -> None:
    """TC-S-010d: count=0 with non-zero max → 0.0"""
    assert log_normalise_reinforcement(0, 5) == 0.0


# ---------------------------------------------------------------------------
# TC-S-011: λ_edge derived from endpoint tiers
# ---------------------------------------------------------------------------
def test_lambda_edge_durable_standard() -> None:
    """TC-S-011: λ_edge = min(λ_durable, λ_standard) × 0.5 = 0.001×0.5 = 0.0005"""
    lam_edge = compute_lambda_edge(DecayTier.DURABLE, DecayTier.STANDARD)
    assert abs(lam_edge - 0.0005) < 1e-6


def test_lambda_edge_inherits_more_stable() -> None:
    """TC-S-011b: edge λ is always ≤ min of both endpoint λ values"""
    lam_edge = compute_lambda_edge(DecayTier.EPHEMERAL, DecayTier.PERMANENT)
    assert lam_edge <= LAMBDA[DecayTier.PERMANENT]
    assert lam_edge <= LAMBDA[DecayTier.EPHEMERAL]


# ---------------------------------------------------------------------------
# TC-S-012: Same block scores differently per frame
# ---------------------------------------------------------------------------
def test_same_block_different_scores_per_frame() -> None:
    """TC-S-012: ATTENTION > TASK > SELF for a query-relevant block with high similarity"""
    # A block that is query-relevant (high similarity) but low on identity signals
    kwargs = dict(
        similarity=0.90,
        confidence=0.50,
        recency=0.50,
        centrality=0.30,
        reinforcement=0.20,
    )
    score_attention = compute_score(**kwargs, weights=ATTENTION_WEIGHTS)
    score_task = compute_score(**kwargs, weights=TASK_WEIGHTS)
    score_self = compute_score(**kwargs, weights=SELF_WEIGHTS)

    # High similarity → ATTENTION rewards it most
    assert score_attention > score_task
    # TASK is balanced; SELF penalises high-sim, low-reinforcement content
    assert score_task > score_self
