"""outcome() — domain-agnostic Bayesian confidence update from observed outcomes."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    accelerate_block_decay,
    get_block,
    get_blocks_by_tag_pattern,
    get_peer,
    insert_block_outcome,
    reinforce_blocks,
    update_block_outcome,
    update_edge,
    update_peer_trust,
    upsert_outcome_edge,
)
from elfmem.types import OutcomeResult, SkippedBlock

# The tag that confers PERMANENT decay is exactly the tag that should confer
# protection from ordinary scoring: `determine_decay_tier` maps
# `self/constitutional` to PERMANENT 1:1, so tier-based and tag-based
# protection are the same set, and the tag is the cheaper one to resolve.
#
# Why protect at all: a constitutional block describes *how the agent
# reasons*, not a bet it placed. A losing trade says nothing about whether
# "a pattern in one regime is a hypothesis in another" is a good principle;
# judging that is what `review_constitutional` is for, and it is deliberately
# manual. The library already draws this exact distinction one level down --
# `record_use()` refuses to touch confidence because use is evidence of
# relevance, never of truth. This is the same reasoning applied to outcomes.
#
# The damage was measured, not assumed: one losing trade moved a principle
# from confidence 0.50 to 0.275, and six took it to 0.114. Decay is already
# safe (`accelerate_block_decay` skips PERMANENT), so the harm is entirely
# through the posterior -- which feeds ranking, which decides what survives a
# budget-bound SELF frame. The end state is an agent whose constitution
# quietly stops being injected.
_PROTECTED_TAG = "self/constitutional"

# Weight scale for outcome-confirmed edges.
# Outcome confirmation is stronger evidence than geometric similarity —
# these blocks co-produced a real-world result. signal=PERFECT → 0.80, signal=GOOD → 0.60.
OUTCOME_EDGE_WEIGHT_SCALE = 0.8


def _validate_signal(signal: float) -> None:
    if not (0.0 <= signal <= 1.0):
        raise ValueError(f"signal must be in [0.0, 1.0], got {signal!r}")


def _validate_weight(weight: float) -> None:
    if weight <= 0.0:
        raise ValueError(f"weight must be > 0.0, got {weight!r}")


def compute_bayesian_update_ab(
    success_count: float,
    failure_count: float,
    signal: float,
    weight: float = 1.0,
) -> tuple[float, float, float]:
    """Pure Beta-Binomial update on sufficient statistics.

    USE WHEN: an outcome has been observed for a block; you need new (α, β).
    DON'T USE WHEN: you only have ``confidence``, not stored (α, β) — derive
        them first: ``alpha = confidence * total``, ``beta = (1 - confidence)
        * total`` for whatever ``total`` (prior strength) applies, then call
        this. Every block stores (α, β) directly since v0.17, so this
        conversion is a migration-only path, not a normal call shape.
    COST: pure arithmetic, no I/O.
    RETURNS: ``(new_success, new_failure, new_confidence)`` — α and β are the
        canonical Beta sufficient statistics, ``new_confidence`` is the
        denormalised view ``α / (α + β)``.
    NEXT: persist all three with ``update_block_outcome``.
    """
    new_success = success_count + signal * weight
    new_failure = failure_count + (1.0 - signal) * weight
    new_confidence = new_success / (new_success + new_failure)
    return new_success, new_failure, new_confidence


async def record_outcome(
    conn: AsyncConnection,
    *,
    block_ids: list[str],
    signal: float,
    weight: float,
    source: str,
    current_active_hours: float,
    reinforce_threshold: float,
    edge_reinforce_delta: float = 0.10,
    penalize_threshold: float = 0.20,
    penalty_factor: float = 2.0,
    lambda_ceiling: float = 0.050,
    allow_constitutional: bool = False,
) -> OutcomeResult:
    """Apply a normalised outcome signal to a set of blocks via Bayesian update.

    Validates signal and weight, fetches each block, skips non-active ones,
    folds the outcome into the Beta posterior's sufficient statistics (α, β),
    writes an audit record, and reinforces blocks + edges for positive signals.
    For low signals (< penalize_threshold), also accelerates block decay.

    Args:
        block_ids: IDs of blocks that contributed to the outcome.
        signal: Normalised quality signal in [0.0, 1.0].
        weight: Observation weight (> 0.0). Higher = faster convergence.
        source: Label for audit trail (e.g. "brier", "test_pass", "csat").
        current_active_hours: Current system clock for reinforcement timestamps.
        reinforce_threshold: Minimum signal to trigger reinforcement (from config).
        penalize_threshold: Signal below which decay is accelerated (from config).
        penalty_factor: decay_lambda multiplier per penalization (from config).
        lambda_ceiling: Maximum decay_lambda after penalization (from config).
        allow_constitutional: Score `self/constitutional` blocks too. Off by
            default -- see `_PROTECTED_TAG`. Pass True only when the outcome
            genuinely judges the principle itself rather than a task that
            happened to recall it; `mind_outcome` passes it because it scores
            one deliberately named block, not whatever a decision touched.
    """
    _validate_signal(signal)
    _validate_weight(weight)

    if not block_ids:
        return OutcomeResult(blocks_updated=0, mean_confidence_delta=0.0, edges_reinforced=0)

    # One indexed tag query rather than per-block tag lookups in the loop.
    protected: set[str] = set()
    if not allow_constitutional:
        tagged = await get_blocks_by_tag_pattern(conn, _PROTECTED_TAG)
        protected = set(block_ids) & set(tagged)

    updated_ids: list[str] = []
    confidence_deltas: list[float] = []
    skipped: list[SkippedBlock] = []

    for block_id in block_ids:
        if block_id in protected:
            skipped.append(SkippedBlock(block_id, "constitutional"))
            continue
        block = await get_block(conn, block_id)
        # Each of these used to be the same silent `continue`, so a caller
        # could not tell a typo'd id from a block that simply had not been
        # consolidated yet. The inbox case is the damaging one: remember()
        # then outcome() before a dream() is an ordinary sequence whenever
        # work resolves faster than the consolidation cycle, and the signal
        # was being dropped with nothing said.
        if block is None:
            skipped.append(SkippedBlock(block_id, "unmatched"))
            continue
        if block["status"] != "active":
            skipped.append(SkippedBlock(
                block_id,
                "pending_inbox" if block["status"] == "inbox" else "archived",
            ))
            continue

        confidence_before = float(block["confidence"])
        success_count = float(block.get("success_count") or 0.0)
        failure_count = float(block.get("failure_count") or 0.0)

        new_success, new_failure, confidence_after = compute_bayesian_update_ab(
            success_count=success_count,
            failure_count=failure_count,
            signal=signal,
            weight=weight,
        )

        await update_block_outcome(
            conn,
            block_id=block_id,
            new_success_count=new_success,
            new_failure_count=new_failure,
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

    # Deliberately the FULL block_ids, not updated_ids: a peer letter that
    # accreted `self/constitutional` (7 of 40 blocks on a real instance) is
    # skipped above, but trust is a judgement about the *peer's contribution*,
    # not about the block's standing as a principle. Filtering here instead
    # would silently disable trust evolution for exactly those peers.
    await _update_peer_trust(conn, block_ids, signal, weight)

    edges_reinforced = 0
    outcome_edges_created = 0
    if updated_ids and signal > reinforce_threshold:
        await reinforce_blocks(conn, updated_ids, current_active_hours)

        if len(updated_ids) > 1:
            outcome_weight = signal * OUTCOME_EDGE_WEIGHT_SCALE
            for from_id, to_id in _canonical_pairs(updated_ids):
                created = await upsert_outcome_edge(
                    conn, from_id=from_id, to_id=to_id, weight=outcome_weight,
                    last_active_hours=current_active_hours,
                )
                if created:
                    outcome_edges_created += 1
                else:
                    edges_reinforced += 1
                    await update_edge(
                        conn,
                        from_id=from_id,
                        to_id=to_id,
                        reinforce_delta=signal * edge_reinforce_delta,
                        current_active_hours=current_active_hours,
                    )

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
        outcome_edges_created=outcome_edges_created,
        skipped=skipped,
    )


TRUST_DELTA_SCALE = 0.05
TRUST_POSITIVE_THRESHOLD = 0.7
TRUST_NEGATIVE_THRESHOLD = 0.3


async def _update_peer_trust(
    conn: AsyncConnection,
    block_ids: list[str],
    signal: float,
    weight: float,
) -> None:
    """Update trust scores for peers whose blocks received outcome closure."""
    peers_seen: set[str] = set()
    for block_id in block_ids:
        block = await get_block(conn, block_id)
        if block is None:
            continue
        source_peer = block.get("source_peer")
        if not source_peer or source_peer in peers_seen:
            continue
        peers_seen.add(source_peer)

        peer = await get_peer(conn, source_peer)
        if peer is None:
            continue

        delta = TRUST_DELTA_SCALE * weight
        if signal >= TRUST_POSITIVE_THRESHOLD:
            new_trust = min(1.0, peer["trust"] + delta)
        elif signal <= TRUST_NEGATIVE_THRESHOLD:
            new_trust = max(0.0, peer["trust"] - delta)
        else:
            continue
        await update_peer_trust(conn, source_peer, new_trust)


def _canonical_pairs(block_ids: list[str]) -> list[tuple[str, str]]:
    """Return all canonical (min, max) pairs for co-retrieved blocks."""
    pairs = []
    for i, a in enumerate(block_ids):
        for b in block_ids[i + 1:]:
            pairs.append((min(a, b), max(a, b)))
    return pairs
