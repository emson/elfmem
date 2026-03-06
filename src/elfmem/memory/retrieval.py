"""4-stage hybrid retrieval pipeline — pure (no side effects)."""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db import queries
from elfmem.db.queries import bytes_to_embedding
from elfmem.memory.dedup import cosine_similarity
from elfmem.memory.graph import compute_centrality, expand_1hop
from elfmem.ports.services import EmbeddingService
from elfmem.scoring import (
    ScoringWeights,
    compute_score,
    log_normalise_reinforcement,
)
from elfmem.types import ScoredBlock

N_SEEDS_MULTIPLIER = 4
CONTRADICTION_OVERSAMPLE = 2
DEFAULT_SEARCH_WINDOW_HOURS = 200.0
# MMR diversity coefficient: 1.0 = pure relevance, 0.0 = pure diversity
MMR_DIVERSITY_LAMBDA = 0.7


async def hybrid_retrieve(
    conn: AsyncConnection,
    *,
    embedding_svc: EmbeddingService,
    query: str | None,
    weights: ScoringWeights,
    current_active_hours: float,
    top_k: int = 5,
    tag_filter: str | None = None,
    search_window_hours: float = DEFAULT_SEARCH_WINDOW_HOURS,
) -> list[ScoredBlock]:
    """Execute the 5-stage hybrid retrieval pipeline.

    Stage 1 — Pre-filter: active blocks within search window.
    Stage 2 — Vector search: cosine similarity → top N_seeds. (Skipped if no query.)
    Stage 3 — Graph expand: 1-hop neighbours of seeds. (Skipped if no query.)
    Stage 4 — Composite score: rank all candidates.
    Stage 5 — MMR diversity: reorder for relevance + diversity. (Query-aware only.)

    Returns top_k * CONTRADICTION_OVERSAMPLE ScoredBlocks for contradiction headroom.
    """
    candidates = await _stage_1_prefilter(
        conn,
        current_active_hours=current_active_hours,
        search_window_hours=search_window_hours,
        tag_filter=tag_filter,
    )

    if not candidates:
        return []

    if query is not None:
        seed_pairs = await _stage_2_vector_search(
            embedding_svc,
            query,
            candidates,
            n_seeds=top_k * N_SEEDS_MULTIPLIER,
        )
        seed_ids = [b["id"] for b, _ in seed_pairs]
        expanded = await _stage_3_graph_expand(
            conn,
            seed_ids=seed_ids,
            existing_candidate_ids={b["id"] for b, _ in seed_pairs},
        )

        # candidates for scoring: (block, similarity, was_expanded)
        scored_inputs: list[tuple[dict[str, Any], float, bool]] = [
            (b, sim, False) for b, sim in seed_pairs
        ] + [
            (b, 0.0, True) for b in expanded
        ]
    else:
        # Queryless — all candidates with similarity=0, no graph expansion
        scored_inputs = [(b, 0.0, False) for b in candidates]

    if not scored_inputs:
        return []

    all_ids = [b["id"] for b, _, _ in scored_inputs]
    centralities = await compute_centrality(conn, all_ids)
    max_reinforcement = max((b["reinforcement_count"] for b, _, _ in scored_inputs), default=0)

    # Build embedding map for Stage 5 MMR (extracted before stage 4 scoring)
    embedding_map: dict[str, Any] = {
        block["id"]: bytes_to_embedding(block["embedding"])
        for block, _, _ in scored_inputs
        if block.get("embedding") is not None
    }

    ranked = _stage_4_composite_score(
        scored_inputs,
        weights=weights,
        current_active_hours=current_active_hours,
        centralities=centralities,
        max_reinforcement_count=max_reinforcement,
        top_k=top_k,
    )

    # Stage 5: MMR diversity reordering (query-aware retrievals with embeddings only)
    if query is not None and len(embedding_map) > 1:
        ranked = _stage_5_mmr_diversity(ranked, embedding_map, limit=len(ranked))

    return ranked


async def _stage_1_prefilter(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    search_window_hours: float,
    tag_filter: str | None,
) -> list[dict[str, Any]]:
    """Stage 1: Pre-filter active blocks within search window."""
    cutoff = current_active_hours - search_window_hours
    if tag_filter is not None:
        tagged_ids = set(await queries.get_blocks_by_tag_pattern(conn, tag_filter))
        all_blocks = await queries.get_active_blocks(conn, min_last_reinforced_at=cutoff)
        return [b for b in all_blocks if b["id"] in tagged_ids]
    return await queries.get_active_blocks_with_embeddings(
        conn, min_last_reinforced_at=cutoff
    )


