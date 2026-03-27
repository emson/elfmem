"""consolidate() — batch promotion of inbox blocks to active.

Architecture: read → compute → write.

Under SQLite WAL DEFERRED transactions, the write lock is acquired only on
the first UPDATE/INSERT. By separating the pipeline into a read+compute phase
(no writes) and a write phase (no LLM calls), LLM and embedding I/O runs under
a shared lock only, keeping the exclusive write lock window to milliseconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import heapq
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    add_tags,
    get_active_blocks,
    get_inbox_blocks,
    get_tags_batch,
    insert_contradiction,
    insert_edge,
    reinforce_blocks,
    update_block_scoring,
    update_block_status,
)
from elfmem.memory.blocks import decay_lambda_for_tier, determine_decay_tier
from elfmem.memory.dedup import cosine_similarity
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.scoring import (
    CROSS_CATEGORY_SCORE,
    MINIMUM_COSINE_FOR_EDGE,
    jaccard_similarity,
    temporal_proximity,
)
from elfmem.types import BlockAnalysis, ConsolidateResult, Edge

SELF_ALIGNMENT_THRESHOLD = 0.70
EDGE_SCORE_THRESHOLD = 0.40
EDGE_DEGREE_CAP = 10
CONTRADICTION_THRESHOLD = 0.80
NEAR_DUP_EXACT_THRESHOLD = 0.95   # similarity >= this → silent reject
NEAR_DUP_NEAR_THRESHOLD = 0.90    # similarity >= this → supersede existing
CONTRADICTION_SIMILARITY_PREFILTER = 0.40

# LLM call timeouts — prevent write-lock stalls on slow or hung providers.
_LLM_PROCESS_TIMEOUT = 30.0    # seconds per block analysis
_LLM_CONTRADICT_TIMEOUT = 15.0  # seconds per contradiction check


# ── Decision dataclasses ──────────────────────────────────────────────────────
# Internal to the consolidation pipeline. Not part of the public API.

@dataclass
class _BlockDecision:
    """Computed outcome for one inbox block after LLM scoring."""
    block_id: str
    action: Literal["promote", "reject_exact", "supersede"]
    supersedes_id: str | None           # existing active block id to archive (supersede only)
    inferred_tags: list[str] = field(default_factory=list)
    confidence: float = 0.50
    alignment_score: float = 0.0
    summary: str | None = None
    summary_embedding: np.ndarray | None = None
    decay_lambda: float = 0.01
    token_count: int = 0


@dataclass
class _EdgeDecision:
    """An edge to create between two active blocks."""
    from_id: str
    to_id: str
    weight: float


@dataclass
class _ContradictionDecision:
    """A contradiction to record between two active blocks."""
    block_a_id: str
    block_b_id: str
    score: float


# ── Pure scoring helpers ──────────────────────────────────────────────────────

def _composite_edge_score(
    vec_a: np.ndarray,
    vec_b: np.ndarray,
    tags_a: list[str],
    tags_b: list[str],
    hours_a: float,
    hours_b: float,
    category_a: str,
    category_b: str,
) -> float:
    """Multi-signal edge quality score for similarity-origin edges.

    Formula: cosine×0.55 + tag_jaccard×0.20 + category_match×0.15 + temporal×0.10

    Cosine is clamped to [0.0, 1.0] — negative cosine contributes 0 rather
    than penalising contextually related blocks.

    Hard guard: returns 0.0 if cosine < MINIMUM_COSINE_FOR_EDGE.
    Without this guard, same-session + same-category context (non-cosine floor
    ≈ 0.25) would allow cosine ≈ 0.27 to form edges — below the semantic floor
    for meaningful relatedness. Spurious edges corrupt recall via graph expansion.
    """
    w_cos, w_tag, w_cat, w_temp = 0.55, 0.20, 0.15, 0.10
    cos = max(0.0, cosine_similarity(vec_a, vec_b))
    if cos < MINIMUM_COSINE_FOR_EDGE:
        return 0.0
    tag  = jaccard_similarity(tags_a, tags_b)
    cat  = 1.0 if category_a == category_b else CROSS_CATEGORY_SCORE
    temp = temporal_proximity(hours_a, hours_b)
    return w_cos * cos + w_tag * tag + w_cat * cat + w_temp * temp


def _fallback_analysis() -> BlockAnalysis:
    """Neutral BlockAnalysis when an LLM call times out.

    Block is promoted with neutral confidence (0.50) and no tags.
    Re-scored on next consolidation if the LLM recovers.
    """
    return BlockAnalysis(alignment_score=0.5, tags=[], summary=None)


def _compute_edge_decisions(
    newly_promoted: list[tuple[dict[str, Any], np.ndarray]],
    all_active: list[tuple[dict[str, Any], np.ndarray]],
    tags_map: dict[str, list[str]],
    current_active_hours: float,
    edge_score_threshold: float,
    edge_degree_cap: int,
) -> list[_EdgeDecision]:
    """Compute edges for newly promoted blocks against all active blocks.

    Pure function: no DB access, no LLM calls.
    Newly promoted blocks use current_active_hours as their activity timestamp.
    """
    decisions: list[_EdgeDecision] = []
    for block, vec in newly_promoted:
        block_id = block["id"]
        block_category = block["category"]
        block_tags = tags_map.get(block_id, [])
        candidates: list[tuple[str, float]] = []

        for a_block, a_vec in all_active:
            if a_block["id"] == block_id:
                continue
            score = _composite_edge_score(
                vec, a_vec,
                block_tags, tags_map.get(a_block["id"], []),
                current_active_hours,
                float(a_block.get("last_reinforced_at") or 0.0),
                block_category, a_block["category"],
            )
            if score >= edge_score_threshold:
                candidates.append((a_block["id"], score))

        # heapq.nlargest: O(n log k) vs sort's O(n log n); k = EDGE_DEGREE_CAP = 10
        for other_id, score in heapq.nlargest(edge_degree_cap, candidates, key=lambda x: x[1]):
            from_id, to_id = Edge.canonical(block_id, other_id)
            decisions.append(_EdgeDecision(from_id=from_id, to_id=to_id, weight=score))

    return decisions


# ── Phase 1: collect decisions (reads + LLM, no writes) ──────────────────────

async def _collect_decisions(
    conn: AsyncConnection,
    *,
    llm: LLMService,
    embedding_svc: EmbeddingService,
    current_active_hours: float,
    self_alignment_threshold: float,
    near_dup_exact_threshold: float,
    near_dup_near_threshold: float,
    contradiction_threshold: float,
    contradiction_similarity_prefilter: float,
    edge_score_threshold: float,
    edge_degree_cap: int,
) -> tuple[list[_BlockDecision], list[_EdgeDecision], list[_ContradictionDecision], int]:
    """Read inbox, embed, score with LLM, and compute all decisions.

    No database writes. Under WAL DEFERRED: only holds a shared read lock.
    The write lock is not acquired until _apply_decisions() issues its first UPDATE.

    Returns (block_decisions, edge_decisions, contradiction_decisions, processed_count).
    processed_count is 0 if the inbox was empty.
    """
    inbox = await get_inbox_blocks(conn)
    if not inbox:
        return [], [], [], 0

    active_blocks = await get_active_blocks(conn)

    # Load tags for all blocks upfront — needed for decay tier and edge scoring.
    all_ids = [b["id"] for b in active_blocks] + [b["id"] for b in inbox]
    tags_map: dict[str, list[str]] = await get_tags_batch(conn, all_ids)

    # Batch embed active blocks; warm inbox embedding cache in reverse order.
    active_vecs: dict[str, tuple[dict[str, Any], np.ndarray]] = {}
    if active_blocks:
        active_texts = [a["content"].strip().lower() for a in active_blocks]
        vecs_list = await embedding_svc.embed_batch(active_texts)
        for a_block, vec in zip(active_blocks, vecs_list, strict=False):
            active_vecs[a_block["content"].strip().lower()] = (a_block, vec)

    inbox_texts_rev = [b["content"].strip().lower() for b in reversed(inbox)]
    if inbox_texts_rev:
        await embedding_svc.embed_batch(inbox_texts_rev)

    # Mutable snapshot: tracks the evolving active set within this batch.
    # Superseded blocks are removed; promoted blocks are added.
    # Later inbox blocks see earlier decisions, matching the original behaviour.
    evolving_vecs = dict(active_vecs)
    block_decisions: list[_BlockDecision] = []
    newly_promoted: list[tuple[dict[str, Any], np.ndarray]] = []
    contradiction_decisions: list[_ContradictionDecision] = []

    for block in inbox:
        block_id = block["id"]
        content = block["content"]
        category = block["category"]
        norm_content = content.strip().lower()

        # Cache hit: pre-warmed by embed_batch above.
        vec = await embedding_svc.embed(norm_content)

        # Near/exact duplicate check (pure in-memory, no DB).
        # Cosine similarities are cached here and reused by contradiction detection
        # below, avoiding a second O(n_active) similarity pass per block.
        sim_cache: dict[str, float] = {}
        best_active: dict[str, Any] | None = None
        best_sim = 0.0
        for _, (a_block, a_vec) in evolving_vecs.items():
            sim = cosine_similarity(vec, a_vec)
            sim_cache[a_block["id"]] = sim
            if sim > best_sim:
                best_sim = sim
                best_active = a_block

        if best_active is not None and best_sim >= near_dup_exact_threshold:
            block_decisions.append(_BlockDecision(
                block_id=block_id, action="reject_exact", supersedes_id=None,
            ))
            continue

        supersedes_id: str | None = None
        if best_active is not None and best_sim >= near_dup_near_threshold:
            supersedes_id = best_active["id"]
            evolving_vecs.pop(best_active["content"].strip().lower(), None)

        # LLM scoring — external I/O with timeout, shared lock only.
        try:
            analysis = await asyncio.wait_for(
                llm.process_block(content, ""),
                timeout=_LLM_PROCESS_TIMEOUT,
            )
        except TimeoutError:
            analysis = _fallback_analysis()

        inferred_tags = analysis.tags or []
        all_block_tags = list({*tags_map.get(block_id, []), *inferred_tags})
        tags_map[block_id] = all_block_tags  # update for edge and contradiction scoring

        tier = determine_decay_tier(all_block_tags, category)
        confidence = (
            analysis.alignment_score
            if analysis.alignment_score >= self_alignment_threshold
            else 0.50
        )
        summary_text = analysis.summary or content
        summary_vec = await embedding_svc.embed(summary_text.strip().lower())

        action: Literal["promote", "reject_exact", "supersede"] = (
            "supersede" if supersedes_id else "promote"
        )
        block_decisions.append(_BlockDecision(
            block_id=block_id,
            action=action,
            supersedes_id=supersedes_id,
            inferred_tags=inferred_tags,
            confidence=confidence,
            alignment_score=analysis.alignment_score,
            summary=analysis.summary,
            summary_embedding=summary_vec,
            decay_lambda=decay_lambda_for_tier(tier),
            token_count=max(1, len(content) // 4),
        ))

        # Contradiction detection — LLM, shared lock, with timeout.
        # Reuses cached cosine similarities from the near-dup pass above.
        # New items added to evolving_vecs (from earlier batch promotions) are
        # not in sim_cache; their similarity is computed on demand.
        for _, (a_block, a_vec) in evolving_vecs.items():
            sim = sim_cache.get(a_block["id"]) or cosine_similarity(vec, a_vec)
            if sim < contradiction_similarity_prefilter:
                continue
            try:
                c_score = await asyncio.wait_for(
                    llm.detect_contradiction(content, a_block["content"]),
                    timeout=_LLM_CONTRADICT_TIMEOUT,
                )
            except TimeoutError:
                continue
            if c_score >= contradiction_threshold:
                a_id = min(block_id, a_block["id"])
                b_id = max(block_id, a_block["id"])
                contradiction_decisions.append(
                    _ContradictionDecision(block_a_id=a_id, block_b_id=b_id, score=c_score)
                )

        # Add to evolving set so subsequent inbox blocks can form edges with this one.
        evolving_vecs[norm_content] = (block, vec)
        newly_promoted.append((block, vec))

    edge_decisions = _compute_edge_decisions(
        newly_promoted=newly_promoted,
        all_active=list(evolving_vecs.values()),
        tags_map=tags_map,
        current_active_hours=current_active_hours,
        edge_score_threshold=edge_score_threshold,
        edge_degree_cap=edge_degree_cap,
    )

    return block_decisions, edge_decisions, contradiction_decisions, len(inbox)


# ── Phase 2: apply decisions (writes only, brief write-lock window) ───────────

async def _apply_decisions(
    conn: AsyncConnection,
    block_decisions: list[_BlockDecision],
    edge_decisions: list[_EdgeDecision],
    contradiction_decisions: list[_ContradictionDecision],
    *,
    current_active_hours: float,
) -> tuple[int, int, int]:
    """Write all pre-computed consolidation decisions to the database.

    This is the only function that writes. The WAL write lock is acquired here
    on the first UPDATE and released when the caller's transaction commits.
    All operations are pure data writes — no LLM calls, no embedding calls.

    Returns (promoted, deduplicated, edges_created).
    """
    promoted = 0
    deduplicated = 0
    promoted_ids: list[str] = []

    for d in block_decisions:
        if d.action == "reject_exact":
            await update_block_status(conn, d.block_id, "archived", archive_reason="superseded")
            deduplicated += 1
            continue

        if d.action == "supersede" and d.supersedes_id:
            await update_block_status(
                conn, d.supersedes_id, "archived", archive_reason="superseded"
            )
            deduplicated += 1

        if d.inferred_tags:
            await add_tags(conn, d.block_id, d.inferred_tags)
        await update_block_scoring(
            conn,
            d.block_id,
            confidence=d.confidence,
            self_alignment=d.alignment_score,
            decay_lambda=d.decay_lambda,
            embedding=d.summary_embedding,
            embedding_model="mock",  # TODO: expose model name from EmbeddingService protocol
            token_count=d.token_count,
            summary=d.summary,
        )
        await update_block_status(conn, d.block_id, "active")
        promoted_ids.append(d.block_id)
        promoted += 1

    # Batch reinforce all promoted blocks in one UPDATE ... WHERE id IN (...)
    if promoted_ids:
        await reinforce_blocks(conn, promoted_ids, current_active_hours)

    for cd in contradiction_decisions:
        # UniqueConstraint on (block_a_id, block_b_id) — duplicate pairs in the
        # same batch are rejected by the DB; suppress only that specific error.
        with contextlib.suppress(IntegrityError):
            await insert_contradiction(
                conn, block_a_id=cd.block_a_id, block_b_id=cd.block_b_id, score=cd.score
            )

    edges_created = 0
    for ed in edge_decisions:
        await insert_edge(
            conn,
            from_id=ed.from_id,
            to_id=ed.to_id,
            weight=ed.weight,
            relation_type="similar",
            origin="similarity",
            last_active_hours=current_active_hours,
        )
        edges_created += 1

    return promoted, deduplicated, edges_created


# ── Public entry point ────────────────────────────────────────────────────────

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
    edge_score_threshold: float = EDGE_SCORE_THRESHOLD,
    edge_degree_cap: int = EDGE_DEGREE_CAP,
    contradiction_similarity_prefilter: float = CONTRADICTION_SIMILARITY_PREFILTER,
) -> ConsolidateResult:
    """Promote all inbox blocks through the full consolidation pipeline.

    Pipeline (two internal phases on the same connection):
    1. _collect_decisions: reads + embedding + LLM scoring (shared lock only)
    2. _apply_decisions:   all database writes (write lock acquired here, held briefly)

    Under SQLite WAL DEFERRED, the write lock is not acquired until _apply_decisions
    issues its first UPDATE. LLM and embedding calls in phase 1 run under a shared
    lock, so they do not block concurrent learn() or recall() writers.

    LLM timeouts (30s per block, 15s per contradiction check) prevent a hung
    provider from stalling the write lock indefinitely.
    """
    block_decisions, edge_decisions, contradiction_decisions, processed = (
        await _collect_decisions(
            conn,
            llm=llm,
            embedding_svc=embedding_svc,
            current_active_hours=current_active_hours,
            self_alignment_threshold=self_alignment_threshold,
            near_dup_exact_threshold=near_dup_exact_threshold,
            near_dup_near_threshold=near_dup_near_threshold,
            contradiction_threshold=contradiction_threshold,
            contradiction_similarity_prefilter=contradiction_similarity_prefilter,
            edge_score_threshold=edge_score_threshold,
            edge_degree_cap=edge_degree_cap,
        )
    )

    if processed == 0:
        return ConsolidateResult(processed=0, promoted=0, deduplicated=0, edges_created=0)

    promoted, deduplicated, edges_created = await _apply_decisions(
        conn,
        block_decisions,
        edge_decisions,
        contradiction_decisions,
        current_active_hours=current_active_hours,
    )

    return ConsolidateResult(
        processed=processed,
        promoted=promoted,
        deduplicated=deduplicated,
        edges_created=edges_created,
    )
