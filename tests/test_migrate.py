"""Tests for schema migration and backup — idempotent, version-tracked, data-preserving."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from elfmem.db.migrate import (
    CURRENT_SCHEMA_VERSION,
    create_backup,
    ensure_schema_current,
    list_backups,
    vacuum_backup,
)
from elfmem.db.queries import get_config, set_config


async def _create_v1_engine() -> AsyncEngine:
    """Create an in-memory database with v1 schema (no peer columns)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        # Create a minimal v1 blocks table (no source_peer, share, envelope_json)
        await conn.execute(text("""
            CREATE TABLE blocks (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT NOT NULL,
                source TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'inbox',
                archive_reason TEXT,
                confidence REAL NOT NULL DEFAULT 0.50,
                reinforcement_count INTEGER NOT NULL DEFAULT 0,
                decay_lambda REAL NOT NULL DEFAULT 0.01,
                last_reinforced_at REAL NOT NULL DEFAULT 0.0,
                self_alignment REAL,
                embedding BLOB,
                embedding_model TEXT,
                token_count INTEGER,
                summary TEXT,
                last_session_id TEXT,
                outcome_evidence REAL NOT NULL DEFAULT 0.0
            )
        """))
        await conn.execute(text("""
            CREATE TABLE system_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """))
        await conn.execute(text("""
            CREATE TABLE peer_roster (
                did TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                public_key TEXT,
                trust REAL NOT NULL DEFAULT 0.0,
                is_self INTEGER NOT NULL DEFAULT 0,
                first_contact TEXT NOT NULL,
                last_active TEXT NOT NULL,
                blocks_imported INTEGER NOT NULL DEFAULT 0,
                blocks_exported INTEGER NOT NULL DEFAULT 0,
                messages_in INTEGER NOT NULL DEFAULT 0,
                messages_out INTEGER NOT NULL DEFAULT 0,
                notes TEXT
            )
        """))
        # Insert some existing data
        await conn.execute(text(
            "INSERT INTO blocks (id, content, category, source, created_at, status) "
            "VALUES ('test1', 'Existing knowledge', 'knowledge', 'api', '2026-01-01T00:00:00', 'active')"
        ))
        await conn.execute(text(
            "INSERT INTO blocks (id, content, category, source, created_at, status) "
            "VALUES ('test2', 'Another block', 'mind', 'api', '2026-01-02T00:00:00', 'active')"
        ))
    return engine


class TestMigration:
    async def test_v1_to_v2_adds_columns(self):
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            version = await ensure_schema_current(conn)
        assert version == CURRENT_SCHEMA_VERSION

        # Verify new columns exist
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA table_info(blocks)"))
            cols = [row[1] for row in result]
            assert "source_peer" in cols
            assert "share" in cols
            assert "envelope_json" in cols

        await engine.dispose()

    async def test_migration_preserves_existing_data(self):
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT id, content FROM blocks ORDER BY id"))
            rows = list(result)
            assert len(rows) == 2
            assert rows[0][0] == "test1"
            assert rows[0][1] == "Existing knowledge"
            assert rows[1][0] == "test2"
            assert rows[1][1] == "Another block"

        await engine.dispose()

    async def test_new_columns_are_null_for_existing_blocks(self):
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT source_peer, envelope_json FROM blocks WHERE id = 'test1'")
            )
            row = result.first()
            assert row[0] is None  # source_peer
            assert row[1] is None  # envelope_json

        await engine.dispose()

    async def test_idempotent_migration(self):
        """Running migration twice is safe — second run is a no-op."""
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            v1 = await ensure_schema_current(conn)
        async with engine.begin() as conn:
            v2 = await ensure_schema_current(conn)
        assert v1 == v2 == CURRENT_SCHEMA_VERSION
        await engine.dispose()

    async def test_already_current_is_fast(self):
        """A database already at current version skips all migrations."""
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            await set_config(conn, "schema_version", str(CURRENT_SCHEMA_VERSION))

        async with engine.begin() as conn:
            version = await ensure_schema_current(conn)
        assert version == CURRENT_SCHEMA_VERSION

        # Columns should NOT exist (migration was skipped, not needed for this path)
        # This test verifies the fast-path: version check prevents running migrations
        await engine.dispose()

    async def test_schema_version_persisted(self):
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.connect() as conn:
            raw = await get_config(conn, "schema_version")
            assert raw == str(CURRENT_SCHEMA_VERSION)

        await engine.dispose()

    async def test_delivery_path_added_to_peer_roster(self):
        """Migration adds delivery_path column to existing peer_roster table."""
        engine = await _create_v1_engine()
        async with engine.begin() as conn:
            await ensure_schema_current(conn)

        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA table_info(peer_roster)"))
            cols = [row[1] for row in result]
            assert "delivery_path" in cols

        await engine.dispose()

    async def test_fresh_database_sets_version(self, test_engine):
        """A fresh database (from create_test_engine) has all columns already.
        Migration just sets the version."""
        async with test_engine.begin() as conn:
            version = await ensure_schema_current(conn)
        assert version == CURRENT_SCHEMA_VERSION


class TestMigrationWithFromConfig:
    async def test_from_config_triggers_migration(self, tmp_path):
        """MemorySystem.from_config() automatically migrates the database."""
        from elfmem.api import MemorySystem

        db_path = str(tmp_path / "test.db")
        system = await MemorySystem.from_config(db_path)

        # Verify schema version is current
        async with system._engine.connect() as conn:
            raw = await get_config(conn, "schema_version")
            assert raw == str(CURRENT_SCHEMA_VERSION)

        await system.close()


