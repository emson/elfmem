"""Scoring module — pure Python, zero external dependencies.

This module is the first to be fully tested (12 test cases from the scoring playground).
It is treated as frozen after Step 2: changing the formula is a breaking version change.
"""

from __future__ import annotations

import math

from pydantic import BaseModel, field_validator, model_validator

from elfmem.types import DecayTier

# Decay rate constants (λ) per tier
LAMBDA: dict[DecayTier, float] = {
    DecayTier.PERMANENT: 0.00001,
    DecayTier.DURABLE: 0.001,
    DecayTier.STANDARD: 0.010,
    DecayTier.EPHEMERAL: 0.050,
}


class ScoringWeights(BaseModel):
    """Weights for the 5-component composite scoring formula.

    Invariant: all five weights must sum to 1.0 (±0.0001 tolerance).
    """

    similarity: float
    confidence: float
    recency: float
    centrality: float
    reinforcement: float

    @field_validator("similarity", "confidence", "recency", "centrality", "reinforcement")
    @classmethod
    def non_negative(cls, v: float) -> float:
        if v < 0.0:
            raise ValueError(f"Weight must be non-negative, got {v}")
        return v

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> ScoringWeights:
        total = self.similarity + self.confidence + self.recency + self.centrality
        total += self.reinforcement
        if abs(total - 1.0) > 0.0001:
            raise ValueError(f"Weights must sum to 1.0, got {total:.6f}")
        return self

    def renormalized_without_similarity(self) -> ScoringWeights:
        """Drop the similarity weight and rescale the remaining four to sum to 1.0.

        Used by SELF frame (no query) and queryless ATTENTION.
        """
        remaining = self.confidence + self.recency + self.centrality + self.reinforcement
        if remaining == 0.0:
            raise ValueError("Cannot renormalize: all non-similarity weights are zero")
        scale = 1.0 / remaining
        return ScoringWeights(
            similarity=0.0,
            confidence=self.confidence * scale,
            recency=self.recency * scale,
            centrality=self.centrality * scale,
            reinforcement=self.reinforcement * scale,
        )


# Frame weight presets (from exploration 013 / scoring playground)
SELF_WEIGHTS = ScoringWeights(
    similarity=0.10,
    confidence=0.30,
    recency=0.05,
    centrality=0.25,
    reinforcement=0.30,
)

ATTENTION_WEIGHTS = ScoringWeights(
    similarity=0.35,
    confidence=0.15,
    recency=0.25,
    centrality=0.15,
    reinforcement=0.10,
)

TASK_WEIGHTS = ScoringWeights(
    similarity=0.20,
    confidence=0.20,
    recency=0.20,
    centrality=0.20,
    reinforcement=0.20,
)


def compute_score(
    *,
    similarity: float,
    confidence: float,
    recency: float,
    centrality: float,
    reinforcement: float,
    weights: ScoringWeights,
) -> float:
    """Compute the weighted composite score for a single block.

    All input values should be in [0.0, 1.0].
    Returns a float in [0.0, 1.0].
    """
    return (
        weights.similarity * similarity
        + weights.confidence * confidence
        + weights.recency * recency
        + weights.centrality * centrality
        + weights.reinforcement * reinforcement
    )


def compute_recency(tier: DecayTier, hours_since_reinforced: float) -> float:
    """Compute exponential recency score for a block.

    Returns exp(-λ × hours_since_reinforced) ∈ (0.0, 1.0].
    hours_since_reinforced is in *active* hours (session-aware clock), not wall time.
    """
    lam = LAMBDA[tier]
    return math.exp(-lam * hours_since_reinforced)


def log_normalise_reinforcement(count: int, max_count: int) -> float:
    """Log-normalised reinforcement score.

    Returns log(1 + count) / log(1 + max_count) ∈ [0.0, 1.0].
    Returns 0.0 when max_count is 0 (empty corpus).
    """
    if max_count == 0:
        return 0.0
    return math.log(1 + count) / math.log(1 + max_count)


def compute_lambda_edge(tier_a: DecayTier, tier_b: DecayTier) -> float:
    """Compute edge decay rate from the two endpoint block tiers.

    λ_edge = min(λ_a, λ_b) × 0.5
    The edge inherits durability from the more stable block.
    """
    return min(LAMBDA[tier_a], LAMBDA[tier_b]) * 0.5
