"""connect() / disconnect() — agent-asserted edge operations.

Pure operation layer: validates, enforces degree caps, and writes to the
database. No session management, no breadcrumbs, no LLM calls.
The api.py layer wraps these with session context and operation recording.
"""

from __future__ import annotations

from typing import Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.exceptions import BlockNotActiveError, ConnectError, DegreeLimitError, SelfLoopError
from elfmem.memory.graph import find_displaceable_edge
from elfmem.types import (
    ConnectResult,
    DisconnectResult,
    DisplacedEdge,
    Edge,
)

# Default weights by relation type — semantic hierarchy reflecting evidence strength
_RELATION_DEFAULT_WEIGHTS: dict[str, float] = {
    "similar":     0.65,
    "co_occurs":   0.55,
    "elaborates":  0.70,
    "supports":    0.75,
    "contradicts": 0.60,
    "outcome":     0.80,
}
_DEFAULT_WEIGHT_FALLBACK = 0.65   # for unknown custom relation types


async def do_connect(
    conn: AsyncConnection,
    *,
    source: str,
    target: str,
    relation: str,
    weight: float | None,
    note: str | None,
    if_exists: Literal["reinforce", "update", "skip", "error"],
    edge_degree_cap: int,
    edge_reinforce_delta: float,
    current_active_hours: float | None,
) -> ConnectResult:
    """Core connect logic. Called by MemorySystem.connect()."""

    # 1. Self-loop guard
    if source == target:
        raise SelfLoopError(source)

    # 2. Validate both blocks exist and are active
    src_block = await queries.get_block(conn, source)
    if src_block is None or src_block.get("status") != "active":
        raise BlockNotActiveError(source)
    tgt_block = await queries.get_block(conn, target)
    if tgt_block is None or tgt_block.get("status") != "active":
        raise BlockNotActiveError(target)

    # 3. Canonical ordering
    from_id, to_id = Edge.canonical(source, target)

    # 4. Resolve weight — explicit > relation default > fallback; always clamp [0, 1]
    resolved_weight = weight if weight is not None else _RELATION_DEFAULT_WEIGHTS.get(
        relation, _DEFAULT_WEIGHT_FALLBACK
    )
    resolved_weight = max(0.0, min(1.0, resolved_weight))

    # 5. Handle existing edge
    existing = await queries.get_edge(conn, from_id, to_id)

    if existing is not None:
        if if_exists == "error":
            raise ConnectError(
                f"Edge already exists between {from_id[:8]}… and {to_id[:8]}…",
                recovery="Use if_exists='reinforce' or 'update' to modify existing edges.",
            )
        if if_exists == "skip":
            return ConnectResult(
                source_id=source,
                target_id=target,
                relation=existing.get("relation_type", "similar"),
                weight=existing["weight"],
                action="skipped",
            )
        if if_exists == "update":
            await queries.update_edge(
                conn,
                from_id=from_id,
                to_id=to_id,
                relation_type=relation,
                weight=resolved_weight if weight is not None else None,
                note=note,
                current_active_hours=current_active_hours,
            )
            return ConnectResult(
                source_id=source,
                target_id=target,
                relation=relation,
                weight=resolved_weight,
                action="updated",
                note=note,
            )
        # if_exists == "reinforce" (default)
        new_weight = min(existing["weight"] + edge_reinforce_delta, 1.0)
        await queries.update_edge(
            conn,
            from_id=from_id,
            to_id=to_id,
            reinforce_delta=edge_reinforce_delta,
            current_active_hours=current_active_hours,
        )
        return ConnectResult(
            source_id=source,
            target_id=target,
            relation=existing.get("relation_type", "similar"),
            weight=new_weight,
            action="reinforced",
        )

    # 6. Degree cap check — only on new edge creation
    displaced_edge: DisplacedEdge | None = None
    for check_id in [from_id, to_id]:
        block_edges = await queries.get_edges_for_block(conn, check_id)
        if len(block_edges) >= edge_degree_cap:
            displaceable = await find_displaceable_edge(conn, check_id)
            if displaceable is None:
                raise DegreeLimitError(check_id, edge_degree_cap)
            displaced_edge = DisplacedEdge(
                from_id=displaceable["from_id"],
                to_id=displaceable["to_id"],
                relation_type=displaceable.get("relation_type", "similar"),
                weight=displaceable["weight"],
            )
            await queries.delete_edge(conn, displaceable["from_id"], displaceable["to_id"])
            break  # displace once even if both endpoints are at cap

    # 7. Insert the new agent-asserted edge
    await queries.insert_agent_edge(
        conn,
        from_id=from_id,
        to_id=to_id,
        weight=resolved_weight,
        relation_type=relation,
        note=note,
        current_active_hours=current_active_hours,
    )

    return ConnectResult(
        source_id=source,
        target_id=target,
        relation=relation,
        weight=resolved_weight,
        action="created",
        note=note,
        displaced_edge=displaced_edge,
    )


async def do_disconnect(
    conn: AsyncConnection,
    *,
    source: str,
    target: str,
    guard_relation: str | None,
) -> DisconnectResult:
    """Core disconnect logic. Called by MemorySystem.disconnect()."""
    from_id, to_id = Edge.canonical(source, target)
    existing = await queries.get_edge(conn, from_id, to_id)

    if existing is None:
        return DisconnectResult(
            source_id=source,
            target_id=target,
            action="not_found",
        )

    if guard_relation is not None and existing.get("relation_type") != guard_relation:
        return DisconnectResult(
            source_id=source,
            target_id=target,
            action="guarded",
        )

    await queries.delete_edge(conn, from_id, to_id)
    return DisconnectResult(
        source_id=source,
        target_id=target,
        action="removed",
        removed_relation=existing.get("relation_type"),
        removed_weight=existing["weight"],
    )
