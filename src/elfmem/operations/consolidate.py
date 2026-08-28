"""consolidate() — batch promotion of inbox blocks to active.

Architecture: read → compute → write.

Under SQLite WAL DEFERRED transactions, the write lock is acquired only on
the first UPDATE/INSERT. By separating the pipeline into a read+compute phase
(no writes) and a write phase (no LLM calls), LLM and embedding I/O runs under
a shared lock only, keeping the exclusive write lock window to milliseconds.
"""

from __future__ import annotations

import asyncio
import heapq
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
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
from elfmem.memory import ledger as _ledger
from elfmem.memory.blocks import decay_lambda_for_tier, determine_decay_tier
from elfmem.memory.dedup import cosine_similarity
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.scoring import (
    CROSS_CATEGORY_SCORE,
    MINIMUM_COSINE_FOR_EDGE,
    jaccard_similarity,
    temporal_proximity,
)
from elfmem.types import BlockAnalysis, ConsolidateResult, ConsolidationHealthMetrics, Edge

SELF_ALIGNMENT_THRESHOLD = 0.70
EDGE_SCORE_THRESHOLD = 0.45
EDGE_DEGREE_CAP = 5
NEAR_DUP_EXACT_THRESHOLD = 0.95   # similarity >= this → silent reject
NEAR_DUP_NEAR_THRESHOLD = 0.90    # similarity >= this → supersede existing

# LLM call timeout — prevents write-lock stalls on slow or hung providers.
_LLM_PROCESS_TIMEOUT = 30.0    # seconds per block analysis


def _cue_similarity(cue_a: str | None, cue_b: str | None) -> float | None:
    """Lexical overlap of two cue lines, or None when either is missing.

    Jaccard on tokens rather than embedding cosine, for two reasons. It costs
    nothing, and it measures the same thing retrieval does: a cue earns its
    place through BM25, so its similarity should be judged on the same terms.

    Measured, not assumed: on the real 145-block corpus, cue *embedding*
    cosine never exceeds 0.812 across any pair and sits near 0.67 even for
    blocks whose content matches at 0.977. Short-text embeddings occupy a
    narrow cone, so a threshold carried over from the content scale would
    never fire. Nothing thresholds this value today — it is recorded as the
    evidence a future auto-merge rule would need.
    """
    if not cue_a or not cue_b:
        return None
    # jaccard_similarity takes lists and sets them internally -- wrapping in
    # set() here was redundant and violated its declared list[str] signature.
    return jaccard_similarity(
        cue_a.lower().split(), cue_b.lower().split()
    )


# ── Decision dataclasses ──────────────────────────────────────────────────────
# Internal to the consolidation pipeline. Not part of the public API.

