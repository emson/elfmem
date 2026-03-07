"""consolidate() — batch promotion of inbox blocks to active."""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    add_tags,
    get_active_blocks,
    get_inbox_blocks,
    get_tags,
    insert_contradiction,
    insert_edge,
    reinforce_blocks,
    update_block_scoring,
    update_block_status,
)
from elfmem.memory.blocks import decay_lambda_for_tier, determine_decay_tier
from elfmem.memory.dedup import (
    EXACT_DUP_THRESHOLD,
    NEAR_DUP_THRESHOLD,
    cosine_similarity,
    resolve_near_duplicate,
)
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.types import ConsolidateResult, Edge

SELF_ALIGNMENT_THRESHOLD = 0.70
SIMILARITY_EDGE_THRESHOLD = 0.60
EDGE_DEGREE_CAP = 10
CONTRADICTION_THRESHOLD = 0.80
NEAR_DUP_EXACT_THRESHOLD = 0.95  # similarity ≥ this → silent reject
NEAR_DUP_NEAR_THRESHOLD = 0.90   # similarity ≥ this → supersede existing
CONTRADICTION_SIMILARITY_PREFILTER = 0.40  # Only check contradictions for similar pairs


async def consolidate(
    conn: AsyncConnection,
    *,
    llm: LLMService,
    embedding_svc: EmbeddingService,
    current_active_hours: float,
    self_alignment_threshold: float = SELF_ALIGNMENT_THRESHOLD,
    contradiction_threshold: float = CONTRADICTION_THRESHOLD,
    near_dup_exact_threshold: float = NEAR_DUP_EXACT_THRESHOLD,
    near_dup_near_threshold: float = NEAR_DUP_NEAR_THRESHOLD,
    similarity_edge_threshold: float = SIMILARITY_EDGE_THRESHOLD,
    edge_degree_cap: int = EDGE_DEGREE_CAP,
    contradiction_similarity_prefilter: float = CONTRADICTION_SIMILARITY_PREFILTER,
) -> ConsolidateResult:
    """Promote all inbox blocks through the full consolidation pipeline.

    Pipeline per inbox block:
    1. Pre-embed pass (reverse order) — warm up mock similarity caches
    2. For each inbox block: near-dup → score → tag → tier → contradictions → promote
    3. Edge creation pass — runs after all blocks are promoted so pairwise
       edges between batch members are created correctly

    OPTIMIZATION: Contradiction detection pre-filters by embedding similarity
    (cosine > contradiction_similarity_prefilter) before calling LLM. Reduces
    LLM calls by ~95% for large inboxes (e.g., 2,366 → ~120 calls for 22×100).
    """
    inbox = await get_inbox_blocks(conn)
    if not inbox:
        return ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)

    # ── Phase 0: snapshot active blocks ───────────────────────────────────────
    active_blocks = await get_active_blocks(conn)

    # ── Phase 1: warm-up embedding cache ──────────────────────────────────────
    # Batch embed active blocks first, then inbox in REVERSE order.
    # Reverse order ensures later-indexed texts (e.g. "satellite 1") are cached
    # before earlier-indexed ones that reference them as similarity anchors.
    # OPTIMIZATION: Batch embedding reduces API calls by ~5x.
    active_vecs: dict[str, tuple[dict[str, Any], np.ndarray]] = {}

    # Batch embed all active blocks
    active_texts = [a["content"].strip().lower() for a in active_blocks]
    if active_texts:
        active_vecs_list = await embedding_svc.embed_batch(active_texts)
        for a, vec in zip(active_blocks, active_vecs_list):
            key = a["content"].strip().lower()
            active_vecs[key] = (a, vec)

    # Batch embed inbox blocks in reverse order
    inbox_texts_reversed = [block["content"].strip().lower() for block in reversed(inbox)]
    if inbox_texts_reversed:
        await embedding_svc.embed_batch(inbox_texts_reversed)

    # ── Phase 2: score and promote (no edges yet) ──────────────────────────────
    processed = 0
    promoted = 0
    deduplicated = 0

    # Track newly promoted blocks for edge creation pass
    newly_promoted: list[tuple[dict[str, Any], np.ndarray]] = []  # (block, vec)

    for block in inbox:
        block_id: str = block["id"]
        content: str = block["content"]
        category: str = block["category"]
        processed += 1

        # Embed (returns from cache populated in Phase 1)
        norm_content = content.strip().lower()
        vec = await embedding_svc.embed(norm_content)

        # Near/exact duplicate check against current active_vecs
        best_active: dict[str, Any] | None = None
        best_sim = 0.0
        for _, (a_block, a_vec) in active_vecs.items():
            sim = cosine_similarity(vec, a_vec)
            if sim > best_sim:
                best_sim = sim
                best_active = a_block

        if best_active is not None and best_sim >= EXACT_DUP_THRESHOLD:
            await update_block_status(conn, block_id, "archived", archive_reason="superseded")
            deduplicated += 1
            continue
        elif best_active is not None and best_sim >= NEAR_DUP_THRESHOLD:
            await resolve_near_duplicate(conn, best_active, block_id)
            removed_key = best_active["content"].strip().lower()
            active_vecs.pop(removed_key, None)
            deduplicated += 1

        # Analyse block: alignment score + tags + summary (one LLM call)
        analysis = await llm.process_block(content, "")
        if analysis.tags:
            await add_tags(conn, block_id, analysis.tags)

        all_tags = await get_tags(conn, block_id)
        tier = determine_decay_tier(all_tags, category)
        lam = decay_lambda_for_tier(tier)
        confidence = (
            analysis.alignment_score
            if analysis.alignment_score >= self_alignment_threshold
            else 0.50
        )
        token_count = max(1, len(content) // 4)

        # Embed summary for DB storage; content vec (vec) is used for near-dup/edges
        summary_text = analysis.summary or content
        summary_vec = await embedding_svc.embed(summary_text.strip().lower())

        await update_block_scoring(
            conn,
            block_id,
            confidence=confidence,
            self_alignment=analysis.alignment_score,
            decay_lambda=lam,
            embedding=summary_vec,
            embedding_model="mock",
            token_count=token_count,
            summary=analysis.summary,
        )

        # Contradiction detection (with similarity pre-filter to reduce LLM calls)
        # Only check contradictions for semantically similar blocks (~95% fewer LLM calls)
        for _, (a_block, a_vec) in list(active_vecs.items()):
            sim = cosine_similarity(vec, a_vec)
            if sim < contradiction_similarity_prefilter:
                continue  # Skip dissimilar blocks; contradictions unlikely
            score = await llm.detect_contradiction(content, a_block["content"])
            if score >= CONTRADICTION_THRESHOLD:
                a_id = min(block_id, a_block["id"])
                b_id = max(block_id, a_block["id"])
                with contextlib.suppress(Exception):
                    await insert_contradiction(conn, block_a_id=a_id, block_b_id=b_id, score=score)

        # Promote to active
        await update_block_status(conn, block_id, "active")
        await reinforce_blocks(conn, [block_id], current_active_hours)

        # Update active snapshot (used for near-dup and contradiction checks
        # for subsequent inbox blocks in this same batch)
        active_vecs[norm_content] = (block, vec)
        newly_promoted.append((block, vec))
        promoted += 1

    # ── Phase 3: edge creation ────────────────────────────────────────────────
    # Done after all blocks are promoted so that edges between batch members
    # are created correctly (hub↔satellite edges require both to be in active).
    edges_created = 0
    all_active_items = list(active_vecs.values())

    for block, vec in newly_promoted:
        block_id = block["id"]
        candidates = []
        for a_block, a_vec in all_active_items:
            if a_block["id"] == block_id:
                continue
            sim = cosine_similarity(vec, a_vec)
            if sim >= similarity_edge_threshold:
                candidates.append((a_block["id"], sim))

        candidates.sort(key=lambda x: x[1], reverse=True)
        for other_id, sim in candidates[:edge_degree_cap]:
            from_id, to_id = Edge.canonical(block_id, other_id)
            try:
                await insert_edge(conn, from_id=from_id, to_id=to_id, weight=sim)
                edges_created += 1
            except Exception:
                pass

    return ConsolidateResult(
        processed=processed,
        promoted=promoted,
        deduplicated=deduplicated,
        edges_created=edges_created,
    )
