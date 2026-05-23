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
    effective_centrality,
    log_normalise_reinforcement,
)
from elfmem.types import DecayTier

TOL = 0.001  # tolerance for float assertions

# v0.17: exploration bonus on the Jeffreys prior (α=β=0.5) — the default
# applied when a caller doesn't pass success_count/failure_count.
# 0.05 × sqrt(0.5×0.5 / (1×1×2)) = 0.05 × sqrt(0.125) ≈ 0.017677669529.
JEFFREYS_BONUS = 0.05 * math.sqrt(0.125)


# ---------------------------------------------------------------------------
# TC-S-001: Basic ATTENTION score
# ---------------------------------------------------------------------------
def test_attention_frame_basic_score() -> None:
    """TC-S-001: Weighted sum with ATTENTION weights = 0.625 (base) + Jeffreys bonus."""
    score = compute_score(
        similarity=0.80,
        confidence=0.70,
        recency=0.60,
        centrality=0.40,
        reinforcement=0.30,
        weights=ATTENTION_WEIGHTS,
    )
    # base: 0.35×0.80 + 0.15×0.70 + 0.25×0.60 + 0.15×0.40 + 0.10×0.30 = 0.625
    # v0.17 exploration bonus on default α=β=0.5: 0.05 × sqrt(0.125) ≈ 0.01768
    assert abs(score - (0.625 + JEFFREYS_BONUS)) < TOL


# ---------------------------------------------------------------------------
# TC-S-002: All-zero components → score = 0.0
# ---------------------------------------------------------------------------
def test_all_zeros_score_zero() -> None:
    """TC-S-002: All input signals 0.0 → composite = exploration bonus only."""
    score = compute_score(
        similarity=0.0,
        confidence=0.0,
        recency=0.0,
        centrality=0.0,
        reinforcement=0.0,
        weights=ATTENTION_WEIGHTS,
    )
    # Base = 0.0; v0.17 exploration bonus on the Jeffreys default lifts to ~0.018.
    assert abs(score - JEFFREYS_BONUS) < TOL


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


# ---------------------------------------------------------------------------
# v0.15.3: Cold-start centrality floor for fresh blocks with few edges
# ---------------------------------------------------------------------------


class TestEffectiveCentrality:
    """Cold-start centrality floor — protects fresh blocks with few edges
    from losing top-K retrieval to bedrock on centrality alone.

    See docs/plans/plan_v0.15.3_centrality_floor.md.
    """

    def test_fresh_block_with_low_centrality_gets_floor(self) -> None:
        """Brand-new block: centrality=0.0, recency=1.0 → floor=0.50."""
        assert abs(effective_centrality(raw_centrality=0.0, recency=1.0) - 0.50) < TOL

    def test_floor_decays_with_recency(self) -> None:
        """Aging fresh block: floor scales linearly with recency."""
        assert abs(effective_centrality(raw_centrality=0.0, recency=0.85) - 0.425) < TOL

    def test_floor_stops_at_recency_boundary(self) -> None:
        """At exactly recency=0.70, no floor (strict inequality)."""
        assert effective_centrality(raw_centrality=0.0, recency=0.70) == 0.0

    def test_floor_stops_below_recency_threshold(self) -> None:
        """Below recency threshold, no floor regardless of centrality."""
        assert effective_centrality(raw_centrality=0.0, recency=0.30) == 0.0
        assert effective_centrality(raw_centrality=0.05, recency=0.50) == 0.05

    def test_floor_stops_above_centrality_threshold(self) -> None:
        """Established block (raw centrality ≥ 0.10): not protected."""
        assert effective_centrality(raw_centrality=0.50, recency=1.0) == 0.50
        assert effective_centrality(raw_centrality=0.95, recency=1.0) == 0.95

    def test_block_at_centrality_boundary_unchanged(self) -> None:
        """At exactly centrality=0.10, no floor (strict inequality)."""
        assert effective_centrality(raw_centrality=0.10, recency=1.0) == 0.10

    def test_never_lowers_centrality(self) -> None:
        """Floor only raises; never lowers — score rankings shift only in
        favour of fresh blocks, never against them."""
        for raw in [0.0, 0.05, 0.09, 0.10, 0.50, 0.95]:
            for rec in [0.0, 0.30, 0.70, 0.85, 1.0]:
                assert effective_centrality(raw_centrality=raw, recency=rec) >= raw

    def test_idempotent(self) -> None:
        """Applying twice yields same result."""
        once = effective_centrality(raw_centrality=0.0, recency=1.0)
        twice = effective_centrality(raw_centrality=once, recency=1.0)
        assert once == twice

    def test_old_block_unaffected(self) -> None:
        """An old block keeps its raw centrality regardless of value."""
        assert effective_centrality(raw_centrality=0.0, recency=0.10) == 0.0
        assert effective_centrality(raw_centrality=0.50, recency=0.10) == 0.50


# ---------------------------------------------------------------------------
# v0.15.3 numerical regression — locks in the cold-start gap-closure numbers
# against the REAL scoring path (ATTENTION_WEIGHTS + compute_score +
# effective_centrality). Source: scripts/verify_v0_15_3_scoring.py.
# ---------------------------------------------------------------------------