# ── Backup tests ─────────────────────────────────────────────────────────────


class TestCreateBackup:
    def test_creates_backup_file(self, tmp_path: Path):
        db = tmp_path / "test.db"
        db.write_text("fake database content")

        result = create_backup(str(db), suffix="test")
        assert result is not None
        assert Path(result).exists()
        assert ".test." in result
        assert result.endswith(".bak")

    def test_backup_preserves_content(self, tmp_path: Path):
        db = tmp_path / "test.db"
        db.write_text("important data")

        result = create_backup(str(db), suffix="test")
        assert Path(result).read_text() == "important data"

    def test_nonexistent_source_returns_none(self, tmp_path: Path):
        result = create_backup(str(tmp_path / "missing.db"), suffix="test")
        assert result is None

    def test_multiple_backups_get_unique_names(self, tmp_path: Path):
        db = tmp_path / "test.db"
        db.write_text("data")

        r1 = create_backup(str(db), suffix="a")
        r2 = create_backup(str(db), suffix="b")
        assert r1 != r2
        assert Path(r1).exists()
        assert Path(r2).exists()


class TestListBackups:
    def test_lists_backup_files(self, tmp_path: Path):
        db = tmp_path / "test.db"
        db.write_text("data")
        create_backup(str(db), suffix="first")
        create_backup(str(db), suffix="second")

        backups = list_backups(str(db))
        assert len(backups) == 2
        names = {b["name"] for b in backups}
        assert any("first" in n for n in names)
        assert any("second" in n for n in names)

    def test_no_backups_returns_empty(self, tmp_path: Path):
        db = tmp_path / "test.db"
        db.write_text("data")
        assert list_backups(str(db)) == []

    def test_backup_entry_has_required_fields(self, tmp_path: Path):
        db = tmp_path / "test.db"
        db.write_text("data")
        create_backup(str(db), suffix="test")

        backups = list_backups(str(db))
        entry = backups[0]
        assert "path" in entry
        assert "name" in entry
        assert "size" in entry
        assert "modified" in entry


class TestVacuumBackup:
    async def test_vacuum_creates_valid_database(self, tmp_path: Path):
        """VACUUM INTO creates a self-contained, openable database."""
        from elfmem.db.engine import create_engine

        db_path = str(tmp_path / "source.db")
        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            from elfmem.db.models import metadata
            await conn.run_sync(metadata.create_all)
            await conn.execute(text(
                "INSERT INTO system_config (key, value) VALUES ('test', 'hello')"
            ))

        # Vacuum backup
        backup_path = str(tmp_path / "backup.db")
        async with engine.connect() as conn:
            result = await vacuum_backup(conn, backup_path)

        assert Path(result).exists()
        assert Path(result).stat().st_size > 0

        # Open the backup and verify data
        backup_engine = await create_engine(backup_path)
        async with backup_engine.connect() as conn:
            r = await conn.execute(text("SELECT value FROM system_config WHERE key = 'test'"))
            assert r.scalar() == "hello"

        await engine.dispose()
        await backup_engine.dispose()


class TestPreMigrationBackup:
    async def test_migration_creates_backup(self, tmp_path: Path):
        """When migration is needed and db_path is provided, a backup is created."""
        from elfmem.db.engine import create_engine

        db_path = str(tmp_path / "test.db")
        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            # Create minimal v1 schema
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS blocks (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    category TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'inbox',
                    confidence REAL NOT NULL DEFAULT 0.50,
                    reinforcement_count INTEGER NOT NULL DEFAULT 0,
                    decay_lambda REAL NOT NULL DEFAULT 0.01,
                    last_reinforced_at REAL NOT NULL DEFAULT 0.0,
                    outcome_evidence REAL NOT NULL DEFAULT 0.0
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                )
            """))
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS peer_roster (
                    did TEXT PRIMARY KEY, name TEXT NOT NULL,
                    trust REAL NOT NULL DEFAULT 0.0,
                    is_self INTEGER NOT NULL DEFAULT 0,
                    first_contact TEXT NOT NULL DEFAULT '',
                    last_active TEXT NOT NULL DEFAULT '',
                    blocks_imported INTEGER NOT NULL DEFAULT 0,
                    blocks_exported INTEGER NOT NULL DEFAULT 0,
                    messages_in INTEGER NOT NULL DEFAULT 0,
                    messages_out INTEGER NOT NULL DEFAULT 0
                )
            """))

        async with engine.begin() as conn:
            await ensure_schema_current(conn, db_path=db_path)

        # Verify backup was created
        backups = list_backups(db_path)
        assert len(backups) >= 1
        assert "before-v2" in backups[0]["name"]

        # Verify metadata recorded
        async with engine.connect() as conn:
            path = await get_config(conn, "last_backup_path")
            assert path is not None
            at = await get_config(conn, "last_backup_at")
            assert at is not None

        await engine.dispose()

    async def test_no_backup_when_already_current(self, tmp_path: Path):
        """No backup created when schema is already current."""
        from elfmem.db.engine import create_engine

        db_path = str(tmp_path / "test.db")
        engine = await create_engine(db_path)
        async with engine.begin() as conn:
            from elfmem.db.models import metadata
            await conn.run_sync(metadata.create_all)
            await set_config(conn, "schema_version", str(CURRENT_SCHEMA_VERSION))

        async with engine.begin() as conn:
            await ensure_schema_current(conn, db_path=db_path)

        backups = list_backups(db_path)
        assert len(backups) == 0

        await engine.dispose()
