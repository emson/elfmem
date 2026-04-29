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

    # Backup before any migration (safety net)
    if db_path:
        backup_path = create_backup(db_path, suffix=f"before-v{version + 1}")
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


def create_backup(db_path: str, *, suffix: str = "backup") -> str | None:
    """Create a timestamped copy of the database file.

    Returns the backup path on success, None if the source doesn't exist.
    The backup is a simple file copy (shutil.copy2) — fast and preserves
    metadata. For a clean, WAL-free backup, use ``vacuum_backup()`` instead.
    """
    src = Path(db_path)
    if not src.exists():
        return None
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = src.with_suffix(f".{suffix}.{timestamp}.bak")
    shutil.copy2(src, backup)
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
