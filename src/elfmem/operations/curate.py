"""curate() — scheduled maintenance: archive decayed blocks, prune edges, reinforce top-N."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    get_active_blocks,
    get_config,
    get_weighted_degree,
    prune_weak_edges,
    reinforce_blocks,
    set_config,
    update_block_status,
)
from elfmem.memory.blocks import determine_decay_tier
from elfmem.scoring import (
    SELF_WEIGHTS,
    compute_recency,
    compute_score,
    log_normalise_reinforcement,
)
from elfmem.types import CurateResult

PRUNE_THRESHOLD = 0.05
EDGE_PRUNE_THRESHOLD = 0.10
CURATE_REINFORCE_TOP_N = 5
CURATE_INTERVAL_HOURS = 40.0
# Blocks above this percentile of weighted degree are bridge-protected from archival
BRIDGE_PROTECTION_QUANTILE = 0.80


async def should_curate(
    conn: AsyncConnection,
    current_active_hours: float,
    *,
    curate_interval_hours: float = CURATE_INTERVAL_HOURS,
) -> bool:
    """Return True if curate() should run.

    Returns True when:
    - last_curate_at has never been set (first run), OR
    - elapsed active hours since last curate >= curate_interval_hours
    """
    last_str = await get_config(conn, "last_curate_at")
    if last_str is None:
        return True
    last = float(last_str)
    return (current_active_hours - last) >= curate_interval_hours


async def curate(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    prune_threshold: float = PRUNE_THRESHOLD,
    edge_prune_threshold: float = EDGE_PRUNE_THRESHOLD,
    reinforce_top_n: int = CURATE_REINFORCE_TOP_N,
) -> CurateResult:
    """Run maintenance on the memory corpus.

    Three phases:
    1. Archive blocks whose recency has dropped below prune_threshold
    2. Delete edges whose weight is below edge_prune_threshold
    3. Reinforce the top-N blocks by composite score

    Updates last_curate_at in system_config after completion.
    """
    archived = await _archive_decayed_blocks(conn, current_active_hours, prune_threshold)
    edges_pruned = await prune_weak_edges(conn, edge_prune_threshold)
    reinforced = await _reinforce_top_blocks(conn, current_active_hours, reinforce_top_n)

    await set_config(conn, "last_curate_at", str(current_active_hours))

    return CurateResult(archived=archived, edges_pruned=edges_pruned, reinforced=reinforced)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _archive_decayed_blocks(
    conn: AsyncConnection,
    current_active_hours: float,
    prune_threshold: float,
) -> int:
    """Archive active blocks whose recency has fallen below prune_threshold.

    Bridge protection: blocks in the top 20% by weighted degree are structurally
    important connectors and are exempt from archival even when their recency
    drops below the threshold. This preserves graph connectivity across knowledge
    clusters that might otherwise be silently severed.
    """
    active = await get_active_blocks(conn)
    if not active:
        return 0

    # Compute weighted degree for all active blocks
    all_ids = [b["id"] for b in active]
    degrees = await get_weighted_degree(conn, all_ids)

    # Bridge threshold: 80th percentile of non-zero weighted degrees.
    # Blocks with degree=0 (isolated) are never bridge-protected.
    nonzero_degs = sorted(d for d in degrees.values() if d > 0.0)
    if nonzero_degs:
        p_idx = min(
            int(len(nonzero_degs) * BRIDGE_PROTECTION_QUANTILE),
            len(nonzero_degs) - 1,
        )
        bridge_threshold = nonzero_degs[p_idx]
    else:
        bridge_threshold = 0.0  # no edges in graph — nothing to protect

    archived = 0
    for block in active:
        tags_row = await _get_tags_fast(conn, block["id"])
        tier = determine_decay_tier(tags_row, block["category"])
        hours_since = current_active_hours - float(block["last_reinforced_at"])
        recency = compute_recency(tier, hours_since)
        if recency < prune_threshold:
            # Skip archival for highly-connected bridge nodes
            if bridge_threshold > 0.0 and degrees.get(block["id"], 0.0) >= bridge_threshold:
                continue
            await update_block_status(conn, block["id"], "archived", archive_reason="decayed")
            archived += 1
    return archived


async def _get_tags_fast(conn: AsyncConnection, block_id: str) -> list[str]:
    """Fetch tags for a single block."""
    from elfmem.db.queries import get_tags
    return await get_tags(conn, block_id)


async def _reinforce_top_blocks(
    conn: AsyncConnection,
    current_active_hours: float,
    top_n: int,
) -> int:
    """Score all active blocks and reinforce the top-N."""
    active = await get_active_blocks(conn)
    if not active:
        return 0

    block_ids = [b["id"] for b in active]
    degrees = await get_weighted_degree(conn, block_ids)
    max_degree = max(degrees.values()) if degrees else 0.0
    max_reinforcement = max((b["reinforcement_count"] for b in active), default=0)

    weights = SELF_WEIGHTS.renormalized_without_similarity()

    scored: list[tuple[str, float]] = []
    for block in active:
        tags = await _get_tags_fast(conn, block["id"])
        tier = determine_decay_tier(tags, block["category"])
        hours_since = current_active_hours - float(block["last_reinforced_at"])
        recency = compute_recency(tier, hours_since)
        centrality = degrees.get(block["id"], 0.0) / max_degree if max_degree > 0 else 0.0
        reinforcement = log_normalise_reinforcement(
            block["reinforcement_count"], max_reinforcement
        )
        score = compute_score(
            similarity=0.0,
            confidence=float(block["confidence"]),
            recency=recency,
            centrality=centrality,
            reinforcement=reinforcement,
            weights=weights,
        )
        scored.append((block["id"], score))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [bid for bid, _ in scored[:top_n]]
    if top_ids:
        await reinforce_blocks(conn, top_ids, current_active_hours)
    return len(top_ids)
