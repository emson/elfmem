"""Constitutional review — drift detection math + DB helpers (v0.18).

Two layers:

1. **Pure math** (``compute_drift``, ``recent_self_centroid``): NumPy in,
   NumPy/float out. No I/O, no LLM, no global state. Composable and unit
   testable without fixtures.

2. **DB helpers** (``fetch_recent_reinforced_embeddings``,
   ``fetch_constitutional_blocks``): pure-read async queries that pick
   out the input rows the math needs. No mutation, no LLM.

The orchestration that composes these with an LLM amendment proposer
lives in commit 3 (``review_constitutional`` operation). This module
deliberately stops at "produce the inputs and the drift scalar"; the
"is this drift high enough to surface?" judgement is the caller's.

Defaults
--------
- ``MIN_RECENT_REINFORCED_BLOCKS = 20`` — minimum non-constitutional
  reinforced blocks needed before the centroid is meaningful enough
  to drive a constitutional review. Below this, the caller should flag
  ``insufficient_history``.
- ``CONSTITUTIONAL_TAG = "self/constitutional"`` — single source of
  truth for the tag that marks constitutional blocks.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

# ── Defaults ─────────────────────────────────────────────────────────────────

MIN_RECENT_REINFORCED_BLOCKS: int = 20
CONSTITUTIONAL_TAG: str = "self/constitutional"


# ── Pure math ────────────────────────────────────────────────────────────────


def compute_drift(
    block_embedding: np.ndarray,
    self_centroid: np.ndarray,
) -> float:
    """Drift score between a block and the agent's operational self.

    USE WHEN: Detecting whether a constitutional block has fallen out of
        alignment with the agent's recent operational reality.
    DON'T USE WHEN: Comparing two arbitrary blocks — use cosine similarity
        directly; drift is a half-rescaled cosine specifically interpreted
        as "distance from self".
    COST: O(d) NumPy dot product, where d is the embedding dimension.
    RETURNS: float in [0.0, 1.0]. 0.0 = perfectly aligned (cos = +1),
        0.5 = orthogonal (cos = 0), 1.0 = perfectly opposed (cos = -1).
    NEXT: Compare to a threshold; if above, the block is a candidate
        for amendment proposal via the LLM in ``review_constitutional``.

    Derivation:
        cos = (a · b) / (|a| |b|)        ∈ [-1, +1]
        drift = (1 - cos) / 2            ∈ [0, 1]

    Both vectors are L2-normalised internally so caller need not pre-normalise.
    """
    a = block_embedding.astype(np.float64)
    b = self_centroid.astype(np.float64)
    a_norm = float(np.linalg.norm(a))
    b_norm = float(np.linalg.norm(b))
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.5  # undefined direction → maximally uncertain
    cos = float(np.dot(a, b) / (a_norm * b_norm))
    # Numerical safety — dot product can drift outside [-1, 1] by ~1e-15.
    cos = max(-1.0, min(1.0, cos))
    return (1.0 - cos) / 2.0


def recent_self_centroid(
    block_embeddings: list[np.ndarray],
) -> np.ndarray | None:
    """Compute the unit-normalised centroid of recently-reinforced blocks.

    USE WHEN: Establishing "where the agent IS now" — the empirical
        operational identity expressed by the blocks the agent actually
        relies on day-to-day.
    DON'T USE WHEN: You need a single block's embedding (use the block
        directly), or the input list is unfiltered (the caller is
        responsible for excluding constitutional blocks first — see
        ``fetch_recent_reinforced_embeddings``).
    COST: O(N · d) — one mean + one normalisation across N vectors.
    RETURNS: np.ndarray (unit-normalised, dtype float32) representing
        the operational self centroid, OR ``None`` when input is empty
        (caller decides what insufficient_history means).
    NEXT: Pass to ``compute_drift`` together with each constitutional
        block's embedding to obtain per-block drift scores.
    """
    if not block_embeddings:
        return None
    stack = np.stack([v.astype(np.float64) for v in block_embeddings], axis=0)
    mean = stack.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        # All vectors cancelled to zero — degenerate but representable.
        # Returning the zero vector lets compute_drift fall through to 0.5.
        result: np.ndarray = mean.astype(np.float32)
        return result
    normalised: np.ndarray = (mean / norm).astype(np.float32)
    return normalised


# ── DB helpers ───────────────────────────────────────────────────────────────


async def fetch_recent_reinforced_embeddings(
    conn: AsyncConnection,
    *,
    window_hours: float,
    min_reinforcement: int,
    top_n: int,
    current_active_hours: float,
    exclude_tag: str = CONSTITUTIONAL_TAG,
) -> list[np.ndarray]:
    """Load embeddings for the recently-reinforced, non-constitutional blocks.

    USE WHEN: Building the input to ``recent_self_centroid`` — the
        operational-self prior for drift detection.
    DON'T USE WHEN: You need full block rows; this returns embeddings
        only, by design (centroid math doesn't need anything else).
    COST: One SELECT, bounded by ``top_n``. No LLM, no mutation.
    RETURNS: list of np.ndarray (float32 vectors) ordered newest first.
        Empty list means "no eligible blocks".
    NEXT: Feed into ``recent_self_centroid`` to obtain the centroid.

    Filter:
        - status = 'active'
        - reinforcement_count > min_reinforcement
        - last_reinforced_at > current_active_hours - window_hours
        - embedding IS NOT NULL
        - NOT tagged with ``exclude_tag`` (default: self/constitutional)

    Order: last_reinforced_at DESC; limit top_n.
    """
    from elfmem.db.queries import bytes_to_embedding

    if top_n <= 0:
        return []
    cutoff = current_active_hours - window_hours
    sql = """
        SELECT embedding FROM blocks
        WHERE status = 'active'
          AND reinforcement_count > :min_r
          AND last_reinforced_at > :cutoff
          AND embedding IS NOT NULL
          AND id NOT IN (
              SELECT block_id FROM block_tags WHERE tag = :exclude_tag
          )
        ORDER BY last_reinforced_at DESC
        LIMIT :top_n
    """
    rows = await conn.execute(
        text(sql),
        {
            "min_r": min_reinforcement,
            "cutoff": cutoff,
            "exclude_tag": exclude_tag,
            "top_n": top_n,
        },
    )
    return [bytes_to_embedding(r[0]) for r in rows.fetchall() if r[0] is not None]


async def fetch_constitutional_blocks(
    conn: AsyncConnection,
    *,
    include_status: tuple[str, ...] = ("active",),
    exclude_recent_amendments_within_hours: float | None = None,
    current_active_hours: float = 0.0,
    tag: str = CONSTITUTIONAL_TAG,
) -> list[dict[str, object]]:
    """Fetch constitutional blocks (tagged ``self/constitutional``).

    USE WHEN: Selecting candidates for drift evaluation in a review cycle.
    DON'T USE WHEN: You need ALL self/* blocks — constitutional is a
        strict subset (only the foundational principles tagged
        ``self/constitutional`` by setup or seed).
    COST: One join (block_tags), plus one LEFT JOIN onto block_amendments
        when the cooldown is requested. Small N (constitutional blocks
        are a handful), so negligible.
    RETURNS: list of dicts with all block columns plus the raw
        ``embedding`` bytes (None when the block has no embedding).
        Order is stable by block id.
    NEXT: For each block, decode the embedding, call ``compute_drift``,
        and (in commit 3) pass over-threshold blocks to the LLM
        amendment proposer.

    Cooldown semantics:
        When ``exclude_recent_amendments_within_hours`` is provided,
        a block is excluded if it has an *unreverted* amendment whose
        timestamp falls within the cooldown window measured from
        ``current_active_hours``. The unit is wall-clock hours
        (block_amendments.timestamp is wall-clock); we trust the
        review caller to convert active hours → wall-clock hours
        upstream if a different clock is desired. Default behaviour
        with the timestamp comparison below is "wall-clock hours
        relative to NOW", consistent with how amendments are stamped.
    """
    if not include_status:
        return []
    placeholders = ", ".join(f":st_{i}" for i in range(len(include_status)))
    params: dict[str, object] = {
        f"st_{i}": s for i, s in enumerate(include_status)
    }
    params["tag"] = tag

    cooldown_clause = ""
    if exclude_recent_amendments_within_hours is not None:
        cooldown_clause = """
          AND id NOT IN (
              SELECT block_id FROM block_amendments
              WHERE reverted_at IS NULL
                AND timestamp > datetime(
                    'now', :cooldown_offset
                )
          )
        """
        # SQLite datetime modifier: '-N hours' subtracts from NOW.
        params["cooldown_offset"] = (
            f"-{float(exclude_recent_amendments_within_hours)} hours"
        )

    sql = f"""
        SELECT * FROM blocks
        WHERE status IN ({placeholders})
          AND id IN (
              SELECT block_id FROM block_tags WHERE tag = :tag
          )
          {cooldown_clause}
        ORDER BY id ASC
    """
    rows = await conn.execute(text(sql), params)
    return [dict(row) for row in rows.mappings()]
