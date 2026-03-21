"""Graph operations — centrality, 1-hop expansion, co-retrieval edge reinforcement."""

from __future__ import annotations

from typing import Any

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
    current_active_hours: float | None = None,
) -> int:
    """Reinforce edges between co-retrieved blocks.

    For each pair of block_ids that share an edge, increments reinforcement_count
    and updates last_active_hours. Returns count of edges reinforced.
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
        await queries.reinforce_edges(conn, to_reinforce, current_active_hours)
    return len(to_reinforce)


async def stage_and_promote_co_retrievals(
    conn: AsyncConnection,
    block_ids: list[str],
    staging: dict[tuple[str, str], int],
    *,
    threshold: int,
    edge_weight: float,
    current_active_hours: float,
    staging_max: int = 1000,
    session_seen: set[tuple[str, str]] | None = None,
) -> int:
    """Increment staging counts for co-retrieved pairs without existing edges.

    Promotes pairs that reach `threshold` to permanent co_occurs edges
    (origin="co_retrieval"). Promoted pairs are removed from staging.

    Only stages pairs WITHOUT an existing edge — pairs with edges are
    already reinforced by reinforce_co_retrieved_edges() before this call.

    ``session_seen`` enforces per-session deduplication: each pair contributes
    at most 1 count per begin_session() cycle, making threshold semantically mean
    "N distinct sessions" rather than "N calls." Pass the MemorySystem's
    ``_co_retrieval_session_seen`` set; it is cleared on begin_session().
    Pass None to disable deduplication (useful in tests or always-on agents
    without session boundaries).

    Returns count of edges promoted in this call.

    Must be called after reinforce_co_retrieved_edges() so that existing-edge
    check reflects the current graph state.
    """
    if len(block_ids) < 2:
        return 0

    # Build all canonical pairs from returned block IDs
    canonical_pairs: list[tuple[str, str]] = [
        (min(block_ids[i], block_ids[j]), max(block_ids[i], block_ids[j]))
        for i in range(len(block_ids))
        for j in range(i + 1, len(block_ids))
    ]

    # Batch-load existing edges (one pass per block, deduped)
    existing: set[tuple[str, str]] = set()
    seen: set[str] = set()
    for bid in block_ids:
        if bid in seen:
            continue
        seen.add(bid)
        for edge in await queries.get_edges_for_block(conn, bid):
            existing.add((edge["from_id"], edge["to_id"]))

    # Stage pairs without existing edges; promote at threshold
    promoted = 0
    for pair in canonical_pairs:
        if pair in existing:
            continue  # already connected — reinforced by reinforce_co_retrieved_edges()

        # Per-session dedup: each pair contributes at most one count per session.
        # Makes threshold mean "N distinct sessions" not "N calls in one session."
        if session_seen is not None:
            if pair in session_seen:
                continue  # already counted this pair in the current session
            session_seen.add(pair)

        count = staging.get(pair, 0) + 1
        if count >= threshold:
            await queries.insert_edge(
                conn,
                from_id=pair[0],
                to_id=pair[1],
                weight=edge_weight,
                relation_type="co_occurs",
                origin="co_retrieval",
                last_active_hours=current_active_hours,
            )
            staging.pop(pair, None)
            promoted += 1
        else:
            staging[pair] = count

    # Evict when over cap — preserve pairs closest to promotion (highest count)
    if len(staging) > staging_max:
        top = sorted(staging.items(), key=lambda x: x[1], reverse=True)[: staging_max - 100]
        staging.clear()
        staging.update(top)

    return promoted


# Displacement priority for degree-cap enforcement.
# Evict auto-created edges first; never evict agent-asserted or outcome-confirmed.
_EVICTION_ORDER: list[str] = ["similar", "co_occurs"]
_PROTECTED_RELATIONS: frozenset[str] = frozenset(
    {"elaborates", "supports", "contradicts", "outcome"}
)


async def find_displaceable_edge(
    conn: AsyncConnection,
    block_id: str,
) -> dict[str, Any] | None:
    """Find the lowest-priority displaceable edge connected to block_id.

    Returns the edge row dict to displace, or None if all edges are protected.
    Eviction order: similar → co_occurs (weakest weight first within tier).
    Never displaces: elaborates, supports, contradicts, outcome, or agent-origin edges.
    """
    block_edges = await queries.get_edges_for_block(conn, block_id)
    if not block_edges:
        return None

    candidates = [
        e for e in block_edges
        if e.get("relation_type", "similar") in _EVICTION_ORDER
        and e.get("origin", "similarity") != "agent"
    ]
    if not candidates:
        return None

    def eviction_key(e: dict[str, Any]) -> tuple[int, float]:
        relation = e.get("relation_type", "similar")
        order_idx = _EVICTION_ORDER.index(relation) if relation in _EVICTION_ORDER else 99
        return (order_idx, e["weight"])

    candidates.sort(key=eviction_key)
    return candidates[0]
