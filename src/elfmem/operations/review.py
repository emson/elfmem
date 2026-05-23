"""Constitutional review — drift detection math + DB helpers (v0.18).

Two layers:

1. **Pure math** (``compute_drift``, ``recent_self_centroid``): NumPy in,
   NumPy/float out. No I/O, no LLM, no global state. Composable and unit
   testable without fixtures.

2. **DB helpers** (``fetch_recent_reinforced_embeddings``,
   ``fetch_constitutional_blocks``): pure-read async queries that pick
   out the input rows the math needs. No mutation, no LLM.

The orchestration that composes these with an LLM amendment proposer
lives in commit 3 (``review_constitutional`` operation). This module
deliberately stops at "produce the inputs and the drift scalar"; the
"is this drift high enough to surface?" judgement is the caller's.

Defaults
--------
- ``MIN_RECENT_REINFORCED_BLOCKS = 20`` — minimum non-constitutional
  reinforced blocks needed before the centroid is meaningful enough
  to drive a constitutional review. Below this, the caller should flag
  ``insufficient_history``.
- ``CONSTITUTIONAL_TAG = "self/constitutional"`` — single source of
  truth for the tag that marks constitutional blocks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.types import ConstitutionalReviewResult, ProposedAmendment

if TYPE_CHECKING:
    from elfmem.config import ReviewConfig
    from elfmem.ports.services import LLMService

logger = logging.getLogger(__name__)

# ── Defaults ─────────────────────────────────────────────────────────────────

MIN_RECENT_REINFORCED_BLOCKS: int = 20
CONSTITUTIONAL_TAG: str = "self/constitutional"


# ── Pure math ────────────────────────────────────────────────────────────────


def compute_drift(
    block_embedding: np.ndarray,
    self_centroid: np.ndarray,
) -> float:
    """Drift score between a block and the agent's operational self.

    USE WHEN: Detecting whether a constitutional block has fallen out of
        alignment with the agent's recent operational reality.
    DON'T USE WHEN: Comparing two arbitrary blocks — use cosine similarity
        directly; drift is a half-rescaled cosine specifically interpreted
        as "distance from self".
    COST: O(d) NumPy dot product, where d is the embedding dimension.
    RETURNS: float in [0.0, 1.0]. 0.0 = perfectly aligned (cos = +1),
        0.5 = orthogonal (cos = 0), 1.0 = perfectly opposed (cos = -1).
    NEXT: Compare to a threshold; if above, the block is a candidate
        for amendment proposal via the LLM in ``review_constitutional``.

    Derivation:
        cos = (a · b) / (|a| |b|)        ∈ [-1, +1]
        drift = (1 - cos) / 2            ∈ [0, 1]

    Both vectors are L2-normalised internally so caller need not pre-normalise.
    """
    a = block_embedding.astype(np.float64)
    b = self_centroid.astype(np.float64)
    a_norm = float(np.linalg.norm(a))
    b_norm = float(np.linalg.norm(b))
    if a_norm == 0.0 or b_norm == 0.0:
        return 0.5  # undefined direction → maximally uncertain
    cos = float(np.dot(a, b) / (a_norm * b_norm))
    # Numerical safety — dot product can drift outside [-1, 1] by ~1e-15.
    cos = max(-1.0, min(1.0, cos))
    return (1.0 - cos) / 2.0


def recent_self_centroid(
    block_embeddings: list[np.ndarray],
) -> np.ndarray | None:
    """Compute the unit-normalised centroid of recently-reinforced blocks.

    USE WHEN: Establishing "where the agent IS now" — the empirical
        operational identity expressed by the blocks the agent actually
        relies on day-to-day.
    DON'T USE WHEN: You need a single block's embedding (use the block
        directly), or the input list is unfiltered (the caller is
        responsible for excluding constitutional blocks first — see
        ``fetch_recent_reinforced_embeddings``).
    COST: O(N · d) — one mean + one normalisation across N vectors.
    RETURNS: np.ndarray (unit-normalised, dtype float32) representing
        the operational self centroid, OR ``None`` when input is empty
        (caller decides what insufficient_history means).
    NEXT: Pass to ``compute_drift`` together with each constitutional
        block's embedding to obtain per-block drift scores.
    """
    if not block_embeddings:
        return None
    stack = np.stack([v.astype(np.float64) for v in block_embeddings], axis=0)
    mean = stack.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        # All vectors cancelled to zero — degenerate but representable.
        # Returning the zero vector lets compute_drift fall through to 0.5.
        result: np.ndarray = mean.astype(np.float32)
        return result
    normalised: np.ndarray = (mean / norm).astype(np.float32)
    return normalised


# ── DB helpers ───────────────────────────────────────────────────────────────


async def fetch_recent_reinforced_embeddings(
    conn: AsyncConnection,
    *,
    window_hours: float,
    min_reinforcement: int,
    top_n: int,
    current_active_hours: float,
    exclude_tag: str = CONSTITUTIONAL_TAG,
) -> list[np.ndarray]:
    """Load embeddings for the recently-reinforced, non-constitutional blocks.

    USE WHEN: Building the input to ``recent_self_centroid`` — the
        operational-self prior for drift detection.
    DON'T USE WHEN: You need full block rows; this returns embeddings
        only, by design (centroid math doesn't need anything else).
    COST: One SELECT, bounded by ``top_n``. No LLM, no mutation.
    RETURNS: list of np.ndarray (float32 vectors) ordered newest first.
        Empty list means "no eligible blocks".
    NEXT: Feed into ``recent_self_centroid`` to obtain the centroid.

    Filter:
        - status = 'active'
        - reinforcement_count > min_reinforcement
        - last_reinforced_at > current_active_hours - window_hours
        - embedding IS NOT NULL
        - NOT tagged with ``exclude_tag`` (default: self/constitutional)

    Order: last_reinforced_at DESC; limit top_n.
    """
    from elfmem.db.queries import bytes_to_embedding

    if top_n <= 0:
        return []
    cutoff = current_active_hours - window_hours
    sql = """
        SELECT embedding FROM blocks
        WHERE status = 'active'
          AND reinforcement_count > :min_r
          AND last_reinforced_at > :cutoff
          AND embedding IS NOT NULL
          AND id NOT IN (
              SELECT block_id FROM block_tags WHERE tag = :exclude_tag
          )
        ORDER BY last_reinforced_at DESC
        LIMIT :top_n
    """
    rows = await conn.execute(
        text(sql),
        {
            "min_r": min_reinforcement,
            "cutoff": cutoff,
            "exclude_tag": exclude_tag,
            "top_n": top_n,
        },
    )
    return [bytes_to_embedding(r[0]) for r in rows.fetchall() if r[0] is not None]


async def fetch_constitutional_blocks(
    conn: AsyncConnection,
    *,
    include_status: tuple[str, ...] = ("active",),
    exclude_recent_amendments_within_hours: float | None = None,
    current_active_hours: float = 0.0,
    tag: str = CONSTITUTIONAL_TAG,
) -> list[dict[str, object]]:
    """Fetch constitutional blocks (tagged ``self/constitutional``).

    USE WHEN: Selecting candidates for drift evaluation in a review cycle.
    DON'T USE WHEN: You need ALL self/* blocks — constitutional is a
        strict subset (only the foundational principles tagged
        ``self/constitutional`` by setup or seed).
    COST: One join (block_tags), plus one LEFT JOIN onto block_amendments
        when the cooldown is requested. Small N (constitutional blocks
        are a handful), so negligible.
    RETURNS: list of dicts with all block columns plus the raw
        ``embedding`` bytes (None when the block has no embedding).
        Order is stable by block id.
    NEXT: For each block, decode the embedding, call ``compute_drift``,
        and (in commit 3) pass over-threshold blocks to the LLM
        amendment proposer.

    Cooldown semantics:
        When ``exclude_recent_amendments_within_hours`` is provided,
        a block is excluded if it has an *unreverted* amendment whose
        timestamp falls within the cooldown window measured from
        ``current_active_hours``. The unit is wall-clock hours
        (block_amendments.timestamp is wall-clock); we trust the
        review caller to convert active hours → wall-clock hours
        upstream if a different clock is desired. Default behaviour
        with the timestamp comparison below is "wall-clock hours
        relative to NOW", consistent with how amendments are stamped.
    """
    if not include_status:
        return []
    placeholders = ", ".join(f":st_{i}" for i in range(len(include_status)))
    params: dict[str, object] = {
        f"st_{i}": s for i, s in enumerate(include_status)
    }
    params["tag"] = tag

    cooldown_clause = ""
    if exclude_recent_amendments_within_hours is not None:
        cooldown_clause = """
          AND id NOT IN (
              SELECT block_id FROM block_amendments
              WHERE reverted_at IS NULL
                AND timestamp > datetime(
                    'now', :cooldown_offset
                )
          )
        """
        # SQLite datetime modifier: '-N hours' subtracts from NOW.
        params["cooldown_offset"] = (
            f"-{float(exclude_recent_amendments_within_hours)} hours"
        )

    sql = f"""
        SELECT * FROM blocks
        WHERE status IN ({placeholders})
          AND id IN (
              SELECT block_id FROM block_tags WHERE tag = :tag
          )
          {cooldown_clause}
        ORDER BY id ASC
    """
    rows = await conn.execute(text(sql), params)
    return [dict(row) for row in rows.mappings()]


async def fetch_recent_reinforced_evidence(
    conn: AsyncConnection,
    *,
    window_hours: float,
    min_reinforcement: int,
    top_n: int,
    current_active_hours: float,
    exclude_tag: str = CONSTITUTIONAL_TAG,
) -> list[dict[str, object]]:
    """Load recently-reinforced, non-constitutional blocks for LLM evidence.

    USE WHEN: Building the ``evidence_summaries`` payload for the LLM
        amendment proposer — the LLM needs to know WHAT the agent has
        been doing, not just the centroid vector.
    DON'T USE WHEN: You need embeddings (use
        ``fetch_recent_reinforced_embeddings`` instead — same filter,
        different projection).
    COST: One SELECT bounded by ``top_n``. No LLM.
    RETURNS: list of dicts with ``id``, ``content``, ``summary``,
        ``reinforcement_count``, ``last_reinforced_at`` — newest first.
    NEXT: Project to summary strings for the LLM prompt.
    """
    if top_n <= 0:
        return []
    cutoff = current_active_hours - window_hours
    sql = """
        SELECT id, content, summary, reinforcement_count, last_reinforced_at
        FROM blocks
        WHERE status = 'active'
          AND reinforcement_count > :min_r
          AND last_reinforced_at > :cutoff
          AND id NOT IN (
              SELECT block_id FROM block_tags WHERE tag = :exclude_tag
          )
        ORDER BY last_reinforced_at DESC
        LIMIT :top_n
    """
    rows = await conn.execute(
        text(sql),
        {
            "min_r": min_reinforcement,
            "cutoff": cutoff,
            "exclude_tag": exclude_tag,
            "top_n": top_n,
        },
    )
    return [dict(row) for row in rows.mappings()]


# ── Orchestration ────────────────────────────────────────────────────────────


def _block_age_days(created_at: object, now: datetime) -> float:
    """Return wall-clock age in days for a created_at column value.

    ``created_at`` is stored ISO-8601; we tolerate naive timestamps by
    assuming UTC, matching how ``_now_iso`` writes them.
    """
    if not isinstance(created_at, str) or not created_at:
        return 0.0
    try:
        ts = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() / 86400.0


def _evidence_summary_for(block: dict[str, object]) -> str:
    """Project an evidence-block dict to a single human-readable line."""
    summary = block.get("summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    content = block.get("content")
    if isinstance(content, str):
        return content.strip()[:200]
    return ""


def _select_eligible(
    constitutional_blocks: list[dict[str, object]],
    self_centroid: np.ndarray,
    *,
    drift_threshold: float,
    min_block_evidence: float,
    min_age_days: float,
    now: datetime,
) -> tuple[list[tuple[dict[str, object], float]], int]:
    """Filter constitutional blocks to those eligible for amendment proposal.

    Returns ``(eligible, skipped_count)`` where ``eligible`` is a list of
    ``(block_dict, drift_score)`` tuples sorted by drift_score DESC.
    """
    from elfmem.db.queries import bytes_to_embedding

    eligible: list[tuple[dict[str, object], float]] = []
    skipped = 0
    for block in constitutional_blocks:
        emb_bytes = block.get("embedding")
        if not isinstance(emb_bytes, bytes) or not emb_bytes:
            skipped += 1
            continue
        emb = bytes_to_embedding(emb_bytes)
        drift = compute_drift(emb, self_centroid)
        if drift <= drift_threshold:
            skipped += 1
            continue
        alpha_raw = block.get("success_count")
        beta_raw = block.get("failure_count")
        alpha = float(alpha_raw) if isinstance(alpha_raw, (int, float)) else 0.0
        beta = float(beta_raw) if isinstance(beta_raw, (int, float)) else 0.0
        if (alpha + beta) < min_block_evidence:
            skipped += 1
            continue
        if _block_age_days(block.get("created_at"), now) < min_age_days:
            skipped += 1
            continue
        eligible.append((block, drift))
    eligible.sort(key=lambda pair: pair[1], reverse=True)
    return eligible, skipped


async def review_constitutional(
    conn: AsyncConnection,
    llm: LLMService,
    config: ReviewConfig,
    *,
    current_active_hours: float,
) -> ConstitutionalReviewResult:
    """Surface drifted constitutional blocks as LLM-proposed amendments.

    USE WHEN: The agent wants to know whether its tagged
        ``self/constitutional`` blocks still match its operational
        identity (the empirical centroid of recently-reinforced blocks).
        Pure surface — nothing is applied until ``accept_amendment``.
    DON'T USE WHEN: A fresh DB with little evidence (handled internally:
        result will carry ``insufficient_history=True`` and no LLM calls
        are made).
    COST: O(eligible constitutional blocks) LLM calls, bounded by
        ``config.max_proposals``. Two read-only DB queries up front.
    RETURNS: ``ConstitutionalReviewResult``. Idempotent under a
        deterministic LLM (production LLMs may vary slightly between
        runs; the orchestration itself does not).
    NEXT: Iterate ``.proposals`` and call ``accept_amendment`` (commit 4)
        on each that the agent or user wishes to apply.

    Steps:
      1. Fetch recent reinforced embeddings.
      2. If count < ``min_recent_reinforced_blocks`` →
         ``insufficient_history=True``, return immediately (no LLM calls).
      3. Compute the operational-self centroid.
      4. Fetch constitutional blocks (cooldown applied).
      5. For each, compute drift; filter by drift_threshold, age,
         and α+β evidence; sort by drift DESC; cap at ``max_proposals``.
      6. Fetch evidence summaries for the LLM context.
      7. Per-block LLM call — wrapped in the **only** try/except in this
         module: external service failures are logged and counted, never
         aborted, per the CLAUDE.md exception for N-call orchestrations
         around an external service.
    """
    embeddings = await fetch_recent_reinforced_embeddings(
        conn,
        window_hours=config.window_hours,
        min_reinforcement=config.min_reinforcement,
        top_n=config.top_n,
        current_active_hours=current_active_hours,
    )
    if len(embeddings) < config.min_recent_reinforced_blocks:
        return ConstitutionalReviewResult(
            proposals=[],
            reviewed_count=0,
            skipped_count=0,
            insufficient_history=True,
            failed_proposal_count=0,
        )

    centroid = recent_self_centroid(embeddings)
    if centroid is None:
        # Defensive — should be unreachable given the length check above.
        return ConstitutionalReviewResult(
            proposals=[], reviewed_count=0, skipped_count=0,
            insufficient_history=True, failed_proposal_count=0,
        )

    constitutional = await fetch_constitutional_blocks(
        conn,
        include_status=("active",),
        exclude_recent_amendments_within_hours=config.cooldown_hours,
        current_active_hours=current_active_hours,
    )
    reviewed_count = len(constitutional)

    now = datetime.now(UTC)
    eligible, skipped = _select_eligible(
        constitutional,
        centroid,
        drift_threshold=config.drift_threshold,
        min_block_evidence=config.min_block_evidence,
        min_age_days=config.min_age_days,
        now=now,
    )
    capped = eligible[: config.max_proposals]
    skipped += max(0, len(eligible) - len(capped))

    evidence_blocks = await fetch_recent_reinforced_evidence(
        conn,
        window_hours=config.window_hours,
        min_reinforcement=config.min_reinforcement,
        top_n=5,
        current_active_hours=current_active_hours,
    )
    evidence_summaries = [
        s for s in (_evidence_summary_for(b) for b in evidence_blocks) if s
    ]

    proposals: list[ProposedAmendment] = []
    failed_proposal_count = 0
    for block, drift in capped:
        block_id = str(block["id"])
        block_content = str(block["content"])
        block_summary = block.get("summary")
        try:
            raw = await llm.propose_amendment(
                block_content=block_content,
                block_summary=(
                    block_summary if isinstance(block_summary, str) else None
                ),
                drift_score=drift,
                evidence_summaries=evidence_summaries,
            )
        except Exception as exc:  # noqa: BLE001 — see docstring rationale.
            # The ONE place defensive code is justified in business logic:
            # an N-call orchestration around an external service. One
            # provider failure must not abort the whole review cycle.
            logger.warning(
                "amendment proposal failed",
                extra={"block_id": block_id, "error": str(exc)},
            )
            failed_proposal_count += 1
            continue
        proposals.append(ProposedAmendment(
            block_id=block_id,
            original_content=block_content,
            proposed_content=str(raw.get("proposed_content", "")),
            rationale=str(raw.get("rationale", "")),
            drift_score=drift,
        ))

    return ConstitutionalReviewResult(
        proposals=proposals,
        reviewed_count=reviewed_count,
        skipped_count=skipped,
        insufficient_history=False,
        failed_proposal_count=failed_proposal_count,
    )
