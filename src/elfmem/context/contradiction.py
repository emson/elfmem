"""Contradiction suppression — remove lower-confidence member of contradicting pairs."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.types import ScoredBlock


async def suppress_contradictions(
    conn: AsyncConnection,
    candidates: list[ScoredBlock],
) -> list[ScoredBlock]:
    """Remove blocks involved in contradictions, keeping the higher-confidence one.

    For each pair with an active contradiction record:
    - Keep the block with higher confidence
    - If confidence equal, keep the more recently reinforced one (higher recency)

    Args:
        conn: Database connection.
        candidates: Scored candidate blocks (may be oversampled).

    Returns:
        Filtered list with contradicting pairs resolved.
    """
    if len(candidates) < 2:
        return candidates

    candidate_ids = [b.id for b in candidates]
    contradiction_records = await queries.get_contradictions_for_blocks(conn, candidate_ids)

    if not contradiction_records:
        return candidates

    id_to_block: dict[str, ScoredBlock] = {b.id: b for b in candidates}
    to_remove: set[str] = set()

    for record in contradiction_records:
        a_id = record["block_a_id"]
        b_id = record["block_b_id"]

        # Only suppress if both are in our candidate set
        if a_id not in id_to_block or b_id not in id_to_block:
            continue
        if a_id in to_remove or b_id in to_remove:
            continue

        block_a = id_to_block[a_id]
        block_b = id_to_block[b_id]

        # Keep higher confidence; if equal, keep higher recency
        if block_a.confidence > block_b.confidence:
            to_remove.add(b_id)
        elif block_b.confidence > block_a.confidence:
            to_remove.add(a_id)
        else:
            # Equal confidence — keep higher recency
            if block_a.recency >= block_b.recency:
                to_remove.add(b_id)
            else:
                to_remove.add(a_id)

    return [b for b in candidates if b.id not in to_remove]