class TestColdStartGapRegression:
    """Numerical regression fixture for v0.15.3.

    The original plan was built on a simulation that used
        ATTENTION = (sim=0.60, conf=0.15, rec=0.10, cent=0.15, reinf=0.00)
    but the shipped ATTENTION_WEIGHTS are
        (sim=0.35, conf=0.15, rec=0.25, cent=0.15, reinf=0.10).
    Recency weight is 2.5× higher and reinforcement is non-zero in production.
    These tests pin the *real* gap-closure numbers so future scoring tweaks
    can't silently re-open the cold-start hole.
    """

    # Canonical scenarios (kwargs for compute_score, minus weights).
    NEW_BLOCK = dict(similarity=0.74, confidence=0.65, recency=1.0,
                     centrality=0.0, reinforcement=0.0)
    BEDROCK_REINFORCED = dict(similarity=0.62, confidence=0.95, recency=0.70,
                              centrality=0.80, reinforcement=1.0)
    BEDROCK_NO_REINF = dict(similarity=0.62, confidence=0.95, recency=0.70,
                            centrality=0.80, reinforcement=0.0)

    def _floored(self, block: dict) -> dict:
        return {**block, "centrality": effective_centrality(
            raw_centrality=block["centrality"], recency=block["recency"],
        )}

    def test_floor_closes_exactly_0_075_of_weighted_gap(self) -> None:
        """The floor raises centrality from 0.0 to 0.5 at recency=1.0, which
        at centrality_weight=0.15 closes exactly 0.075 of weighted gap."""
        pre = compute_score(**self.NEW_BLOCK, weights=ATTENTION_WEIGHTS)
        post = compute_score(**self._floored(self.NEW_BLOCK), weights=ATTENTION_WEIGHTS)
        assert abs((post - pre) - 0.075) < TOL

    def test_wide_similarity_gap_floor_surfaces_new_block(self) -> None:
        """S1: similarity 0.92 vs 0.55 — floor flips ranking from −0.0605 to +0.0145.

        Both blocks carry the Jeffreys exploration bonus (~0.018) since no
        explicit (α, β) is passed; it cancels out of the *gap*, so the
        ranking remains flipped, but both pinned scores shift by the same
        constant.
        """
        new = dict(self.NEW_BLOCK, similarity=0.92)
        bedrock = dict(self.BEDROCK_REINFORCED, similarity=0.55)
        new_score = compute_score(**self._floored(new), weights=ATTENTION_WEIGHTS)
        bedrock_score = compute_score(**bedrock, weights=ATTENTION_WEIGHTS)
        assert abs(new_score - (0.7445 + JEFFREYS_BONUS)) < TOL
        assert abs(bedrock_score - (0.7300 + JEFFREYS_BONUS)) < TOL
        assert new_score > bedrock_score

    def test_tight_gap_reinforced_bedrock_floor_INSUFFICIENT(self) -> None:
        """S1b: similarity 0.74 vs 0.62 — when bedrock has reinforcement=1.0,
        the centrality floor alone is NOT enough. Margin remains −0.073.

        This is a deliberate regression fixture: if a future change closes
        this gap (e.g. a reinforcement floor, or exploration bonus from v0.17),
        update the expected value — but the change should be intentional.
        """
        new_score = compute_score(**self._floored(self.NEW_BLOCK), weights=ATTENTION_WEIGHTS)
        bedrock_score = compute_score(**self.BEDROCK_REINFORCED, weights=ATTENTION_WEIGHTS)
        # Both carry the Jeffreys bonus (~0.018); it cancels out of the gap.
        assert abs(new_score - (0.6815 + JEFFREYS_BONUS)) < TOL
        assert abs(bedrock_score - (0.7545 + JEFFREYS_BONUS)) < TOL
        assert new_score < bedrock_score
        assert abs((new_score - bedrock_score) - (-0.0730)) < TOL

    def test_tight_gap_unreinforced_bedrock_floor_SUFFICIENT(self) -> None:
        """S1b': same tight similarity gap, but bedrock has reinforcement=0.0
        (e.g. integration-test setup). Floor surfaces new block by +0.027."""
        new_score = compute_score(**self._floored(self.NEW_BLOCK), weights=ATTENTION_WEIGHTS)
        bedrock_score = compute_score(**self.BEDROCK_NO_REINF, weights=ATTENTION_WEIGHTS)
        # Both carry the Jeffreys bonus (~0.018); it cancels out of the gap.
        assert abs(new_score - (0.6815 + JEFFREYS_BONUS)) < TOL
        assert abs(bedrock_score - (0.6545 + JEFFREYS_BONUS)) < TOL
        assert new_score > bedrock_score

    def test_floor_decays_with_recency_in_full_score(self) -> None:
        """As recency falls, the floor scales linearly until the threshold,
        then snaps to raw centrality. Verifies the cliff is at recency=0.70."""
        bedrock_score = compute_score(**self.BEDROCK_NO_REINF, weights=ATTENTION_WEIGHTS)

        def score_at(rec: float) -> float:
            floored = effective_centrality(raw_centrality=0.0, recency=rec)
            return compute_score(
                similarity=0.74, confidence=0.65, recency=rec,
                centrality=floored, reinforcement=0.0,
                weights=ATTENTION_WEIGHTS,
            )

        # At recency=1.0: wins by +0.027
        assert score_at(1.0) - bedrock_score > 0.02
        # Just above threshold (recency=0.71): floor still applies but reduced
        assert effective_centrality(raw_centrality=0.0, recency=0.71) > 0.0
        # At threshold (recency=0.70): floor stops; sharp drop
        assert effective_centrality(raw_centrality=0.0, recency=0.70) == 0.0
        # Below threshold: bedrock dominates by a widening margin
        assert score_at(0.50) < bedrock_score
        assert score_at(0.10) < score_at(0.50)

    def test_established_block_score_unchanged_by_floor(self) -> None:
        """A block with raw_centrality=0.15 (above threshold) gets no floor —
        score is identical to the no-floor baseline. Guards against the floor
        accidentally being applied to established blocks."""
        block = dict(self.NEW_BLOCK, centrality=0.15)
        raw = compute_score(**block, weights=ATTENTION_WEIGHTS)
        floored = compute_score(**self._floored(block), weights=ATTENTION_WEIGHTS)
        assert raw == floored
