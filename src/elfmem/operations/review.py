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

from elfmem.exceptions import (
    AmendmentAlreadyReverted,
    AmendmentNotFound,
    BlockNotFound,
)
from elfmem.types import (
    AmendmentRecord,
    AmendmentResult,
    ConstitutionalReviewResult,
    ProposedAmendment,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from elfmem.config import ReviewConfig
    from elfmem.context.frames import FrameCache
    from elfmem.ports.services import EmbeddingService, LLMService

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


# ── Apply path (commit 4): accept / revert / list ────────────────────────────


def _normalise_for_embedding(content: str) -> str:
    """Match the embedding-input convention used by consolidate / rescore."""
    return content.strip().lower()


def _parse_timestamp(raw: object) -> datetime:
    """Coerce a DB timestamp (SQLite returns str OR datetime) to aware UTC."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=UTC)
    if isinstance(raw, str) and raw:
        # SQLite's CURRENT_TIMESTAMP returns "YYYY-MM-DD HH:MM:SS" (space, no tz).
        normalised = raw.replace(" ", "T") if " " in raw and "T" not in raw else raw
        try:
            ts = datetime.fromisoformat(normalised)
        except ValueError:
            return datetime.now(UTC)
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _row_to_amendment_record(row: dict[str, object]) -> AmendmentRecord:
    reverted_raw = row.get("reverted_at")
    reverted_at = _parse_timestamp(reverted_raw) if reverted_raw else None
    raw_id = row["id"]
    raw_drift = row["drift_score"]
    return AmendmentRecord(
        id=int(raw_id) if isinstance(raw_id, (int, float, str)) else 0,
        block_id=str(row["block_id"]),
        timestamp=_parse_timestamp(row.get("timestamp")),
        pre_content=str(row["pre_content"]),
        post_content=str(row["post_content"]),
        pre_summary=(
            str(row["pre_summary"]) if row.get("pre_summary") is not None else None
        ),
        post_summary=(
            str(row["post_summary"]) if row.get("post_summary") is not None else None
        ),
        drift_score=(
            float(raw_drift) if isinstance(raw_drift, (int, float)) else 0.0
        ),
        rationale=(
            str(row["rationale"]) if row.get("rationale") is not None else None
        ),
        acceptor=str(row["acceptor"]),
        reverted_at=reverted_at,
    )


async def _compute_current_drift(
    conn: AsyncConnection,
    *,
    block_id: str,
    review_cfg: ReviewConfig,
    current_active_hours: float,
) -> float:
    """Recompute drift_score for a block against today's operational centroid.

    Used by ``accept_amendment`` when the caller did NOT pass a drift_score
    from a recent ``review_constitutional`` result. Returns 0.0 (no drift
    audit signal) when the centroid cannot be built — accept still proceeds
    because the agent has explicitly chosen to apply the proposal.
    """
    from elfmem.db.queries import bytes_to_embedding, get_block

    block = await get_block(conn, block_id)
    if block is None:
        return 0.0
    emb_bytes = block.get("embedding")
    if not isinstance(emb_bytes, bytes) or not emb_bytes:
        return 0.0
    embeddings = await fetch_recent_reinforced_embeddings(
        conn,
        window_hours=review_cfg.window_hours,
        min_reinforcement=review_cfg.min_reinforcement,
        top_n=review_cfg.top_n,
        current_active_hours=current_active_hours,
    )
    centroid = recent_self_centroid(embeddings)
    if centroid is None:
        return 0.0
    return compute_drift(bytes_to_embedding(emb_bytes), centroid)


async def accept_amendment(
    engine: AsyncEngine,
    embedding_svc: EmbeddingService,
    frame_cache: FrameCache,
    *,
    block_id: str,
    proposed_content: str,
    rationale: str | None,
    drift_score: float,
    acceptor: str,
) -> AmendmentResult:
    """Apply a proposed amendment to a constitutional block.

    USE WHEN: The agent or user has reviewed a ``ProposedAmendment`` and
        wants to commit it. Single source of truth for the apply path.
    DON'T USE WHEN: You only want to inspect history (``list_amendments``)
        or undo a previous edit (``revert_amendment``).
    COST: One embedding call (BEFORE the DB transaction so a slow network
        round-trip does not hold a write lock) + one short transaction
        containing one INSERT into block_amendments and one UPDATE on
        blocks. O(1) overall.
    RETURNS: ``AmendmentResult`` with the new amendment_id and pre/post
        content snapshot.
    NEXT: The block goes to the front of the rescore queue
        (``last_scored_at = NULL``); ``dream --rescore`` regenerates
        ``summary`` / tags / alignment against the new content.

    Side effects:
    - ``blocks.content`` ← proposed_content
    - ``blocks.embedding`` ← embed(normalised proposed_content)
    - ``blocks.summary`` ← NULL  (stale; rescore regenerates)
    - ``blocks.last_scored_at`` ← NULL  (rescore queue front)
    - ``blocks.success_count`` / ``failure_count`` UNCHANGED — content edits
      are not knowledge confirmation events.
    - ``blocks.reinforcement_count`` / ``last_reinforced_at`` UNCHANGED.
    - ``block_amendments`` ← new audit row capturing pre/post state.
    - Frame cache: ``self`` frame invalidated.

    Raises:
        BlockNotFound: ``block_id`` does not exist.
    """
    # Step 1: read pre-state. Short read transaction so we know what to embed
    # against and what to capture in the audit row before any writes.
    from elfmem.db.queries import (
        get_block,
        insert_amendment,
        update_block_content,
    )

    async with engine.connect() as conn:
        block = await get_block(conn, block_id)
    if block is None:
        raise BlockNotFound(block_id)

    pre_content = str(block["content"])
    pre_summary_raw = block.get("summary")
    pre_summary = (
        str(pre_summary_raw) if isinstance(pre_summary_raw, str) else None
    )

    # Step 2: embed the new content OUTSIDE the transaction. Embedding is a
    # network call; holding the write transaction across it would needlessly
    # serialise unrelated DB activity.
    new_embedding = await embedding_svc.embed(
        _normalise_for_embedding(proposed_content)
    )

    # Step 3: single short transaction — insert audit row, update block.
    async with engine.begin() as conn:
        amendment_id = await insert_amendment(
            conn,
            block_id=block_id,
            pre_content=pre_content,
            post_content=proposed_content,
            pre_summary=pre_summary,
            post_summary=None,  # post_summary regenerated by next rescore
            drift_score=drift_score,
            rationale=rationale,
            acceptor=acceptor,
        )
        await update_block_content(
            conn,
            block_id=block_id,
            content=proposed_content,
            embedding=new_embedding,
            embedding_model=embedding_svc.model_name,
        )
        # Read the row back so the returned timestamp matches what was stored.
        ts_row = (await conn.execute(
            text("SELECT timestamp FROM block_amendments WHERE id = :id"),
            {"id": amendment_id},
        )).first()

    # Step 4: invalidate the self frame — constitutional content just changed.
    frame_cache.invalidate("self")

    timestamp = (
        _parse_timestamp(ts_row[0]) if ts_row is not None else datetime.now(UTC)
    )
    return AmendmentResult(
        amendment_id=amendment_id,
        block_id=block_id,
        pre_content=pre_content,
        post_content=proposed_content,
        timestamp=timestamp,
        acceptor=acceptor,
    )


async def revert_amendment(
    engine: AsyncEngine,
    embedding_svc: EmbeddingService,
    frame_cache: FrameCache,
    *,
    amendment_id: int,
) -> AmendmentResult:
    """Undo one amendment: restore block.content to amendment.pre_content.

    USE WHEN: An amendment was a mistake and the agent or user wants the
        block back at its immediately-prior content.
    DON'T USE WHEN: You want to roll back through multiple amendments —
        call ``revert_amendment`` repeatedly, newest first, to walk back
        through the history. There is no "revert to original" shortcut;
        the audit chain is one-step-at-a-time on purpose.
    COST: One embedding call (BEFORE the transaction) + a transaction with
        one UPDATE on the amendment row and one UPDATE on the block.
    RETURNS: ``AmendmentResult`` describing the revert (post_content =
        the restored ``pre_content``; pre_content = what was on the block
        immediately before the revert).
    NEXT: Block re-enters the rescore queue with ``last_scored_at = NULL``.

    Raises:
        AmendmentNotFound: no row with that id.
        AmendmentAlreadyReverted: the amendment row already has a
            non-null ``reverted_at`` — refusing to write a double-revert
            into the audit trail.
    """
    from elfmem.db.queries import (
        get_amendment,
        get_block,
        mark_amendment_reverted,
        update_block_content,
    )

    async with engine.connect() as conn:
        amendment = await get_amendment(conn, amendment_id)
    if amendment is None:
        raise AmendmentNotFound(amendment_id)
    if amendment.get("reverted_at") is not None:
        raise AmendmentAlreadyReverted(amendment_id)

    block_id = str(amendment["block_id"])
    target_content = str(amendment["pre_content"])

    async with engine.connect() as conn:
        block = await get_block(conn, block_id)
    if block is None:
        # Pathological — FK CASCADE would have removed the amendment too.
        raise BlockNotFound(block_id)
    pre_revert_content = str(block["content"])

    # Embed restored content OUTSIDE the transaction (same rationale as accept).
    new_embedding = await embedding_svc.embed(
        _normalise_for_embedding(target_content)
    )

    async with engine.begin() as conn:
        await mark_amendment_reverted(conn, amendment_id)
        await update_block_content(
            conn,
            block_id=block_id,
            content=target_content,
            embedding=new_embedding,
            embedding_model=embedding_svc.model_name,
        )
        ts_row = (await conn.execute(
            text("SELECT reverted_at FROM block_amendments WHERE id = :id"),
            {"id": amendment_id},
        )).first()

    frame_cache.invalidate("self")

    timestamp = (
        _parse_timestamp(ts_row[0]) if ts_row is not None else datetime.now(UTC)
    )
    return AmendmentResult(
        amendment_id=amendment_id,
        block_id=block_id,
        pre_content=pre_revert_content,
        post_content=target_content,
        timestamp=timestamp,
        acceptor="system",  # revert is a system-driven audit event
    )


async def list_amendments(
    engine: AsyncEngine,
    *,
    block_id: str | None = None,
    limit: int = 100,
) -> list[AmendmentRecord]:
    """List amendments, newest first; optionally filter by block_id.

    USE WHEN: Inspecting audit history before deciding to revert.
    DON'T USE WHEN: You only need the latest applied amendment summary —
        ``AmendmentResult`` from ``accept_amendment`` already carries that.
    COST: One indexed SELECT bounded by ``limit``.
    RETURNS: list of ``AmendmentRecord`` in ``timestamp DESC`` order;
        empty list when there is no history (never raises for an
        unknown block_id — absence is information).
    NEXT: Pick an amendment id and call ``revert_amendment`` if needed.
    """
    from elfmem.db.queries import list_amendments as _db_list

    async with engine.connect() as conn:
        rows = await _db_list(conn, block_id=block_id, limit=limit)
    return [_row_to_amendment_record(r) for r in rows]
