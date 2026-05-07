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
CURRENT_SCHEMA_VERSION = 2


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

    # Future migrations:
    # if version < 3:
    #     await _migrate_v3_something(conn)

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


# ── Helpers ──────────────────────────────────────────────────────────────────


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
