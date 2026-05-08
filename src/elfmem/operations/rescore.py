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

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

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
    conn: AsyncConnection,
    *,
    block_ids: list[str],
    llm: object,         # LLMService — typed as object to avoid circular import
    embedding_svc: object,  # EmbeddingService
) -> dict[str, int]:
    """Re-run the LLM analysis on each block id and update its scoring.

    For each block:
    - Read content from DB.
    - Run ``llm.process_block`` (alignment + summary + tags).
    - Re-embed the new summary.
    - Persist the refreshed analysis with ``last_scored_at = now``.

    On LLM timeout: leaves the block as-is (last_scored_at stays NULL or
    its old value), but counts the block as a failure. The next rescore
    invocation tries again — naturally resumable.

    Does not touch contradictions or graph edges. Edge regeneration is
    deferred to a future ``--rebuild-edges`` patch (cost is O(N²)).

    Returns ``{"rescored": N, "failed": M, "attempted": N+M}``.
    """
    import asyncio

    from elfmem.db.queries import get_block, update_block_scoring
    from elfmem.operations.consolidate import _LLM_PROCESS_TIMEOUT

    rescored = 0
    failed = 0
    now_iso = datetime.now(UTC).isoformat()

    for block_id in block_ids:
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
        await update_block_scoring(
            conn,
            block_id,
            confidence=analysis.alignment_score,
            self_alignment=analysis.alignment_score,
            embedding=summary_vec,
            embedding_model=embedding_svc.model_name,  # type: ignore[attr-defined]
            summary=analysis.summary,
            last_scored_at=now_iso,
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