@dataclass
class _BlockDecision:
    """Computed outcome for one inbox block after LLM scoring."""
    block_id: str
    action: Literal["promote", "reject_exact"]
    # Existing active block this one closely duplicates, if any. Formerly the
    # block to archive; now the other half of a pair that is recorded and
    # kept. Nothing on this path destroys an existing block any more.
    near_duplicate_of: str | None
    near_duplicate_score: float = 0.0
    near_duplicate_cue_similarity: float | None = None
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
    edge_score_threshold: float,
    edge_degree_cap: int,
    skip_llm: bool = False,
    max_inbox_per_run: int | None = None,
    host_analyses: dict[str, BlockAnalysis] | None = None,
) -> tuple[
    list[_BlockDecision],
    list[_EdgeDecision],
    int,
    int,
    list[str],
]:
    """Read inbox, embed, score with LLM, and compute all decisions.

    No database writes. Under WAL DEFERRED: only holds a shared read lock.
    The write lock is not acquired until _apply_decisions() issues its first UPDATE.

    Returns (block_decisions, edge_decisions, processed_count,
    inbox_remaining, analyses_unused). All counts are 0 if the inbox was
    empty. ``inbox_remaining`` is the count left unprocessed after
    ``max_inbox_per_run`` truncation — 0 unless the inbox was larger than the
    budget. ``analyses_unused`` lists caller-supplied ``host_analyses`` ids
    this run did not apply.

    Near-duplicate matches no longer archive anything here: the pair is
    carried on the decision and recorded by ``_apply_decisions``, counted as
    ``ConsolidateResult.near_duplicates_flagged``.

    ``host_analyses``: a block id present here is treated as genuinely
    analysed — same as a successful ``llm.process_block()`` call, not a
    fallback — and ``llm.process_block()`` is never called for it. Lets a
    host agent session (e.g. a Claude Code session, via
    ``MemorySystem.inbox()`` + ``dream(host_analyses=...)``) supply its own
    reasoning instead of a configured LLM adapter, for some or all of the
    batch. Dedup, tagging, decay, and promotion are otherwise unchanged —
    this only substitutes where the alignment/tags/summary comes from.
    """
    inbox = await get_inbox_blocks(conn)
    if not inbox:
        return [], [], 0, 0, sorted(host_analyses or {})

    inbox_remaining = 0
    if max_inbox_per_run is not None and len(inbox) > max_inbox_per_run:
        inbox_remaining = len(inbox) - max_inbox_per_run
        inbox = inbox[:max_inbox_per_run]  # oldest first (ADR 0007 FIFO fairness)

    # Which caller-supplied analyses this run will NOT apply: either the block
    # fell outside the max_inbox_per_run window, or its id is not pending at
    # all. Both used to pass silently, and the first is the damaging one --
    # the block stays in the inbox for a later pass that analyses it with the
    # configured LLM instead, quietly replacing the wording the caller
    # supplied host_analyses precisely to preserve.
    processed_ids = {b["id"] for b in inbox}
    analyses_unused = sorted(set(host_analyses or {}) - processed_ids)

    # Load active blocks and build their embedding vectors.
    #
    # When skip_llm=True: summary falls back to content, so the stored embedding
    # (written by update_block_scoring) equals embed(content). We load directly
    # from the database — zero API calls. This reduces embedding work from O(n²)
    # across all consolidation batches to O(n): each block is embedded once on
    # inbox entry, then its vector is reused from storage forever after.
    #
    # When skip_llm=False: the stored embedding is embed(summary), which differs
    # from embed(content). The near-dup check compares content embeddings, so
    # we must re-embed active blocks via the embedding service to keep the
    # vectors on the same semantic basis as inbox blocks.
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


    # Mutable snapshot: tracks the evolving active set within this batch.
    # Superseded blocks are removed; promoted blocks are added.
    # Later inbox blocks see earlier decisions, matching the original behaviour.
    evolving_vecs = dict(active_vecs)
    block_decisions: list[_BlockDecision] = []
    newly_promoted: list[tuple[dict[str, Any], np.ndarray]] = []

    for block in inbox:
        block_id = block["id"]
        content = block["content"]
        category = block["category"]
        norm_content = content.strip().lower()

        # Cache hit: pre-warmed by embed_batch above.
        vec = await embedding_svc.embed(norm_content)

        # Messages are events, not knowledge claims — skip dedup checks.
        # They still get embeddings and edges.
        is_message = category == "message"

        # Near/exact duplicate check (pure in-memory, no DB).
        best_active: dict[str, Any] | None = None
        best_sim = 0.0
        for _, (a_block, a_vec) in evolving_vecs.items():
            sim = cosine_similarity(vec, a_vec)
            if sim > best_sim:
                best_sim = sim
                best_active = a_block

        if not is_message and best_active is not None and best_sim >= near_dup_exact_threshold:
            block_decisions.append(_BlockDecision(
                block_id=block_id, action="reject_exact", near_duplicate_of=None,
            ))
            continue

        near_dup_of: str | None = None
        near_dup_score = 0.0
        near_dup_cue_sim: float | None = None
        if not is_message and best_active is not None and best_sim >= near_dup_near_threshold:
            # Both blocks are kept. Supersession used to archive the existing
            # one here: 41 of 187 blocks ever created on the maintainer's
            # instance died this way, six of them constitutional, with no
            # audit row and no undo. Keeping both costs ~11% more corpus
            # tokens on that same corpus; recall-time suppression already
            # stops the pair from occupying two slots in one frame, and
            # unlike deletion it is reversible.
            #
            # The pin guard that used to sit here is not gone, it is
            # subsumed: no block, pinned or not, is destroyed on this path.
            near_dup_of = best_active["id"]
            near_dup_score = best_sim
            near_dup_cue_sim = _cue_similarity(
                block.get("cue"), best_active.get("cue")
            )

        # LLM scoring — external I/O with timeout, shared lock only.
        # host_analyses: a host agent session already did this reasoning —
        # treated as real analysis (llm_skipped stays False), never falls
        # through to skip_llm or a real LLM call for this block.
        # skip_llm: bypass LLM calls entirely (embed + promote only).
        # Blocks are promoted with neutral scoring AND last_scored_at=NULL,
        # making them first in line for `dream --rescore`. The same NULL
        # signal is set on LLM timeout, so timeout-fallback blocks are no
        # longer a one-way door (the prior bug fixed by v0.13.3).
        llm_skipped = False
        if host_analyses is not None and block_id in host_analyses:
            analysis = host_analyses[block_id]
        elif skip_llm:
            analysis = _fallback_analysis()
            llm_skipped = True
        else:
            try:
                analysis = await asyncio.wait_for(
                    llm.process_block(content, ""),
                    timeout=_LLM_PROCESS_TIMEOUT,
                )
            except (TimeoutError, Exception):  # noqa: BLE001 — boundary
                # Not just timeout: a local/self-hosted model can also return
                # non-JSON text that exhausts the adapter's own retries and
                # raises a schema/parse error (ValidationError et al.).
                # Same fallback either way — rescore_blocks() already treats
                # this failure class identically; this path didn't, until a
                # real local-model response ("Please provide the **Age...")
                # surfaced the gap.
                analysis = _fallback_analysis()
                llm_skipped = True

        inferred_tags = analysis.tags or []
        all_block_tags = list({*tags_map.get(block_id, []), *inferred_tags})
        tags_map[block_id] = all_block_tags  # update for edge scoring

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

        block_decisions.append(_BlockDecision(
            block_id=block_id,
            action="promote",
            near_duplicate_of=near_dup_of,
            near_duplicate_score=near_dup_score,
            near_duplicate_cue_similarity=near_dup_cue_sim,
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
        len(inbox),
        inbox_remaining,
        analyses_unused,
    )


# ── Phase 2: apply decisions (writes only, brief write-lock window) ───────────

async def _apply_decisions(
    conn: AsyncConnection,
    block_decisions: list[_BlockDecision],
    edge_decisions: list[_EdgeDecision],
    *,
    current_active_hours: float,
    ledger_dir: Path | None = None,
) -> tuple[int, int, int, int]:
    """Write all pre-computed consolidation decisions to the database.

    This is the only function that writes. The WAL write lock is acquired here
    on the first UPDATE and released when the caller's transaction commits.
    All operations are pure data writes — no LLM calls, no embedding calls.

    Returns (promoted, deduplicated, edges_created, near_duplicates).
    """
    promoted = 0
    deduplicated = 0
    near_duplicates = 0
    promoted_ids: list[str] = []

    for d in block_decisions:
        if d.action == "reject_exact":
            await update_block_status(conn, d.block_id, "archived", archive_reason="superseded")
            deduplicated += 1
            continue

        if d.near_duplicate_of:
            # Record the pair; destroy nothing. Recall-time suppression
            # (context/contradiction.py, deliberately kept by ADR 0010) stops
            # both halves occupying slots in the same frame, and `elfmem
            # review` surfaces the pair for a deliberate decision.
            a, b = sorted((d.block_id, d.near_duplicate_of))
            await insert_contradiction(
                conn, block_a_id=a, block_b_id=b,
                score=d.near_duplicate_score, kind="near_duplicate",
                cue_similarity=d.near_duplicate_cue_similarity,
            )
            near_duplicates += 1

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
        if ledger_dir is not None:
            # Promotion is where a block's posterior, decay class and summary
            # come into being. All three live only in the index, which under
            # file authority is the disposable layer.
            _ledger.append(
                ledger_dir,
                _ledger.KIND_PROMOTE,
                active_hours=current_active_hours,
                id=d.block_id,
                conf=d.confidence,
                sig=d.alignment_score,
                lam=d.decay_lambda,
                **({"sum": d.summary} if d.summary else {}),
            )
        promoted_ids.append(d.block_id)
        promoted += 1

    # Batch reinforce all promoted blocks in one UPDATE ... WHERE id IN (...)
    if promoted_ids:
        await reinforce_blocks(conn, promoted_ids, current_active_hours)

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

    return promoted, deduplicated, edges_created, near_duplicates


# ── Public entry point ────────────────────────────────────────────────────────

async def consolidate(
    conn: AsyncConnection,
    *,
    llm: LLMService,
    embedding_svc: EmbeddingService,
    current_active_hours: float,
    self_alignment_threshold: float = SELF_ALIGNMENT_THRESHOLD,
    near_dup_exact_threshold: float = NEAR_DUP_EXACT_THRESHOLD,
    near_dup_near_threshold: float = NEAR_DUP_NEAR_THRESHOLD,
    edge_score_threshold: float = EDGE_SCORE_THRESHOLD,
    edge_degree_cap: int = EDGE_DEGREE_CAP,
    skip_llm: bool = False,
    max_inbox_per_run: int | None = None,
    host_analyses: dict[str, BlockAnalysis] | None = None,
    ledger_dir: Path | None = None,
) -> ConsolidateResult:
    """Promote inbox blocks through the full consolidation pipeline.

    Pipeline (two internal phases on the same connection):
    1. _collect_decisions: reads + embedding + LLM scoring (shared lock only)
    2. _apply_decisions:   all database writes (write lock acquired here, held briefly)

    Under SQLite WAL DEFERRED, the write lock is not acquired until _apply_decisions
    issues its first UPDATE. LLM and embedding calls in phase 1 run under a shared
    lock, so they do not block concurrent learn() or recall() writers.

    The per-block LLM timeout (30s) prevents a hung provider from stalling
    the write lock indefinitely. ``max_inbox_per_run`` bounds how many inbox
    blocks one call processes (ADR 0007) — the remainder is reported via
    ``ConsolidateResult.inbox_remaining`` and left in the inbox for the next
    call.

    Pairwise LLM contradiction detection was retired in v2 step 7b: it was
    the dominant LLM cost of this pipeline (up to 10 contradiction calls per
    1 process_block call, ADR 0007) for a yield of 14 lifetime findings, 12
    still unresolved. Contradiction *suppression* at recall time
    (``context/contradiction.py::suppress_contradictions``) and the
    ``contradictions`` table it reads are untouched — existing findings keep
    suppressing; new pairs simply aren't auto-detected until a corpus-level
    LLM review (step 6b) replaces this. See ADR 0010.
    """
    (
        block_decisions,
        edge_decisions,
        processed,
        inbox_remaining,
        analyses_unused,
    ) = await _collect_decisions(
        conn,
        llm=llm,
        embedding_svc=embedding_svc,
        current_active_hours=current_active_hours,
        self_alignment_threshold=self_alignment_threshold,
        near_dup_exact_threshold=near_dup_exact_threshold,
        near_dup_near_threshold=near_dup_near_threshold,
        edge_score_threshold=edge_score_threshold,
        edge_degree_cap=edge_degree_cap,
        skip_llm=skip_llm,
        max_inbox_per_run=max_inbox_per_run,
        host_analyses=host_analyses,
    )

    if processed == 0:
        # ``health`` left at its dataclass default (None). Empty consolidation
        # measured no cycle; populating zeros would falsely imply otherwise.
        return ConsolidateResult(
            processed=0, promoted=0, deduplicated=0, edges_created=0,
            inbox_remaining=inbox_remaining,
            analyses_unused=analyses_unused,
        )

    promoted, deduplicated, edges_created, near_duplicates = await _apply_decisions(
        conn,
        block_decisions,
        edge_decisions,
        current_active_hours=current_active_hours,
        ledger_dir=ledger_dir,
    )

    # Health metrics (issue #73, ADR 0006).
    health = ConsolidationHealthMetrics(
        edge_creation_rate=edges_created / max(1, promoted),
        promotion_rate=promoted / max(1, processed),
        deduplication_rate=deduplicated / max(1, processed),
    )

    return ConsolidateResult(
        processed=processed,
        promoted=promoted,
        deduplicated=deduplicated,
        edges_created=edges_created,
        inbox_remaining=inbox_remaining,
        near_duplicates_flagged=near_duplicates,
        health=health,
        analyses_unused=analyses_unused,
    )
