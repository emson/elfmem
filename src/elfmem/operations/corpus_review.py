"""corpus_review — deterministic staleness detection (v2 step 6a).

No LLM calls. A block is a staleness candidate only when three weak, cheap
signals all agree: long-unused, rarely reinforced, and never confirmed by an
outcome. Independent of the decay-tier system (`decay_lambda`, `curate()`)
that step 7 retires — this does not read or write `decay_lambda`.

Duplicate/contradiction detection (LLM, corpus-wide) is step 6b, a later
addition to `review_corpus()`'s output, not this module.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import get_active_blocks, get_unresolved_pairs
from elfmem.types import CorpusProposal, CorpusReviewResult, NearDuplicatePair


def _stale_candidates(
    blocks: list[dict[str, Any]],
    *,
    current_active_hours: float,
    min_hours_since_reinforced: float,
    max_reinforcement_count: int,
    max_proposals: int,
) -> list[CorpusProposal]:
    """Pure function: no I/O. Blocks already fetched by the caller.

    All three signals must agree — this is deliberately conservative. A
    block reinforced twice yesterday and a block reinforced twice a year
    ago are very different; requiring all three avoids flagging the former.
    """
    candidates: list[CorpusProposal] = []
    for block in blocks:
        hours_since = current_active_hours - float(block.get("last_reinforced_at", 0.0))
        reinforcement_count = int(block.get("reinforcement_count", 0))
        outcome_evidence = float(block.get("outcome_evidence", 0.0))
        if (
            hours_since >= min_hours_since_reinforced
            and reinforcement_count <= max_reinforcement_count
            and outcome_evidence <= 0.0
        ):
            candidates.append(
                CorpusProposal(
                    block_id=block["id"],
                    kind="stale",
                    reason=(
                        f"not reinforced in {hours_since:.0f}h "
                        f"(reinforced {reinforcement_count}x, no outcome evidence)"
                    ),
                    content_preview=block.get("content", "")[:80],
                )
            )
            if len(candidates) >= max_proposals:
                break
    return candidates


async def review_corpus(
    conn: AsyncConnection,
    *,
    current_active_hours: float,
    min_hours_since_reinforced: float,
    max_reinforcement_count: int,
    max_proposals: int,
) -> CorpusReviewResult:
    """Read active blocks once, compute staleness proposals. No writes.

    Applying a proposal (or not) is the caller's job — this function never
    mutates anything, matching `forget()`'s own no-surprise-writes contract.
    """
    blocks = await get_active_blocks(conn)
    proposals = _stale_candidates(
        blocks,
        current_active_hours=current_active_hours,
        min_hours_since_reinforced=min_hours_since_reinforced,
        max_reinforcement_count=max_reinforcement_count,
        max_proposals=max_proposals,
    )
    # Near-duplicate pairs consolidation kept instead of destroying. They are
    # surfaced here rather than acted on: this function proposes, the caller
    # decides, exactly as it does for staleness.
    pairs = await get_unresolved_pairs(conn, kind="near_duplicate")
    return CorpusReviewResult(
        reviewed_count=len(blocks),
        proposals=proposals,
        near_duplicate_pairs=[
            NearDuplicatePair(
                block_a_id=p["block_a_id"],
                block_b_id=p["block_b_id"],
                similarity=float(p["score"]),
                cue_similarity=(
                    None if p["cue_similarity"] is None
                    else float(p["cue_similarity"])
                ),
            )
            for p in pairs
        ],
    )