async def _stage_2_vector_search(
    embedding_svc: EmbeddingService,
    query: str,
    candidates: list[dict[str, Any]],
    n_seeds: int,
) -> list[tuple[dict[str, Any], float]]:
    """Stage 2: Embed query, compute cosine similarity, return top n_seeds."""
    candidates_with_embedding = [b for b in candidates if b.get("embedding") is not None]
    if not candidates_with_embedding:
        return []

    query_vec = await embedding_svc.embed(query)

    scored: list[tuple[dict[str, Any], float]] = []
    for block in candidates_with_embedding:
        block_vec = bytes_to_embedding(block["embedding"])
        sim = cosine_similarity(query_vec, block_vec)
        scored.append((block, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:n_seeds]


async def _stage_3_graph_expand(
    conn: AsyncConnection,
    seed_ids: list[str],
    existing_candidate_ids: set[str],
) -> list[dict[str, Any]]:
    """Stage 3: 1-hop graph expansion from seeds."""
    neighbour_ids = await expand_1hop(conn, seed_ids)
    new_ids = [nid for nid in neighbour_ids if nid not in existing_candidate_ids]
    if not new_ids:
        return []
    result = []
    for nid in new_ids:
        block = await queries.get_block(conn, nid)
        if block is not None and block.get("status") == "active":
            result.append(block)
    return result


def _stage_4_composite_score(
    candidates: list[tuple[dict[str, Any], float, bool]],
    *,
    weights: ScoringWeights,
    current_active_hours: float,
    centralities: dict[str, float],
    max_reinforcement_count: int,
    top_k: int,
) -> list[ScoredBlock]:
    """Stage 4: Compute composite score for all candidates.

    Each candidate is (block_dict, similarity, was_expanded).
    Returns top (top_k × CONTRADICTION_OVERSAMPLE) ScoredBlock objects.
    """
    scored: list[ScoredBlock] = []
    for block, similarity, was_expanded in candidates:
        block_id = block["id"]
        decay_lam = float(block.get("decay_lambda", 0.01))
        hours_since = current_active_hours - float(block.get("last_reinforced_at", 0.0))

        recency = math.exp(-decay_lam * hours_since)

        centrality = centralities.get(block_id, 0.0)
        reinforcement = log_normalise_reinforcement(
            int(block.get("reinforcement_count", 0)),
            max_reinforcement_count,
        )
        confidence = float(block.get("confidence", 0.5))

        score = compute_score(
            similarity=similarity,
            confidence=confidence,
            recency=recency,
            centrality=centrality,
            reinforcement=reinforcement,
            weights=weights,
        )
        scored.append(
            ScoredBlock(
                id=block_id,
                content=block.get("summary") or block.get("content", ""),
                tags=[],
                similarity=similarity,
                confidence=confidence,
                recency=recency,
                centrality=centrality,
                reinforcement=reinforcement,
                score=score,
                was_expanded=was_expanded,
                status=block.get("status", "active"),
            )
        )

    scored.sort(key=lambda x: x.score, reverse=True)
    limit = top_k * CONTRADICTION_OVERSAMPLE
    return scored[:limit]


def _stage_5_mmr_diversity(
    candidates: list[ScoredBlock],
    embeddings: dict[str, Any],
    limit: int,
) -> list[ScoredBlock]:
    """Stage 5: Maximal Marginal Relevance reordering for result diversity.

    Greedily selects blocks maximising:
        MMR(b) = λ × score(b) − (1−λ) × max_{s∈Selected} cosine_sim(b, s)

    Where λ = MMR_DIVERSITY_LAMBDA. Higher λ favours relevance; lower favours
    diversity. Blocks without embeddings fall back to score-only selection.

    Effect: prevents the context frame from being dominated by near-duplicate
    blocks that all score high due to shared reinforcement history.

    Returns `limit` blocks in MMR-priority order.
    """
    if len(candidates) <= 1:
        return candidates[:limit]

    lam = MMR_DIVERSITY_LAMBDA
    selected: list[ScoredBlock] = []
    remaining = list(candidates)

    while remaining and len(selected) < limit:
        best_block: ScoredBlock | None = None
        best_mmr = float("-inf")

        for block in remaining:
            block_emb = embeddings.get(block.id)

            if not selected or block_emb is None:
                # First pick, or block has no embedding: use raw score
                mmr_val = block.score
            else:
                # MMR = λ×relevance − (1−λ)×max_similarity_to_already_selected
                max_sim = max(
                    (
                        cosine_similarity(block_emb, embeddings[s.id])
                        for s in selected
                        if s.id in embeddings
                    ),
                    default=0.0,
                )
                mmr_val = lam * block.score - (1 - lam) * max_sim

            if mmr_val > best_mmr:
                best_mmr = mmr_val
                best_block = block

        if best_block is not None:
            selected.append(best_block)
            remaining.remove(best_block)

    return selected
