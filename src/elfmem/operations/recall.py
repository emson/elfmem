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
from elfmem.types import DroppedBlock, FrameResult, ScoredBlock


async def recall(
    conn: AsyncConnection,
    *,
    embedding_svc: EmbeddingService,
    frame_def: FrameDefinition,
    query: str | None,
    current_active_hours: float,
    top_k: int | None = None,
    default_top_k: int = 5,
    cache: FrameCache | None = None,
    reinforce: bool = True,
    host_name: str = "elf",
) -> FrameResult:
    """Execute full retrieval with reinforcement side effects.

    Pipeline:
    0. Drop the query if the frame is queryless
    1. Resolve the guaranteed ids, and from them the effective top_k
    2. Check cache (if frame has caching enabled)
    3. Determine effective weights (renormalize if no query)
    4. Run hybrid retrieval pipeline (pure)
    5. Apply guarantee enforcement
    6. Suppress contradictions
    7. Trim to top_k, reporting what that dropped
    8. Render via template with token budget, reporting what that dropped
    9. Reinforce returned blocks (side effect)
    10. Reinforce co-retrieved edges (side effect)
    11. Cache result (if applicable)

    ``top_k`` is a hard ceiling whenever the caller passes one -- silently
    exceeding it would be the same class of bug as silently ignoring a
    caller-supplied ``host_analyses``. What changed is only its *default*:
    ``max(default_top_k, n_guaranteed)`` rather than a blind constant, so a
    frame that guarantees ten constitutional blocks no longer renders five
    of them by default and calls that success.
    """
    # 0. A queryless frame discards the query before anything reads it.
    # frame() has always documented SELF as queryless; until now the code
    # took the query anyway, embedded it, let it move 10% of the ranking,
    # and then cached the outcome under a key that ignored it -- so the
    # first question of a session silently set the identity for the next
    # hour. Dropping it here restores the documented contract and makes
    # the cache sound: no query in, nothing query-shaped to cache.
    if frame_def.queryless:
        query = None

    # 1. Guaranteed and excluded ids are both resolved before retrieval, for
    # the same reason: the candidate pool is sized from top_k, so neither can
    # be applied afterwards without distorting what the pool contained.
    guaranteed_ids = await _resolve_tag_set(
        conn,
        include_patterns=frame_def.guarantees,
        minus_patterns=frame_def.guarantee_excludes,
    )
    excluded_ids = await _resolve_tag_set(
        conn,
        include_patterns=frame_def.filters.exclude_tag_patterns,
        minus_patterns=frame_def.filters.exclude_exempt_patterns,
    )
    # A block cannot be both guaranteed a slot and denied one. The guarantee
    # is the more specific, more deliberate declaration, so it wins -- and a
    # frame declaring both for the same block is a config error worth not
    # silently resolving in the surprising direction.
    excluded_ids -= guaranteed_ids
    effective_k = top_k if top_k is not None else max(default_top_k, len(guaranteed_ids))

    # 2. Check cache
    if cache is not None and frame_def.cache is not None:
        cached = cache.get(frame_def.name, effective_k)
        if cached is not None:
            return FrameResult(
                text=cached.text,
                blocks=cached.blocks,
                frame_name=frame_def.name,
                cached=True,
                # Carried forward, not recomputed: a cache hit returns the
                # same text, so it dropped the same blocks. Omitting these
                # would make a cached call look complete while an identical
                # uncached one reported truncation.
                dropped=cached.dropped,
                budget_used=cached.budget_used,
                budget_total=cached.budget_total,
                excluded_by_filter=cached.excluded_by_filter,
            )

    # 3. Determine weights
    if query is None:
        weights = frame_def.weights.renormalized_without_similarity()
    else:
        weights = frame_def.weights

    # 4. Tag filter (first pattern only in Phase 1)
    tag_filter: str | None = None
    if frame_def.filters.tag_patterns:
        tag_filter = frame_def.filters.tag_patterns[0]

    # 5. Hybrid retrieval (pure)
    candidates = await hybrid_retrieve(
        conn,
        embedding_svc=embedding_svc,
        query=query,
        weights=weights,
        current_active_hours=current_active_hours,
        top_k=effective_k,
        tag_filter=tag_filter,
        exclude_ids=excluded_ids,
        search_window_hours=frame_def.filters.search_window_hours,
        score_boosts=frame_def.score_boosts,
    )

    # 6. Guarantee enforcement
    final_blocks = _enforce_guarantees(
        candidates=candidates,
        guaranteed_ids=guaranteed_ids,
        top_k=effective_k,
    )

    # 7. Contradiction suppression -- the third silent reducer, and the one
    # least likely to be guessed from the outside. Consolidation flags
    # near-duplicate pairs instead of destroying either half (ADR 0010), so
    # a corpus of similarly-worded principles can legitimately render a
    # fraction of itself here with nothing else in the pipeline having
    # dropped anything.
    before_suppression = final_blocks
    final_blocks = await suppress_contradictions(conn, final_blocks)
    kept_ids = {b.id for b in final_blocks}
    dropped = [
        _as_dropped(b, "contradiction")
        for b in before_suppression
        if b.id not in kept_ids
    ]

    # 8. Trim to the ceiling. Guaranteed blocks are ordered first by
    # _enforce_guarantees, so they survive this trim in preference to
    # merely high-scoring ones -- but an explicit top_k still binds, and
    # whatever it costs is now reported rather than silently discarded.
    trimmed = final_blocks[effective_k:]
    final_blocks = final_blocks[:effective_k]
    dropped.extend(_as_dropped(b, "top_k") for b in trimmed)

    # 8. Render
    render = render_blocks(
        final_blocks, frame_def.template, frame_def.token_budget, host_name,
    )
    dropped.extend(_as_dropped(b, "token_budget") for b in render.dropped)
    # `blocks` reports what actually reached the text, so that
    # `len(result.blocks)` and the rendered content can never disagree.
    final_blocks = render.selected

    result = FrameResult(
        text=render.text,
        blocks=final_blocks,
        frame_name=frame_def.name,
        cached=False,
        dropped=dropped,
        budget_used=render.budget_used,
        budget_total=frame_def.token_budget,
        excluded_by_filter=len(excluded_ids),
    )

    # 10. Reinforcement side effects. Skipped for a preview: retrieval
    # normally strengthens what it returns, which is right for real use and
    # wrong for a diagnostic -- `doctor --frames` renders every frame, and
    # reinforcing on each run would make the blocks that already render more
    # likely to render, inflating the very scores it exists to inspect.
    if final_blocks and reinforce:
        returned_ids = [b.id for b in final_blocks]
        await queries.reinforce_blocks(conn, returned_ids, current_active_hours)
        await reinforce_co_retrieved_edges(conn, returned_ids, current_active_hours)

    # 11. Cache, keyed on the effective ceiling rather than the raw argument
    # so a `top_k=None` call and an explicit call that resolve to the same
    # ceiling share one entry instead of computing the same result twice.
    if cache is not None and frame_def.cache is not None:
        cache.set(frame_def.name, result, frame_def.cache.ttl_seconds, effective_k)

    return result


