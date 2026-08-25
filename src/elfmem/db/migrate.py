"""Schema migration — automatic, idempotent, version-tracked.

elfmem stores a ``schema_version`` integer in ``system_config``.
On every startup, ``ensure_schema_current()`` compares it against
``CURRENT_SCHEMA_VERSION`` and applies any pending migrations.

Design rules:
- Migrations are additive only (ALTER TABLE ADD COLUMN, CREATE TABLE).
- Every migration is idempotent (safe to run twice).
- Each migration bumps schema_version atomically at the end.
- A backup is created automatically before the first migration runs.
- Total cost for an already-current database: one SELECT from system_config.

SQLite constraints:
- ALTER TABLE ADD COLUMN requires nullable or DEFAULT.
- Cannot modify or remove existing columns.
- Cannot add constraints to existing columns.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from elfmem.db.queries import get_config, set_config

logger = logging.getLogger(__name__)

# Bump this when adding a new migration function.
CURRENT_SCHEMA_VERSION = 9


async def ensure_schema_current(
    conn: AsyncConnection,
    *,
    db_path: str | None = None,
) -> int:
    """Apply pending migrations. Returns the final schema version.

    Called automatically by MemorySystem.from_config() on every startup.
    Cost for already-current databases: one SELECT.

    Args:
        db_path: Path to the database file. When provided and a migration
            is needed, a backup is created before any changes are made.
    """
    version = await _get_version(conn)
    if version >= CURRENT_SCHEMA_VERSION:
        return version

    # Backup before any migration (safety net). The backup is validated by
    # row count; if validation fails the migration is aborted so we never
    # mutate a DB whose rollback doesn't exist. A live populated DB whose
    # backup ends up empty is the failure mode that wiped a peer's vault in
    # the 0.13.0 path-resolution disaster — never again. (We use file-copy
    # rather than VACUUM INTO here because VACUUM cannot run inside the
    # active migration transaction.)
    if db_path:
        try:
            backup_path = create_backup(db_path, suffix=f"before-v{version + 1}")
        except BackupValidationError as e:
            logger.error("Pre-migration backup validation failed: %s", e)
            raise
        if backup_path:
            await set_config(conn, "last_backup_path", backup_path)
            await set_config(conn, "last_backup_at", _now_iso())
            logger.info("Pre-migration backup: %s", backup_path)

    if version < 2:
        await _migrate_v2_peer_communication(conn)

    if version < 3:
        await _migrate_v3_rescore_tracking(conn)

    if version < 4:
        await _migrate_v4_sufficient_statistics(conn)

    if version < 5:
        await _migrate_v5_block_amendments(conn)

    if version < 6:
        await _migrate_v6_superseded_by(conn)

    if version < 7:
        await _migrate_v7_pinned(conn)

    if version < 8:
        await _migrate_v8_block_format_v2(conn)

    if version < 9:
        await _migrate_v9_near_duplicate_pairs(conn)

    final = await _get_version(conn)
    logger.info("Schema migrated from v%d to v%d", version, final)
    return final


# ── Version helpers ──────────────────────────────────────────────────────────


async def _get_version(conn: AsyncConnection) -> int:
    """Read schema_version from system_config. Returns 1 if not set."""
    raw = await get_config(conn, "schema_version")
    if raw is None:
        return 1
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 1


# ── Migration: v1 → v2 (peer communication) ─────────────────────────────────


async def _migrate_v2_peer_communication(conn: AsyncConnection) -> None:
    """Add peer communication columns to blocks table.

    New columns (all nullable, safe for ALTER TABLE ADD COLUMN):
    - source_peer TEXT  — DID of originating peer (None = local)
    - share TEXT        — private | public | peer (default: private)
    - envelope_json TEXT — JSON envelope for message blocks
    - delivery_path TEXT — on peer_roster: path to peer's inbox
    """
    await _add_column(conn, "blocks", "source_peer", "TEXT")
    await _add_column(conn, "blocks", "share", "TEXT DEFAULT 'private'")
    await _add_column(conn, "blocks", "envelope_json", "TEXT")

    # peer_roster table is created by metadata.create_all (it's a new table).
    # But if it was created before delivery_path was added, add the column.
    await _add_column(conn, "peer_roster", "delivery_path", "TEXT")

    # Create index on source_peer for efficient peer block queries
    await _add_index(conn, "idx_blocks_source_peer", "blocks", "source_peer")

    await set_config(conn, "schema_version", "2")
    logger.info("Migration v2 complete: peer communication columns added")


# ── Migration: v2 → v3 (deep-sleep rescoring) ───────────────────────────────


async def _migrate_v3_rescore_tracking(conn: AsyncConnection) -> None:
    """Add the column that supports deep-sleep rescoring.

    New column:
    - last_scored_at TEXT — ISO timestamp of the most recent LLM-pass that
      produced this block's alignment / summary / tags. NULL means "never
      LLM-scored" (set explicitly by --no-llm or LLM-timeout fallback);
      this is what makes a block first in line for ``dream --rescore``.

    Backfill: existing blocks are stamped with their ``created_at`` so they
    sort by age in the rescore queue. This is synthetic but conservative —
    the oldest blocks become the first rescore candidates, which is the
    correct default. Truly-unscored blocks (those promoted with skip_llm=True
    or via LLM timeout going forward) carry NULL; the migration backfill
    does not retroactively set NULL on blocks whose history we don't know.
    """
    await _add_column(conn, "blocks", "last_scored_at", "TEXT")
    # Idempotent: only fills NULL → created_at, leaves real values alone.
    await conn.execute(text(
        "UPDATE blocks SET last_scored_at = created_at "
        "WHERE last_scored_at IS NULL"
    ))
    await _add_index(conn, "idx_blocks_last_scored_at", "blocks", "last_scored_at")
    await set_config(conn, "schema_version", "3")
    logger.info("Migration v3 complete: rescore tracking columns added")


# ── Migration: v3 → v4 (Bayesian sufficient statistics) ─────────────────────


async def _migrate_v4_sufficient_statistics(conn: AsyncConnection) -> None:
    """Add (success_count, failure_count) — the Beta posterior sufficient stats.

    From v0.17 onwards every outcome update writes (α, β) directly; confidence
    is the denormalised view ``α / (α + β)`` and ``outcome_evidence`` is the
    denormalised view ``(α + β) - 1.0``. Storing the sufficient statistics
    makes peer merge mathematically sound (you can sum α-priors and β-priors;
    you cannot sum confidences) and lets rescoring update the prior without
    discarding accumulated evidence.

    Bootstrap formula for existing rows:
        α = confidence × (1 + outcome_evidence)
        β = (1 - confidence) × (1 + outcome_evidence)

    This preserves both the current confidence (α / (α + β) = confidence) and
    the cumulative event count (α + β - 1 = outcome_evidence). New blocks
    receive Jeffreys priors α=β=0.5, set by the column DEFAULT. We only
    bootstrap rows still sitting at the default sentinel — never overwrite
    sufficient statistics that have already been computed.
    """
    await _add_column(
        conn, "blocks", "success_count", "REAL NOT NULL DEFAULT 0.5",
    )
    await _add_column(
        conn, "blocks", "failure_count", "REAL NOT NULL DEFAULT 0.5",
    )
    # Bootstrap only rows still at the default sentinel (0.5, 0.5).
    # Anything else is already trusted state and must not be overwritten.
    await conn.execute(text(
        "UPDATE blocks "
        "SET success_count = confidence * (1.0 + outcome_evidence), "
        "    failure_count = (1.0 - confidence) * (1.0 + outcome_evidence) "
        "WHERE success_count = 0.5 AND failure_count = 0.5"
    ))
    await set_config(conn, "schema_version", "4")
    logger.info("Migration v4 complete: Bayesian sufficient statistics added")


# ── Migration: v4 → v5 (constitutional review — block_amendments audit) ─────


async def _migrate_v5_block_amendments(conn: AsyncConnection) -> None:
    """Add the ``block_amendments`` audit table for constitutional review.

    A block_amendments row records one edit to a block's content: the
    pre/post content and summary, the LLM-supplied rationale for the change,
    a drift score in [0, 1], and the acceptor (agent | user | system). When
    an amendment is reverted, ``reverted_at`` is stamped non-null so history
    is preserved rather than deleted.

    No existing data needs backfill — the table starts empty. Re-running the
    migration is safe because ``CREATE TABLE IF NOT EXISTS`` and
    ``CREATE INDEX IF NOT EXISTS`` are idempotent.
    """
    await conn.execute(text("""
        CREATE TABLE IF NOT EXISTS block_amendments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
            timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            pre_content TEXT NOT NULL,
            post_content TEXT NOT NULL,
            pre_summary TEXT,
            post_summary TEXT,
            drift_score REAL NOT NULL,
            rationale TEXT,
            acceptor TEXT NOT NULL CHECK (acceptor IN ('agent', 'user', 'system')),
            reverted_at TIMESTAMP
        )
    """))
    await _add_index(
        conn, "idx_block_amendments_block_id", "block_amendments", "block_id",
    )
    await _add_index(
        conn, "idx_block_amendments_timestamp", "block_amendments", "timestamp",
    )
    await set_config(conn, "schema_version", "5")
    logger.info("Migration v5 complete: block_amendments audit table added")


# ── Migration: v5 → v6 (supersession audit trail) ───────────────────────────


async def _migrate_v6_superseded_by(conn: AsyncConnection) -> None:
    """Add ``superseded_by`` — the id of the block that replaced this one.

    Until now, supersession wrote ``archive_reason='superseded'`` with no
    record of *which* block did the superseding — the archived row carried
    only that *something* replaced it, never what. This is the first half of
    closing that gap (the second half is the pin guard in
    ``operations/consolidate.py`` that refuses to supersede a
    ``self/constitutional`` block at all — see docs/plans/plan_v2_substrate_reevaluation.md
    §5.3/§9 step 1). Existing archived rows have no way to recover which
    block superseded them, so this is left NULL on backfill rather than
    guessed at.
    """
    await _add_column(conn, "blocks", "superseded_by", "TEXT")
    await set_config(conn, "schema_version", "6")
    logger.info("Migration v6 complete: superseded_by audit column added")


async def _migrate_v7_pinned(conn: AsyncConnection) -> None:
    """Add ``pinned`` — the column Invariant 5 always assumed existed.

    ``pinned:`` has been written into markdown frontmatter since the v2
    export landed, but there was no column, no reader, and no enforcement
    anywhere in ``src/`` — so "a pinned block is never proposed for removal"
    was a declared invariant with no implementation behind it. The automatic
    supersession guard in ``operations/consolidate.py`` checked the
    ``self/constitutional`` tag directly instead, which is narrower: it
    protects the constitution and nothing else.

    Backfill sets ``pinned=1`` for exactly the blocks that tag-based guard
    already protected, so this migration changes no behaviour on its own —
    it only gives the guard a column to generalise onto.
    """
    await _add_column(conn, "blocks", "pinned", "INTEGER NOT NULL DEFAULT 0")
    # A database old enough to predate block_tags has no tags to backfill
    # from; the column default (0) is already correct there. Checked
    # explicitly rather than caught, so a real failure still surfaces.
    if await _table_exists(conn, "block_tags"):
        await conn.execute(
            text(
                "UPDATE blocks SET pinned = 1 WHERE id IN ("
                "  SELECT block_id FROM block_tags WHERE tag = 'self/constitutional'"
                ")"
            )
        )
    await set_config(conn, "schema_version", "7")
    logger.info("Migration v7 complete: pinned column added and backfilled")


async def _migrate_v8_block_format_v2(conn: AsyncConnection) -> None:
    """Add the block-format-v2 declared fields.

    ``blocks.cue`` and ``blocks.volatility_class`` are authored, not computed,
    so they stay NULL on backfill: guessing a cue would defeat its purpose,
    which is that a writer who had the context says when the block matters.
    Existing blocks get theirs from a deliberate backfill pass, not from a
    migration inventing them.

    ``edges.declared_by`` records the declaring endpoint of a typed link. Also
    NULL on backfill: existing edges are canonicalised (min, max) and their
    original direction is not recoverable.
    """
    await _add_column(conn, "blocks", "cue", "TEXT")
    await _add_column(conn, "blocks", "volatility_class", "TEXT")
    await _add_column(conn, "edges", "declared_by", "TEXT")
    await set_config(conn, "schema_version", "8")
    logger.info("Migration v8 complete: cue, volatility_class, declared_by added")


async def _migrate_v9_near_duplicate_pairs(conn: AsyncConnection) -> None:
    """Let the contradictions table also record near-duplicate pairs.

    Automatic supersession used to archive the existing block outright: 41 of
    the 187 blocks ever created on the maintainer's instance died that way,
    including six constitutional ones, with no audit row and no undo. Keeping
    both and recording the pair costs about 11% more corpus tokens and makes
    the loss impossible.

    Existing rows are contradictions by definition, which is exactly what the
    column default gives them.
    """
    await _add_column(
        conn, "contradictions", "kind", "TEXT NOT NULL DEFAULT 'contradiction'"
    )
    await _add_column(conn, "contradictions", "cue_similarity", "FLOAT")
    await set_config(conn, "schema_version", "9")
    logger.info("Migration v9 complete: near-duplicate pair recording added")


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _table_exists(conn: AsyncConnection, table: str) -> bool:
    """Does *table* exist in this database?"""
    result = await conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:t LIMIT 1"),
        {"t": table},
    )
    return result.first() is not None


async def _add_column(
    conn: AsyncConnection, table: str, column: str, col_type: str,
) -> None:
    """Add a column to a table if it doesn't exist. Idempotent."""
    import contextlib
    with contextlib.suppress(OperationalError):
        await conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        )


