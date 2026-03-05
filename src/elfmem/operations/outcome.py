"""outcome() — domain-agnostic Bayesian confidence update from observed outcomes."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    accelerate_block_decay,
    get_block,
    insert_block_outcome,
    reinforce_blocks,
    reinforce_edges,
    update_block_outcome,
)
from elfmem.types import OutcomeResult


def _validate_signal(signal: float) -> None:
    if not (0.0 <= signal <= 1.0):
        raise ValueError(f"signal must be in [0.0, 1.0], got {signal!r}")


def _validate_weight(weight: float) -> None:
    if weight <= 0.0:
        raise ValueError(f"weight must be > 0.0, got {weight!r}")


def compute_bayesian_update(
    *,
    confidence: float,
    outcome_evidence: float,
    signal: float,
    weight: float,
    prior_strength: float,
) -> float:
    """Pure Bayesian Beta-Binomial confidence update.

    The LLM alignment score acts as a Beta prior with weight `prior_strength`.
    Each outcome signal adds weighted evidence. Prior dominates early; evidence
    dominates later (crossover around prior_strength / weight outcomes).

    Returns the new confidence in [0.0, 1.0].
    """
    total = prior_strength + outcome_evidence
    alpha = confidence * total + signal * weight
    beta = (1.0 - confidence) * total + (1.0 - signal) * weight
    return alpha / (alpha + beta)


async def record_outcome(
    conn: AsyncConnection,
    *,
    block_ids: list[str],
    signal: float,
    weight: float,
    source: str,
    current_active_hours: float,
    prior_strength: float,
    reinforce_threshold: float,
    penalize_threshold: float = 0.20,
    penalty_factor: float = 2.0,
    lambda_ceiling: float = 0.050,
) -> OutcomeResult:
    """Apply a normalised outcome signal to a set of blocks via Bayesian update.

    Validates signal and weight, fetches each block, skips non-active ones,
    computes the Beta-Binomial update, persists confidence + outcome_evidence,
    writes an audit record, and reinforces blocks + edges for positive signals.
    For low signals (< penalize_threshold), also accelerates block decay.

    Args:
        block_ids: IDs of blocks that contributed to the outcome.
        signal: Normalised quality signal in [0.0, 1.0].
        weight: Observation weight (> 0.0). Higher = faster convergence.
        source: Label for audit trail (e.g. "brier", "test_pass", "csat").
        current_active_hours: Current system clock for reinforcement timestamps.
        prior_strength: Weight of the LLM alignment prior (from config).
        reinforce_threshold: Minimum signal to trigger reinforcement (from config).
        penalize_threshold: Signal below which decay is accelerated (from config).
        penalty_factor: decay_lambda multiplier per penalization (from config).
        lambda_ceiling: Maximum decay_lambda after penalization (from config).
    """
    _validate_signal(signal)
    _validate_weight(weight)

    if not block_ids:
        return OutcomeResult(blocks_updated=0, mean_confidence_delta=0.0, edges_reinforced=0)

    updated_ids: list[str] = []
    confidence_deltas: list[float] = []

    for block_id in block_ids:
        block = await get_block(conn, block_id)
        if block is None or block["status"] != "active":
            continue

        confidence_before = float(block["confidence"])
        outcome_evidence = float(block.get("outcome_evidence") or 0.0)

        confidence_after = compute_bayesian_update(
            confidence=confidence_before,
            outcome_evidence=outcome_evidence,
            signal=signal,
            weight=weight,
            prior_strength=prior_strength,
        )
        new_evidence = outcome_evidence + weight

        await update_block_outcome(
            conn,
            block_id=block_id,
            new_confidence=confidence_after,
            new_outcome_evidence=new_evidence,
        )
        await insert_block_outcome(
            conn,
            block_id=block_id,
            signal=signal,
            weight=weight,
            source=source,
            confidence_before=confidence_before,
            confidence_after=confidence_after,
        )

        updated_ids.append(block_id)
        confidence_deltas.append(confidence_after - confidence_before)

    edges_reinforced = 0
    if updated_ids and signal > reinforce_threshold:
        await reinforce_blocks(conn, updated_ids, current_active_hours)

        if len(updated_ids) > 1:
            pairs = _canonical_pairs(updated_ids)
            await reinforce_edges(conn, pairs)
            edges_reinforced = len(pairs)

    blocks_penalized = 0
    if updated_ids and signal < penalize_threshold:
        penalized = await accelerate_block_decay(
            conn,
            block_ids=updated_ids,
            penalty_factor=penalty_factor,
            lambda_ceiling=lambda_ceiling,
        )
        blocks_penalized = len(penalized)

    blocks_updated = len(updated_ids)
    mean_delta = sum(confidence_deltas) / blocks_updated if blocks_updated else 0.0
    return OutcomeResult(
        blocks_updated=blocks_updated,
        mean_confidence_delta=mean_delta,
        edges_reinforced=edges_reinforced,
        blocks_penalized=blocks_penalized,
    )


def _canonical_pairs(block_ids: list[str]) -> list[tuple[str, str]]:
    """Return all canonical (min, max) pairs for co-retrieved blocks."""
    pairs = []
    for i, a in enumerate(block_ids):
        for b in block_ids[i + 1:]:
            pairs.append((min(a, b), max(a, b)))
    return pairs