def _as_dropped(block: ScoredBlock, reason: str) -> DroppedBlock:
    """Project a retrieved block onto the smaller shape a caller needs to
    diagnose why it is missing from the rendered text."""
    return DroppedBlock(
        id=block.id,
        content=block.content,
        tags=list(block.tags),
        reason=reason,  # type: ignore[arg-type]
    )


async def _resolve_tag_set(
    conn: AsyncConnection,
    include_patterns: list[str],
    minus_patterns: list[str] | None = None,
) -> set[str]:
    """Active block ids matching *include_patterns*, minus *minus_patterns*.

    One helper, two callers, because a frame's guarantee and a frame's
    exclusion are the same computation pointed in opposite directions:
    "these tags, except those" describes both `guarantees`/`guarantee_excludes`
    and `exclude_tag_patterns`/`exclude_exempt_patterns`.

    Resolved before retrieval rather than after, in both cases: the candidate
    pool is sized from top_k, so a guarantee applied afterwards cannot rescue
    a block that never made the pool, and an exclusion applied afterwards
    would let the excluded blocks consume the pool on their way out.
    """
    if not include_patterns:
        return set()

    resolved: set[str] = set()
    for pattern in include_patterns:
        ids = await queries.get_blocks_by_tag_pattern(conn, pattern)
        resolved.update(ids)

    for pattern in minus_patterns or []:
        exempt = await queries.get_blocks_by_tag_pattern(conn, pattern)
        resolved.difference_update(exempt)

    return resolved


def _enforce_guarantees(
    candidates: list[ScoredBlock],
    guaranteed_ids: set[str],
    top_k: int,
) -> list[ScoredBlock]:
    """Order guaranteed blocks ahead of the rest, then fill remaining slots.

    Pure: the ids are resolved once by ``_resolve_tag_set``. Blocks excluded
    there forfeit the guarantee -- they still compete for the remaining
    slots on score, they just cannot pre-empt.
    """
    if not guaranteed_ids:
        return candidates

    guaranteed_blocks = [b for b in candidates if b.id in guaranteed_ids]
    other_blocks = [b for b in candidates if b.id not in guaranteed_ids]

    # Fill remaining slots with top-scoring non-guaranteed
    remaining_slots = max(0, top_k - len(guaranteed_blocks))
    return guaranteed_blocks + other_blocks[:remaining_slots]
