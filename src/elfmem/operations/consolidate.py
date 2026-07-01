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
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import (
    add_tags,
    bytes_to_embedding,
    get_active_blocks,
    get_active_blocks_with_embeddings,
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
from elfmem.types import (
    BlockAnalysis,
    ConsolidateResult,
    ConsolidationHealthMetrics,
    ContradictionFinding,
    Edge,
)

SELF_ALIGNMENT_THRESHOLD = 0.70
EDGE_SCORE_THRESHOLD = 0.45
EDGE_DEGREE_CAP = 5
CONTRADICTION_THRESHOLD = 0.80
NEAR_DUP_EXACT_THRESHOLD = 0.95   # similarity >= this → silent reject
NEAR_DUP_NEAR_THRESHOLD = 0.90    # similarity >= this → supersede existing
CONTRADICTION_SIMILARITY_PREFILTER = 0.40
CONTRADICTION_TOP_K = 10  # ADR 0007: hard cap on contradiction LLM calls per block

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
    embedding_model: str = "unknown"
    # Deep-sleep rescoring (v0.13.3): True when this block's analysis came
    # from the LLM-bypass path (skip_llm or LLM timeout fallback). Carried
    # through to _apply_decisions so the persisted last_scored_at is set
    # to NULL — making the block first in line for `dream --rescore`.
    llm_skipped: bool = False


@dataclass
class _EdgeDecision:
    """An edge to create between two active blocks."""
    from_id: str
    to_id: str
    weight: float


@dataclass
class _ContradictionDecision:
    """A contradiction to record between two active blocks.

    ``score`` is the LLM contradiction confidence (persisted to the
    contradictions table). The four feature fields are detection-time
    auxiliary signals surfaced on ``ContradictionFinding`` so agents can
    apply per-deployment rules — not persisted, not used for suppression.
    """
    block_a_id: str
    block_b_id: str
    score: float
    cosine: float
    tag_jaccard: float
    category_match: bool
    hours_apart: float


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

    Hard guard: returns 0.0 if cosine < MINIMUM_COSINE_FOR_EDGE (0.50).
    Without this guard, same-session + same-category context (non-cosine floor
    ≈ 0.25) would allow very low cosine pairs to form edges. 0.50 ensures
    genuine semantic similarity before contextual signals contribute.
    """
    w_cos, w_tag, w_cat, w_temp = 0.55, 0.20, 0.15, 0.10
    cos = max(0.0, cosine_similarity(vec_a, vec_b))
    if cos < MINIMUM_COSINE_FOR_EDGE:
        return 0.0
    tag  = jaccard_similarity(tags_a, tags_b)
    cat  = 1.0 if category_a == category_b else CROSS_CATEGORY_SCORE
    temp = temporal_proximity(hours_a, hours_b)
    return w_cos * cos + w_tag * tag + w_cat * cat + w_temp * temp


def _contradiction_features(
    *,
    cosine: float,
    tags_a: list[str],
    tags_b: list[str],
    category_a: str,
    category_b: str,
    hours_a: float,
    hours_b: float,
) -> tuple[float, float, bool, float]:
    """Detection-time signals surfaced on each contradiction finding.

    Pure — every input is already in scope at the detection site, so no
    extra I/O. Returns (cosine_clamped, tag_jaccard, category_match,
    hours_apart). Used by agents that want to gate suppression decisions
    on richer signal than the LLM score alone (e.g. ignore "contradictions"
    between pairs with very high tag overlap, which usually indicate the
    blocks discuss the same topic rather than disagree about it).
    """
    return (
        max(0.0, cosine),
        jaccard_similarity(tags_a, tags_b),
        category_a == category_b,
        max(0.0, abs(hours_a - hours_b)),
    )


def _fallback_analysis() -> BlockAnalysis:
    """Neutral BlockAnalysis when an LLM call times out.

    Block is promoted with neutral confidence (0.50) and no tags.
    Re-scored on next consolidation if the LLM recovers.
    """
    return BlockAnalysis(alignment_score=0.5, tags=[], summary=None)


def _strip_metadata_prefix(content: str) -> str:
    """Strip metadata prefixes from content for cleaner embedding.

    Handles common formats like "[date] speaker: text" or "speaker: text".
    Returns the text portion only, which produces better semantic embeddings
    by removing noise from dates, speakers, and bracketed metadata.
    Falls back to original content if no prefix is detected.
    """
    # Strip leading bracketed metadata: [anything] rest → rest
    text = content
    if text.startswith("[") and "] " in text:
        text = text.split("] ", 1)[1]
    # Strip speaker prefix: "Name: text" → "text"
    if ": " in text:
        text = text.split(": ", 1)[1]
    return text or content


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

        # heapq.nlargest: O(n log k) vs sort's O(n log n); k = EDGE_DEGREE_CAP
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
    contradiction_top_k: int = CONTRADICTION_TOP_K,
    skip_llm: bool = False,
    skip_contradictions: bool = False,
    max_inbox_per_run: int | None = None,
) -> tuple[
    list[_BlockDecision],
    list[_EdgeDecision],
    list[_ContradictionDecision],
    int,
    int,
    int,
    int,
    int,
]:
    """Read inbox, embed, score with LLM, and compute all decisions.

    No database writes. Under WAL DEFERRED: only holds a shared read lock.
    The write lock is not acquired until _apply_decisions() issues its first UPDATE.

    Returns (block_decisions, edge_decisions, contradiction_decisions,
    processed_count, pair_checks_done, pairs_above_prefilter, pairs_capped,
    inbox_remaining). All counts are 0 if the inbox was empty.
    ``pair_checks_done`` counts every (inbox_block, active_block) pair the
    contradiction loop considered; ``pairs_above_prefilter`` counts the
    subset that survived the cosine prefilter; ``pairs_capped`` (ADR 0007)
    counts the subset of those that were skipped anyway because they fell
    outside the per-block ``contradiction_top_k``. All three fuel
    ``ConsolidationHealthMetrics`` in the caller (issue #73, ADR 0006).
    ``inbox_remaining`` is the count left unprocessed after ``max_inbox_per_run``
    truncation — 0 unless the inbox was larger than the budget.
    """
    inbox = await get_inbox_blocks(conn)
    if not inbox:
        return [], [], [], 0, 0, 0, 0, 0

    inbox_remaining = 0
    if max_inbox_per_run is not None and len(inbox) > max_inbox_per_run:
        inbox_remaining = len(inbox) - max_inbox_per_run
        inbox = inbox[:max_inbox_per_run]  # oldest first (ADR 0007 FIFO fairness)

    # Load active blocks and build their embedding vectors.
    #
    # When skip_llm=True: summary falls back to content, so the stored embedding
    # (written by update_block_scoring) equals embed(content). We load directly
    # from the database — zero API calls. This reduces embedding work from O(n²)
    # across all consolidation batches to O(n): each block is embedded once on
    # inbox entry, then its vector is reused from storage forever after.
    #
    # When skip_llm=False: the stored embedding is embed(summary), which differs
    # from embed(content). Near-dup and contradiction checks compare content
    # embeddings, so we must re-embed active blocks via the embedding service to
    # keep the vectors on the same semantic basis as inbox blocks.
    if skip_llm:
        active_blocks = await get_active_blocks_with_embeddings(conn)
        active_vecs: dict[str, tuple[dict[str, Any], np.ndarray]] = {}
        for a_block in active_blocks:
            vec = bytes_to_embedding(a_block["embedding"])
            active_vecs[a_block["content"].strip().lower()] = (a_block, vec)
    else:
        active_blocks = await get_active_blocks(conn)
        active_vecs = {}
        if active_blocks:
            active_texts = [a["content"].strip().lower() for a in active_blocks]
            vecs_list = await embedding_svc.embed_batch(active_texts)
            for a_block, vec in zip(active_blocks, vecs_list, strict=False):
                active_vecs[a_block["content"].strip().lower()] = (a_block, vec)

    # Load tags for all blocks upfront — needed for decay tier and edge scoring.
    all_ids = [b["id"] for b in active_blocks] + [b["id"] for b in inbox]
    tags_map: dict[str, list[str]] = await get_tags_batch(conn, all_ids)

    inbox_texts_rev = [b["content"].strip().lower() for b in reversed(inbox)]
    if inbox_texts_rev:
        await embedding_svc.embed_batch(inbox_texts_rev)

    # Health-metric counters (issue #73, ADR 0006). Both stay at 0 when the
    # contradiction loop is fully skipped (skip_llm / skip_contradictions /
    # all-message batches), making ``contradiction_detection_rate`` and
    # ``prefilter_pass_rate`` honestly 0.0 in those modes rather than NaN.
    pair_checks_done = 0
    pairs_above_prefilter = 0
    pairs_capped = 0  # ADR 0007: prefilter-passing pairs skipped by contradiction_top_k

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

        # Messages are events, not knowledge claims — skip dedup and
        # contradiction checks. They still get embeddings and edges.
        is_message = category == "message"

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

        if not is_message and best_active is not None and best_sim >= near_dup_exact_threshold:
            block_decisions.append(_BlockDecision(
                block_id=block_id, action="reject_exact", supersedes_id=None,
            ))
            continue

        supersedes_id: str | None = None
        if not is_message and best_active is not None and best_sim >= near_dup_near_threshold:
            supersedes_id = best_active["id"]
            evolving_vecs.pop(best_active["content"].strip().lower(), None)

        # LLM scoring — external I/O with timeout, shared lock only.
        # skip_llm: bypass LLM calls entirely (embed + promote only).
        # Blocks are promoted with neutral scoring AND last_scored_at=NULL,
        # making them first in line for `dream --rescore`. The same NULL
        # signal is set on LLM timeout, so timeout-fallback blocks are no
        # longer a one-way door (the prior bug fixed by v0.13.3).
        llm_skipped = False
        if skip_llm:
            analysis = _fallback_analysis()
            llm_skipped = True
        else:
            try:
                analysis = await asyncio.wait_for(
                    llm.process_block(content, ""),
                    timeout=_LLM_PROCESS_TIMEOUT,
                )
            except TimeoutError:
                analysis = _fallback_analysis()
                llm_skipped = True

        inferred_tags = analysis.tags or []
        all_block_tags = list({*tags_map.get(block_id, []), *inferred_tags})
        tags_map[block_id] = all_block_tags  # update for edge and contradiction scoring

        tier = determine_decay_tier(all_block_tags, category)
        # v0.15.2: identity mapping aligns consolidate with rescore.py:245.
        # Historic cliff (α<threshold → 0.50, else α) created a 0.20
        # discontinuity at the threshold; the floor was a band-aid that
        # masked the discontinuity it created. LLM-timeout / skip_llm
        # blocks still land at 0.50 via _fallback_analysis() returning
        # alignment_score=0.50. See docs/plans/plan_confidence_architecture.md
        # and issue #50 for the full analysis.
        confidence = analysis.alignment_score
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
            embedding_model=embedding_svc.model_name,
            llm_skipped=llm_skipped,
        ))

        # Contradiction detection — LLM, shared lock, with timeout.
        # Reuses cached cosine similarities from the near-dup pass above.
        # New items added to evolving_vecs (from earlier batch promotions) are
        # not in sim_cache; their similarity is computed on demand.
        # Skipped when skip_llm=True (no LLM at all) or skip_contradictions=True
        # (keeps process_block summaries but avoids the O(n²) contradiction loop).
        if skip_llm or skip_contradictions or is_message:
            evolving_vecs[norm_content] = (block, vec)
            newly_promoted.append((block, vec))
            continue

        # Collect every candidate clearing the cosine prefilter, cheaply (no
        # LLM call yet), then cap the expensive LLM-checked set to the
        # contradiction_top_k most similar (ADR 0007). Bounds worst-case
        # per-block LLM cost to O(K), independent of active-set size.
        candidates: list[tuple[float, dict[str, Any]]] = []
        for _, (a_block, a_vec) in evolving_vecs.items():
            sim = sim_cache.get(a_block["id"]) or cosine_similarity(vec, a_vec)
            pair_checks_done += 1
            if sim < contradiction_similarity_prefilter:
                continue
            pairs_above_prefilter += 1
            candidates.append((sim, a_block))

        if len(candidates) > contradiction_top_k:
            pairs_capped += len(candidates) - contradiction_top_k
        top_candidates = heapq.nlargest(contradiction_top_k, candidates, key=lambda c: c[0])

        for sim, a_block in top_candidates:
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
                cos_clamped, tag_jacc, cat_match, hours_apart = _contradiction_features(
                    cosine=sim,
                    tags_a=tags_map.get(block_id, []),
                    tags_b=tags_map.get(a_block["id"], []),
                    category_a=category,
                    category_b=a_block["category"],
                    hours_a=current_active_hours,
                    hours_b=float(a_block.get("last_reinforced_at") or 0.0),
                )
                contradiction_decisions.append(
                    _ContradictionDecision(
                        block_a_id=a_id,
                        block_b_id=b_id,
                        score=c_score,
                        cosine=cos_clamped,
                        tag_jaccard=tag_jacc,
                        category_match=cat_match,
                        hours_apart=hours_apart,
                    )
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

    return (
        block_decisions,
        edge_decisions,
        contradiction_decisions,
        len(inbox),
        pair_checks_done,
        pairs_above_prefilter,
        pairs_capped,
        inbox_remaining,
    )


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
        # last_scored_at: NULL when the LLM was bypassed (skip_llm or
        # timeout fallback) — flags the block for catch-up via
        # `dream --rescore`. Otherwise stamped with the current time.
        # v0.17: seed Beta sufficient statistics at promotion with total mass
        # 1.0 — α = confidence, β = 1 - confidence. This keeps the invariant
        # ``confidence == α / (α + β)`` from birth, so the first outcome
        # update doesn't have to "earn back" the alignment-derived confidence.
        promotion_alpha = d.confidence
        promotion_beta = 1.0 - d.confidence
        if d.llm_skipped:
            await update_block_scoring(
                conn,
                d.block_id,
                self_alignment=d.alignment_score,
                decay_lambda=d.decay_lambda,
                embedding=d.summary_embedding,
                embedding_model=d.embedding_model,
                token_count=d.token_count,
                summary=d.summary,
                clear_last_scored_at=True,
                success_count=promotion_alpha,
                failure_count=promotion_beta,
            )
        else:
            await update_block_scoring(
                conn,
                d.block_id,
                self_alignment=d.alignment_score,
                decay_lambda=d.decay_lambda,
                embedding=d.summary_embedding,
                embedding_model=d.embedding_model,
                token_count=d.token_count,
                summary=d.summary,
                last_scored_at=datetime.now(UTC).isoformat(),
                success_count=promotion_alpha,
                failure_count=promotion_beta,
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
    contradiction_top_k: int = CONTRADICTION_TOP_K,
    skip_llm: bool = False,
    skip_contradictions: bool = False,
    max_inbox_per_run: int | None = None,
) -> ConsolidateResult:
    """Promote inbox blocks through the full consolidation pipeline.

    Pipeline (two internal phases on the same connection):
    1. _collect_decisions: reads + embedding + LLM scoring (shared lock only)
    2. _apply_decisions:   all database writes (write lock acquired here, held briefly)

    Under SQLite WAL DEFERRED, the write lock is not acquired until _apply_decisions
    issues its first UPDATE. LLM and embedding calls in phase 1 run under a shared
    lock, so they do not block concurrent learn() or recall() writers.

    LLM timeouts (30s per block, 15s per contradiction check) prevent a hung
    provider from stalling the write lock indefinitely. ``contradiction_top_k``
    additionally bounds worst-case per-block LLM cost to O(K); ``max_inbox_per_run``
    bounds how many inbox blocks one call processes (ADR 0007) — the remainder
    is reported via ``ConsolidateResult.inbox_remaining`` and left in the inbox
    for the next call.
    """
    (
        block_decisions,
        edge_decisions,
        contradiction_decisions,
        processed,
        pair_checks_done,
        pairs_above_prefilter,
        pairs_capped,
        inbox_remaining,
    ) = await _collect_decisions(
        conn,
        llm=llm,
        embedding_svc=embedding_svc,
        current_active_hours=current_active_hours,
        self_alignment_threshold=self_alignment_threshold,
        near_dup_exact_threshold=near_dup_exact_threshold,
        near_dup_near_threshold=near_dup_near_threshold,
        contradiction_threshold=contradiction_threshold,
        contradiction_similarity_prefilter=contradiction_similarity_prefilter,
        contradiction_top_k=contradiction_top_k,
        edge_score_threshold=edge_score_threshold,
        edge_degree_cap=edge_degree_cap,
        skip_llm=skip_llm,
        skip_contradictions=skip_contradictions,
        max_inbox_per_run=max_inbox_per_run,
    )

    if processed == 0:
        # ``health`` left at its dataclass default (None). Empty consolidation
        # measured no cycle; populating zeros would falsely imply otherwise.
        return ConsolidateResult(
            processed=0, promoted=0, deduplicated=0, edges_created=0,
            inbox_remaining=inbox_remaining,
        )

    promoted, deduplicated, edges_created = await _apply_decisions(
        conn,
        block_decisions,
        edge_decisions,
        contradiction_decisions,
        current_active_hours=current_active_hours,
    )

    # Health metrics (issue #73, ADR 0006; contradiction_cap_rate added ADR 0007).
    # ``max(1, ...)`` guards ÷0 in the honest cases: ``pair_checks_done == 0``
    # when the contradiction loop was skipped (skip_llm / skip_contradictions /
    # all-message batch).
    health = ConsolidationHealthMetrics(
        edge_creation_rate=edges_created / max(1, promoted),
        contradiction_detection_rate=len(contradiction_decisions) / max(1, pair_checks_done),
        prefilter_pass_rate=pairs_above_prefilter / max(1, pair_checks_done),
        promotion_rate=promoted / max(1, processed),
        deduplication_rate=deduplicated / max(1, processed),
        contradiction_cap_rate=pairs_capped / max(1, pairs_above_prefilter),
    )

    return ConsolidateResult(
        processed=processed,
        promoted=promoted,
        deduplicated=deduplicated,
        edges_created=edges_created,
        inbox_remaining=inbox_remaining,
        contradictions_detected=len(contradiction_decisions),
        contradictions=[
            ContradictionFinding(
                block_a_id=cd.block_a_id,
                block_b_id=cd.block_b_id,
                score=cd.score,
                cosine=cd.cosine,
                tag_jaccard=cd.tag_jaccard,
                category_match=cd.category_match,
                hours_apart=cd.hours_apart,
            )
            for cd in contradiction_decisions
        ],
        health=health,
    )
