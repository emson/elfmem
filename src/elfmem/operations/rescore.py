"""Deep-sleep rescoring: select aged or unscored active blocks for re-LLM-pass.

The fourth rhythm of elfmem (after heartbeat / breathing / sleep): periodic
re-evaluation of *existing* active blocks against the *current* identity.

Pure-read selection in this module; the actual LLM pass is performed by
``consolidate()`` after the inbox phase, using the block ids returned here.

The principle this enforces:

    Memory health is observable and actionable. Doctor measures; the
    action (`dream --rescore`) heals; ordering by `last_scored_at ASC`
    ensures progressive coverage without manual targeting. Memory tends
    toward consistency under normal use, like physical hygiene tends
    toward homeostasis.

Eligibility — a block is rescore-eligible iff:
    - status == "active"
    - category not in exclude_categories (message, mind, decision, prediction)
    - source_peer IS NULL (peer perspectives stay intact)
    - no tag in exclude_tags (system/no-rescore is the explicit opt-out)
    - last_scored_at IS NULL (debt — drains first), OR
      last_scored_at < now - min_age_hours (cooldown — don't churn)

Selection order: NULL last_scored_at first (debt), then oldest
last_scored_at ascending (progressive rotation — every block leaves
the front of the queue once rescored).
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from elfmem.memory import ledger as _ledger
from elfmem.memory.dedup import cosine_similarity
from elfmem.types import GoalDirectedEdgeProposal, MetabolismDryRunResult

# ── Defaults ─────────────────────────────────────────────────────────────────
# Surfaced here so tests and callers share one source of truth. The
# corresponding values live in ElfmemConfig.consolidation.rescore.

DEFAULT_EXCLUDE_CATEGORIES: tuple[str, ...] = (
    "message", "mind", "decision", "prediction",
)
DEFAULT_EXCLUDE_TAGS: tuple[str, ...] = ("system/no-rescore",)
DEFAULT_MIN_AGE_HOURS: int = 24
DEFAULT_TARGET_MAX_AGE_DAYS: int = 90
DEFAULT_MAX_PER_RUN: int = 20
DEFAULT_DRIFT_WARNING_COUNT: int = 25
DEFAULT_DRIFT_WARNING_PERCENT: int = 25

# Edge-metabolism Stage A (docs/plans/plan_edge_metabolism.md) — dry-run
# only, nothing here ever writes to the edges table. Constants are
# justified against existing, already-defensible constants (ADR 0006's
# "no magic numbers" bar), not picked fresh:
GOAL_DIRECTED_CANDIDATE_K: int = 30
# 6x EDGE_DEGREE_CAP (operations/consolidate.py) — wide enough to surface
# non-obvious neighbours a similarity threshold would exclude, still O(1)
# per block rather than O(corpus).
GOAL_DIRECTED_MAX_EDGES_PER_BLOCK: int = 3
# Below EDGE_DEGREE_CAP (5) — tier 2 is the higher-risk, lower-confidence
# layer; it should never out-connect tier 1's deterministic edges.
GOAL_DIRECTED_SELF_GOALS_CHAR_BUDGET: int = 2400
# ~600 tokens at ~4 chars/token — matches SELF_FRAME.token_budget
# (context/frames.py), the project's own existing answer to "how much self-
# identity content belongs in one operation." Found necessary, not just
# defensive: a dry run against the real self-hosted corpus (28 self/goal
# blocks, ~49KB) blew a local model's context window outright
# (`n_keep: 27252 >= n_ctx: 4096`) before this cap existed — see
# docs/plans/plan_edge_metabolism.md's Stage A findings.


@dataclass(frozen=True)
class RescoreFilter:
    """Eligibility parameters for rescore selection. Driven by ElfmemConfig.

    Wrapped in a dataclass so the same shape governs selection (this module)
    and doctor's drift accounting (cli) — single source of truth.
    """

    exclude_categories: tuple[str, ...] = DEFAULT_EXCLUDE_CATEGORIES
    exclude_tags: tuple[str, ...] = DEFAULT_EXCLUDE_TAGS
    min_age_hours: int = DEFAULT_MIN_AGE_HOURS
    target_max_age_days: int = DEFAULT_TARGET_MAX_AGE_DAYS


@dataclass(frozen=True)
class DriftStats:
    """What doctor needs to summarise scoring health in one line.

    All counts are over rescore-eligible blocks only — non-eligible blocks
    (messages, peer-imported, etc.) don't count toward drift because they
    are deliberately excluded from rescoring.
    """

    total_active: int
    unscored: int           # last_scored_at IS NULL
    stale: int              # last_scored_at < now - target_max_age_days
    target_max_age_days: int

    @property
    def drift(self) -> int:
        return self.unscored + self.stale

    def percent_drift_of_total(self) -> float:
        if self.total_active == 0:
            return 0.0
        return 100.0 * self.drift / self.total_active

    def is_drifting(self, *, count_threshold: int, percent_threshold: int) -> bool:
        """Drift fires when EITHER absolute count or percentage threshold exceeded."""
        if self.total_active == 0:
            return False
        return (
            self.drift > count_threshold
            or self.percent_drift_of_total() > percent_threshold
        )

    def recommended_max(self, floor: int = 20, round_to: int = 50) -> int:
        """Auto-scaled `--max` recommendation: covers the visible debt
        with a small safety margin, rounded up to the nearest 50.

        Returns ``floor`` when no drift is observed (caller should
        suppress the suggestion in that case).
        """
        if self.drift <= 0:
            return floor
        margin = max(self.drift, floor)
        return ((margin + round_to - 1) // round_to) * round_to


def _build_select_query(
    filt: RescoreFilter,
    *,
    enforce_min_age: bool = True,
) -> tuple[str, dict[str, object]]:
    """Compose the eligibility query. Returns (sql, params).

    Used by both ``select_rescore_candidates`` (with LIMIT) and the doctor
    drift-stats aggregator (without LIMIT, just COUNTs). The same WHERE
    clause governs both — the eligibility rule has exactly one definition.
    """
    placeholders = ", ".join(
        f":cat_{i}" for i in range(len(filt.exclude_categories))
    ) or "''"
    params: dict[str, object] = {
        f"cat_{i}": c for i, c in enumerate(filt.exclude_categories)
    }
    no_rescore_tag_clause = ""
    if filt.exclude_tags:
        tag_placeholders = ", ".join(
            f":tag_{i}" for i in range(len(filt.exclude_tags))
        )
        no_rescore_tag_clause = (
            "AND id NOT IN ("
            f"SELECT block_id FROM block_tags WHERE tag IN ({tag_placeholders})"
            ")"
        )
        for i, tag in enumerate(filt.exclude_tags):
            params[f"tag_{i}"] = tag

    if enforce_min_age:
        cooldown_iso = (
            datetime.now(UTC) - timedelta(hours=filt.min_age_hours)
        ).isoformat()
        params["cooldown_iso"] = cooldown_iso
        scored_clause = (
            "AND (last_scored_at IS NULL OR last_scored_at < :cooldown_iso)"
        )
    else:
        scored_clause = ""

    where = f"""
        FROM blocks
        WHERE status = 'active'
          AND category NOT IN ({placeholders})
          AND source_peer IS NULL
          {no_rescore_tag_clause}
          {scored_clause}
    """
    return where, params


async def select_rescore_candidates(
    conn: AsyncConnection,
    *,
    filt: RescoreFilter,
    max_count: int,
) -> list[str]:
    """Return up to *max_count* block ids eligible for rescoring.

    Order: NULL ``last_scored_at`` first (drains debt), then oldest
    ``last_scored_at`` ascending (progressive rotation).
    """
    if max_count <= 0:
        return []
    where, params = _build_select_query(filt, enforce_min_age=True)
    sql = f"""
        SELECT id {where}
        ORDER BY (last_scored_at IS NULL) DESC, last_scored_at ASC
        LIMIT :limit
    """
    params["limit"] = max_count
    rows = await conn.execute(text(sql), params)
    return [r[0] for r in rows.fetchall()]


async def rescore_blocks(
    engine: AsyncEngine,
    *,
    block_ids: list[str],
    llm: object,         # LLMService — typed as object to avoid circular import
    embedding_svc: object,  # EmbeddingService
    evidence_weight: float = 0.5,
    ledger_dir: Path | None = None,
) -> dict[str, int]:
    """Re-run the LLM analysis on each block id and update its scoring.

    For each block, in its own committed transaction (ADR 0007 — previously
    all blocks shared one caller-opened transaction, so a mid-run kill lost
    every already-rescored block, not just the one in flight):
    - Read content from DB.
    - Run ``llm.process_block`` (alignment + summary + tags).
    - Re-embed the new summary.
    - Fold the new alignment into the Beta posterior as one weighted
      evidence event (additive update; v0.17, ADR 0002) rather than
      clobbering ``confidence``. Mature blocks (α + β ≫ 1) barely move;
      cold blocks (still at the promotion prior, α + β = 1) track the
      new alignment closely.
    - Persist the refreshed analysis with ``last_scored_at = now``.

    On LLM timeout: leaves the block as-is (last_scored_at stays NULL or
    its old value), but counts the block as a failure. The next rescore
    invocation tries again — naturally resumable. Now durably so: a kill
    after block N commits leaves blocks 1..N rescored regardless of what
    happens to N+1.

    Does not touch contradictions or graph edges. Edge regeneration is
    deferred to a future ``--rebuild-edges`` patch (cost is O(N²)).

    Args:
        evidence_weight: weight of the new alignment as a Beta-Binomial
            evidence event. 0.0 disables the confidence update (alignment
            metadata still refreshed). Validated upstream by config.

    Returns ``{"rescored": N, "failed": M, "attempted": N+M}``.
    """
    import asyncio

    from elfmem.db.queries import get_block, update_block_scoring
    from elfmem.operations.consolidate import _LLM_PROCESS_TIMEOUT
    from elfmem.operations.outcome import compute_bayesian_update_ab

    rescored = 0
    failed = 0
    now_iso = datetime.now(UTC).isoformat()

    for block_id in block_ids:
        async with engine.begin() as conn:
            block = await get_block(conn, block_id)
            if block is None or block["status"] != "active":
                continue

            content = block["content"]
            try:
                analysis = await asyncio.wait_for(
                    llm.process_block(content, ""),  # type: ignore[attr-defined]
                    timeout=_LLM_PROCESS_TIMEOUT,
                )
            except (TimeoutError, Exception):  # noqa: BLE001 — boundary
                # LLM unreachable / timed out; leave block untouched. The next
                # rescore call retries it. This makes rescore naturally
                # resumable on partial failure.
                failed += 1
                continue

            summary_text = analysis.summary or content
            summary_vec = await embedding_svc.embed(  # type: ignore[attr-defined]
                summary_text.strip().lower()
            )

            new_alpha, new_beta, _ = compute_bayesian_update_ab(
                float(block["success_count"]),
                float(block["failure_count"]),
                signal=analysis.alignment_score,
                weight=evidence_weight,
            )

            await update_block_scoring(
                conn,
                block_id,
                self_alignment=analysis.alignment_score,
                embedding=summary_vec,
                embedding_model=embedding_svc.model_name,  # type: ignore[attr-defined]
                summary=analysis.summary,
                last_scored_at=now_iso,
                success_count=new_alpha,
                failure_count=new_beta,
            )
        # Recorded after the per-block commit, not inside it: the ledger entry
        # describes something that has already happened. Emitting before the
        # commit would let a crash leave history claiming work the index never
        # took. Without this the whole pass is invisible to a rebuild, which
        # under file authority means it is simply lost.
        if ledger_dir is not None:
            _ledger.append(
                ledger_dir,
                _ledger.KIND_RESCORE,
                active_hours=float(block["last_reinforced_at"] or 0.0),
                id=block_id,
                sig=analysis.alignment_score,
                w=evidence_weight,
                **({"sum": analysis.summary} if analysis.summary else {}),
            )
        rescored += 1

    return {"rescored": rescored, "failed": failed, "attempted": rescored + failed}


async def compute_drift_stats(
    conn: AsyncConnection, *, filt: RescoreFilter,
) -> DriftStats:
    """Aggregate drift counts for the doctor health surface.

    Counts are over the same eligibility rule as ``select_rescore_candidates``
    so "what doctor flags" and "what rescore would process" never disagree.

    Returns zero counts gracefully if the schema hasn't been migrated to
    v3 yet (the column doesn't exist) — doctor surfaces "0 unscored,
    0 stale" rather than erroring; the migration runs automatically on
    the next non-doctor command and subsequent doctor runs work normally.
    """
    from sqlalchemy.exc import OperationalError

    # We deliberately don't reuse _build_select_query's enforce_min_age=True
    # branch here — drift stats want the full picture (including blocks under
    # cooldown), even though those blocks are skipped by selection until
    # their cooldown expires.
    where, params = _build_select_query(filt, enforce_min_age=False)

    try:
        total_sql = f"SELECT COUNT(*) {where}"
        total_row = (await conn.execute(text(total_sql), params)).fetchone()
        total_active = int(total_row[0]) if total_row else 0

        unscored_sql = f"SELECT COUNT(*) {where} AND last_scored_at IS NULL"
        unscored_row = (await conn.execute(text(unscored_sql), params)).fetchone()
        unscored = int(unscored_row[0]) if unscored_row else 0

        stale_iso = (
            datetime.now(UTC) - timedelta(days=filt.target_max_age_days)
        ).isoformat()
        stale_params = {**params, "stale_iso": stale_iso}
        stale_sql = (
            f"SELECT COUNT(*) {where} "
            "AND last_scored_at IS NOT NULL AND last_scored_at < :stale_iso"
        )
        stale_row = (await conn.execute(text(stale_sql), stale_params)).fetchone()
        stale = int(stale_row[0]) if stale_row else 0
    except OperationalError:
        # Schema v2 or earlier: last_scored_at column doesn't exist yet.
        # The next full elfmem command will run the v3 migration; report
        # benign zeros for now so doctor doesn't raise on pre-migration DBs.
        return DriftStats(
            total_active=0, unscored=0, stale=0,
            target_max_age_days=filt.target_max_age_days,
        )

    return DriftStats(
        total_active=total_active,
        unscored=unscored,
        stale=stale,
        target_max_age_days=filt.target_max_age_days,
    )


# ── Edge-metabolism Stage A: dry-run only ───────────────────────────────────
# docs/plans/plan_edge_metabolism.md. Everything below is read-only: it
# proposes goal-directed connections and reports them, but never calls
# insert_edge. Applying proposals to the graph is Stage B, not yet approved
# — see the plan doc's "Decision needed" section.

GOAL_DIRECTED_CANDIDATE_CHAR_CAP: int = 400
# ~100 tokens — a summary should already be this size (average summary
# length on the real self-hosted corpus is ~290 chars); this only bites
# the fallback case (no summary yet, falls back to full content, which
# averages ~900 chars and can run to ~6000). Found necessary alongside
# GOAL_DIRECTED_SELF_GOALS_CHAR_BUDGET — 30 candidates at full content
# length reproduced the same context-window overflow that budget alone
# didn't fix; see docs/plans/plan_edge_metabolism.md's Stage A findings.


def _summary_or_content(row: dict[str, Any]) -> str:
    """Prefer a block's summary (short, LLM-written) over its full content
    for candidate representation — cheaper, and keeps a 30-candidate
    shortlist from reproducing the context-window overflow a full-content
    candidate list caused on the real corpus (see module constants above).
    """
    text: str = row.get("summary") or row["content"]
    return text[:GOAL_DIRECTED_CANDIDATE_CHAR_CAP]


def select_goal_directed_candidates(
    block_id: str,
    vec: np.ndarray,
    all_active: list[tuple[dict[str, Any], np.ndarray]],
    *,
    k: int = GOAL_DIRECTED_CANDIDATE_K,
) -> list[tuple[str, float]]:
    """Widened top-K nearest neighbours by embedding — no threshold cutoff.

    Pure function: no DB, no LLM. Deliberately wider and unthresholded
    compared to consolidate()'s similarity-edge prefilter
    (``EDGE_SCORE_THRESHOLD`` in operations/consolidate.py) — this produces
    a candidate *pool* for goal-relevance judgment, not a similarity
    decision itself. The LLM decides which, if any, of these matter; a
    numeric floor here would just re-exclude the non-obvious connections
    this mechanism exists to find.
    """
    scored: list[tuple[str, float]] = []
    for a_block, a_vec in all_active:
        if a_block["id"] == block_id:
            continue
        scored.append((a_block["id"], cosine_similarity(vec, a_vec)))
    return heapq.nlargest(k, scored, key=lambda pair: pair[1])


async def metabolism_dry_run(
    conn: AsyncConnection,
    *,
    block_ids: list[str],
    llm: object,  # LLMService — typed as object to avoid circular import
    max_edges_per_block: int = GOAL_DIRECTED_MAX_EDGES_PER_BLOCK,
    candidate_k: int = GOAL_DIRECTED_CANDIDATE_K,
) -> MetabolismDryRunResult:
    """Propose goal-directed connections for *block_ids*. Writes nothing.

    USE WHEN: sanity-checking the edge-metabolism mechanism against real
        content before any Stage B decision to apply it live — see
        docs/plans/plan_edge_metabolism.md.
    DON'T USE WHEN: you want edges actually created — this never calls
        ``insert_edge``; it only returns what it would have proposed.
    COST: one LLM call per block in *block_ids* (bounded by the caller's
        own batch size, same budget as rescore's ``max_per_run``).
    RETURNS: ``MetabolismDryRunResult`` — proposals (if the LLM produced
        any), the raw ``self_goals``/``candidates`` it judged them against
        (populated regardless of whether the LLM call succeeded — a caller
        with no configured LLM can still read these), and a count of LLM
        calls that failed and were skipped rather than aborting the run.
    NEXT: read the proposals by hand, or reason over ``self_goals``/
        ``candidates`` directly (e.g. a host agent session applying its own
        judgement via ``connect()``). This is the validation step the
        Zettelkasten-auto-linking deferral asked for before any live
        mutation mechanism ships.
    """
    from elfmem.db.queries import (
        bytes_to_embedding,
        get_active_blocks_with_embeddings,
        list_active_blocks,
    )

    # Exact tag, not the "self/%" prefix used elsewhere for the whole self
    # frame — self/goal is what "the agent's own goals" means here.
    # constitutional/value/style/context/constraint blocks are a different
    # concept (identity, not objectives) and, on a real corpus, can be an
    # order of magnitude more content than goals alone.
    goal_rows = await list_active_blocks(conn, tag="self/goal")
    self_goals: list[str] = []
    goals_char_total = 0
    for row in goal_rows:  # already ordered by last_reinforced_at DESC
        content = row["content"]
        # Always let at least one goal through, even if it alone exceeds
        # the budget — better to slightly overrun once than silently judge
        # every candidate against zero goals.
        if self_goals and goals_char_total + len(content) > GOAL_DIRECTED_SELF_GOALS_CHAR_BUDGET:
            break
        self_goals.append(content)
        goals_char_total += len(content)

    active_rows = await get_active_blocks_with_embeddings(conn)
    all_active: list[tuple[dict[str, Any], np.ndarray]] = [
        (row, bytes_to_embedding(row["embedding"])) for row in active_rows
    ]
    by_id = {row["id"]: (row, vec) for row, vec in all_active}

    proposals: list[GoalDirectedEdgeProposal] = []
    all_candidates: dict[str, list[tuple[str, str]]] = {}
    llm_failures = 0
    blocks_considered = 0

    for block_id in block_ids:
        target = by_id.get(block_id)
        if target is None:
            # Not active, or has no embedding yet — nothing to propose from.
            continue
        block_row, block_vec = target
        blocks_considered += 1

        candidates = select_goal_directed_candidates(
            block_id, block_vec, all_active, k=candidate_k,
        )
        # Recorded even when empty, and even when there are no self_goals to
        # judge against yet — a caller reasoning over this itself (no LLM
        # configured, or judging by hand) still wants to see what was there.
        candidate_pairs = [
            (cid, _summary_or_content(by_id[cid][0]))
            for cid, _score in candidates
            if cid in by_id
        ]
        all_candidates[block_id] = candidate_pairs
        if not candidate_pairs or not self_goals:
            continue

        try:
            raw_proposals = await llm.propose_goal_directed_edges(  # type: ignore[attr-defined]
                block_content=block_row["content"],
                block_summary=block_row.get("summary"),
                self_goals=self_goals,
                candidates=candidate_pairs,
                max_edges=max_edges_per_block,
            )
        except Exception:  # noqa: BLE001 — boundary; one bad block must not abort the batch
            llm_failures += 1
            continue

        valid_candidate_ids = {cid for cid, _content in candidate_pairs}
        for p in raw_proposals[:max_edges_per_block]:
            candidate_id = p.get("candidate_id", "")
            if candidate_id not in valid_candidate_ids:
                # Hallucinated id — drop silently rather than propose a
                # connection to a block that was never offered as a candidate.
                continue
            proposals.append(
                GoalDirectedEdgeProposal(
                    block_id=block_id,
                    candidate_id=candidate_id,
                    reasoning=p.get("reasoning", ""),
                )
            )

    return MetabolismDryRunResult(
        blocks_considered=blocks_considered,
        self_goals=self_goals,
        candidates=all_candidates,
        proposals=proposals,
        llm_failures=llm_failures,
    )
