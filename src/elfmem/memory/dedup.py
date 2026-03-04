"""Near-duplicate detection and resolution helpers."""

from __future__ import annotations

from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    bytes_to_embedding,
    get_active_blocks_with_embeddings,
    update_block_status,
)

EXACT_DUP_THRESHOLD = 0.95
NEAR_DUP_THRESHOLD = 0.90


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two normalised vectors."""
    dot = float(np.dot(a, b))
    # Clamp to [-1, 1] to guard against floating-point drift
    return max(-1.0, min(1.0, dot))


async def find_near_duplicate(
    conn: AsyncConnection,
    candidate_vec: np.ndarray,
) -> tuple[dict[str, Any] | None, float]:
    """Find the most similar active block to candidate_vec.

    Returns (block_dict, similarity) if any active block has similarity >= NEAR_DUP_THRESHOLD,
    else (None, 0.0).
    """
    active = await get_active_blocks_with_embeddings(conn)
    best_block: dict[str, Any] | None = None
    best_sim = 0.0
    for block in active:
        if block.get("embedding") is None:
            continue
        vec = bytes_to_embedding(block["embedding"])
        sim = cosine_similarity(candidate_vec, vec)
        if sim > best_sim:
            best_sim = sim
            best_block = block
    if best_sim >= NEAR_DUP_THRESHOLD:
        return best_block, best_sim
    return None, 0.0


async def resolve_near_duplicate(
    conn: AsyncConnection,
    existing_block: dict[str, Any],
    new_block_id: str,
) -> None:
    """Archive the existing block (superseded) and let the new one proceed.

    The new block inherits nothing — it starts fresh but replaces the old one.
    """
    await update_block_status(
        conn,
        existing_block["id"],
        "archived",
        archive_reason="superseded",
    )
