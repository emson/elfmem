"""recall() — orchestrates retrieval + reinforcement side effects."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.context.contradiction import suppress_contradictions
from elfmem.context.frames import FrameCache, FrameDefinition
from elfmem.context.rendering import render_blocks
from elfmem.db import queries
from elfmem.memory.graph import reinforce_co_retrieved_edges
from elfmem.memory.retrieval import hybrid_retrieve
from elfmem.ports.services import EmbeddingService
from elfmem.types import FrameResult, ScoredBlock


async def recall(
    conn: AsyncConnection,
    *,
    embedding_svc: EmbeddingService,
    frame_def: FrameDefinition,
    query: str | None,
    current_active_hours: float,
    top_k: int = 5,
    cache: FrameCache | None = None,
) -> FrameResult:
    """Execute full retrieval with reinforcement side effects.

    Pipeline:
    1. Check cache (if frame has caching enabled)
    2. Determine effective weights (renormalize if no query)
    3. Run hybrid retrieval pipeline (pure)
    4. Apply guarantee enforcement
    5. Suppress contradictions
    6. Trim to top_k
    7. Render via template with token budget
    8. Reinforce returned blocks (side effect)
    9. Reinforce co-retrieved edges (side effect)
    10. Cache result (if applicable)
    """
    # 1. Check cache
    if cache is not None and frame_def.cache is not None:
        cached = cache.get(frame_def.name)
        if cached is not None:
            return FrameResult(
                text=cached.text,
                blocks=cached.blocks,
                frame_name=frame_def.name,
                cached=True,
            )

    # 2. Determine weights
    if query is None:
        weights = frame_def.weights.renormalized_without_similarity()
    else:
        weights = frame_def.weights

    # 3. Tag filter (first pattern only in Phase 1)
    tag_filter: str | None = None
    if frame_def.filters.tag_patterns:
        tag_filter = frame_def.filters.tag_patterns[0]

    # 4. Hybrid retrieval (pure)
    candidates = await hybrid_retrieve(
        conn,
        embedding_svc=embedding_svc,
        query=query,
        weights=weights,
        current_active_hours=current_active_hours,
        top_k=top_k,
        tag_filter=tag_filter,
        search_window_hours=frame_def.filters.search_window_hours,
    )

    # 5. Guarantee enforcement
    final_blocks = await _enforce_guarantees(
        conn,
        candidates=candidates,
        guarantee_tag_patterns=frame_def.guarantees,
        top_k=top_k,
    )

    # 6. Contradiction suppression
    final_blocks = await suppress_contradictions(conn, final_blocks)

    # 7. Trim to top_k
    final_blocks = final_blocks[:top_k]

    # 8. Render
    text = render_blocks(final_blocks, frame_def.template, frame_def.token_budget)

    result = FrameResult(
        text=text,
        blocks=final_blocks,
        frame_name=frame_def.name,
        cached=False,
    )

    # 9. Reinforcement side effects
    if final_blocks:
        returned_ids = [b.id for b in final_blocks]
        await queries.reinforce_blocks(conn, returned_ids, current_active_hours)
        await reinforce_co_retrieved_edges(conn, returned_ids)

    # 10. Cache
    if cache is not None and frame_def.cache is not None:
        cache.set(frame_def.name, result, frame_def.cache.ttl_seconds)

    return result


async def _enforce_guarantees(
    conn: AsyncConnection,
    candidates: list[ScoredBlock],
    guarantee_tag_patterns: list[str],
    top_k: int,
) -> list[ScoredBlock]:
    """Ensure guaranteed blocks are always in the result.

    Pre-allocates slots for guaranteed blocks before filling remaining slots
    with highest-scoring non-guaranteed candidates.
    """
    if not guarantee_tag_patterns:
        return candidates

    # Find guaranteed block IDs
    guaranteed_ids: set[str] = set()
    for pattern in guarantee_tag_patterns:
        ids = await queries.get_blocks_by_tag_pattern(conn, pattern)
        guaranteed_ids.update(ids)

    # Guaranteed blocks that are in the current candidates
    guaranteed_blocks = [b for b in candidates if b.id in guaranteed_ids]
    other_blocks = [b for b in candidates if b.id not in guaranteed_ids]

    # Fill remaining slots with top-scoring non-guaranteed
    remaining_slots = max(0, top_k - len(guaranteed_blocks))
    selected = guaranteed_blocks + other_blocks[:remaining_slots]
    return selected
