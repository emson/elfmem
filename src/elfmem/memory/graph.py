"""Graph operations — centrality, 1-hop expansion, co-retrieval edge reinforcement."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries

EDGE_REINFORCE_DELTA = 0.10
EDGE_WEIGHT_CAP = 1.0


async def compute_centrality(
    conn: AsyncConnection,
    block_ids: list[str],
) -> dict[str, float]:
    """Compute normalised weighted-degree centrality for a set of blocks.

    centrality(block) = weighted_degree(block) / max_weighted_degree

    Returns dict mapping block_id → centrality in [0.0, 1.0].
    Blocks with no edges have centrality 0.0.
    """
    if not block_ids:
        return {}
    degrees = await queries.get_weighted_degree(conn, block_ids)
    max_degree = max(degrees.values(), default=0.0)
    if max_degree == 0.0:
        return {bid: 0.0 for bid in block_ids}
    return {bid: deg / max_degree for bid, deg in degrees.items()}


async def expand_1hop(
    conn: AsyncConnection,
    seed_ids: list[str],
) -> list[str]:
    """Get 1-hop neighbour block IDs from the graph, active only.

    Returns block IDs connected to any seed via an edge,
    excluding the seeds themselves.
    """
    if not seed_ids:
        return []
    neighbour_ids = await queries.get_neighbours(conn, seed_ids)
    if not neighbour_ids:
        return []
    active = await queries.get_active_blocks(conn)
    active_ids = {b["id"] for b in active}
    return [nid for nid in neighbour_ids if nid in active_ids]


async def reinforce_co_retrieved_edges(
    conn: AsyncConnection,
    block_ids: list[str],
) -> int:
    """Reinforce edges between co-retrieved blocks.

    For each pair of block_ids that share an edge, increments reinforcement_count.
    Returns count of edges reinforced.
    """
    if len(block_ids) < 2:
        return 0

    # Build canonical pairs set
    canonical_pairs: set[tuple[str, str]] = set()
    for i in range(len(block_ids)):
        for j in range(i + 1, len(block_ids)):
            fa = min(block_ids[i], block_ids[j])
            ta = max(block_ids[i], block_ids[j])
            canonical_pairs.add((fa, ta))

    # Find which pairs have existing edges
    existing: set[tuple[str, str]] = set()
    seen_blocks: set[str] = set()
    for bid in block_ids:
        if bid in seen_blocks:
            continue
        seen_blocks.add(bid)
        for edge in await queries.get_edges_for_block(conn, bid):
            existing.add((edge["from_id"], edge["to_id"]))

    to_reinforce = [p for p in canonical_pairs if p in existing]
    if to_reinforce:
        await queries.reinforce_edges(conn, to_reinforce)
    return len(to_reinforce)