async def _add_index(
    conn: AsyncConnection, name: str, table: str, column: str,
) -> None:
    """Create an index if it doesn't exist. Idempotent."""
    import contextlib
    with contextlib.suppress(OperationalError):
        await conn.execute(
            text(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")
        )


# ── Backup ───────────────────────────────────────────────────────────────────


_VALIDATION_TABLES: tuple[str, ...] = ("blocks", "peer_roster", "block_tags", "edges")


def _row_counts(db_path: Path) -> dict[str, int]:
    """Return row counts for the canonical content tables. Missing tables → 0.

    Used to validate that a backup actually contains the source data, rather
    than being a stub of an empty/freshly-created DB. The 0.13.0 disaster
    happened because a backup was technically created but contained nothing,
    while the operator believed it was a recoverable snapshot.
    """
    import sqlite3
    counts: dict[str, int] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.cursor()
            for table in _VALIDATION_TABLES:
                try:
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    counts[table] = int(cur.fetchone()[0])
                except sqlite3.OperationalError:
                    counts[table] = 0  # table doesn't exist
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return dict.fromkeys(_VALIDATION_TABLES, 0)
    return counts


def _validate_backup(src: Path, backup: Path) -> None:
    """Open *backup* and confirm row counts match *src*. Raise on mismatch.

    A valid backup either:
    - Has identical row counts in every validation table, OR
    - Is empty AND the source is empty (fresh install case).

    Anything else is a stub — we delete it and raise so the caller does not
    proceed with a destructive operation under the false impression that a
    rollback exists.
    """
    import contextlib
    src_counts = _row_counts(src)
    bak_counts = _row_counts(backup)
    if src_counts != bak_counts:
        with contextlib.suppress(OSError):
            backup.unlink()
        raise BackupValidationError(
            f"backup row counts diverge from source — refusing to proceed. "
            f"source={src_counts}, backup={bak_counts}",
            recovery=(
                "This usually means the source DB is being written by another "
                "process. Stop other elfmem processes and retry, or run "
                "'elfmem backup --vacuum' for a transactional snapshot."
            ),
        )


class BackupValidationError(Exception):
    """Raised when a created backup fails post-write integrity validation.

    Carries a ``.recovery`` field per the agent-first contract.
    """

    def __init__(self, message: str, *, recovery: str) -> None:
        super().__init__(message)
        self.recovery = recovery

    def __str__(self) -> str:
        return f"{super().__str__()} — Recovery: {self.recovery}"


def create_backup(db_path: str, *, suffix: str = "backup") -> str | None:
    """Create a timestamped, content-validated copy of the database file.

    Returns the backup path on success, None if the source doesn't exist.

    Validation: after the file copy, the backup is opened and its row counts
    in canonical content tables (blocks, peer_roster, block_tags, edges) are
    compared with the source. If they diverge, the stub is deleted and
    ``BackupValidationError`` is raised so the caller does not proceed with
    a destructive operation under the false impression that a rollback exists.

    For a WAL-clean snapshot (preferred for migration backups), use
    ``vacuum_backup()`` instead — it works through SQLite's transaction layer.
    """
    src = Path(db_path)
    if not src.exists():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = src.with_suffix(f".{suffix}.{timestamp}.bak")
    shutil.copy2(src, backup)
    _validate_backup(src, backup)
    logger.info("Database backed up: %s (%.1f KB)", backup.name, backup.stat().st_size / 1024)
    return str(backup)


async def vacuum_backup(conn: AsyncConnection, output_path: str) -> str:
    """Create a clean, self-contained backup using VACUUM INTO.

    Unlike ``create_backup()``, this produces a single file with no
    pending WAL state — ideal for archival or transfer. Slower than
    a file copy because it rebuilds the database.

    Returns the output path.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    await conn.execute(text(f"VACUUM INTO '{out}'"))
    logger.info("Vacuum backup: %s (%.1f KB)", out.name, out.stat().st_size / 1024)
    return str(out)


def list_backups(db_path: str) -> list[dict[str, str | int]]:
    """List all backup files for a database, newest first."""
    src = Path(db_path)
    pattern = f"{src.stem}.*.bak"
    backups = sorted(src.parent.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "path": str(p),
            "name": p.name,
            "size": p.stat().st_size,
            "modified": datetime.fromtimestamp(p.stat().st_mtime, tz=UTC).isoformat(),
        }
        for p in backups
    ]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
