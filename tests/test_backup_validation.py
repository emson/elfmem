"""Tests for backup integrity validation in db.migrate."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from elfmem.db.migrate import (
    BackupValidationError,
    _row_counts,
    _validate_backup,
    create_backup,
)


def _populate(db: Path, blocks: int = 0, peers: int = 0) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE blocks (id TEXT PRIMARY KEY);
        CREATE TABLE peer_roster (did TEXT PRIMARY KEY);
        CREATE TABLE block_tags (block_id TEXT, tag TEXT);
        CREATE TABLE edges (a TEXT, b TEXT);
    """)
    for i in range(blocks):
        conn.execute("INSERT INTO blocks VALUES (?)", (f"b{i}",))
    for i in range(peers):
        conn.execute("INSERT INTO peer_roster VALUES (?)", (f"p{i}",))
    conn.commit()
    conn.close()


class TestRowCounts:
    def test_empty_db(self, tmp_path: Path):
        db = tmp_path / "x.db"
        _populate(db)
        counts = _row_counts(db)
        assert counts == {"blocks": 0, "peer_roster": 0, "block_tags": 0, "edges": 0}

    def test_populated_db(self, tmp_path: Path):
        db = tmp_path / "x.db"
        _populate(db, blocks=5, peers=2)
        counts = _row_counts(db)
        assert counts["blocks"] == 5
        assert counts["peer_roster"] == 2

    def test_corrupt_db_returns_zeros(self, tmp_path: Path):
        db = tmp_path / "corrupt.db"
        db.write_bytes(b"not sqlite at all")
        counts = _row_counts(db)
        assert all(v == 0 for v in counts.values())


class TestValidateBackup:
    def test_matching_passes(self, tmp_path: Path):
        src = tmp_path / "src.db"
        bak = tmp_path / "src.bak"
        _populate(src, blocks=3)
        shutil.copy2(src, bak)
        # Should not raise.
        _validate_backup(src, bak)

    def test_empty_source_empty_backup_passes(self, tmp_path: Path):
        src = tmp_path / "src.db"
        bak = tmp_path / "src.bak"
        _populate(src)
        shutil.copy2(src, bak)
        _validate_backup(src, bak)

    def test_diverging_counts_raise_and_delete_stub(self, tmp_path: Path):
        src = tmp_path / "src.db"
        bak = tmp_path / "src.bak"
        _populate(src, blocks=10, peers=1)
        # Bad backup: empty.
        _populate(bak)
        assert bak.exists()
        with pytest.raises(BackupValidationError) as ei:
            _validate_backup(src, bak)
        assert "diverge" in str(ei.value)
        assert hasattr(ei.value, "recovery")
        # Stub deleted so it can't masquerade as a valid rollback.
        assert not bak.exists()

    def test_zero_to_populated_also_fails(self, tmp_path: Path):
        # Symmetric case — backup somehow has more rows than source. Still
        # divergent; still refuse.
        src = tmp_path / "src.db"
        bak = tmp_path / "src.bak"
        _populate(src)
        _populate(bak, blocks=5)
        with pytest.raises(BackupValidationError):
            _validate_backup(src, bak)


class TestCreateBackup:
    def test_returns_none_for_missing_source(self, tmp_path: Path):
        result = create_backup(str(tmp_path / "missing.db"))
        assert result is None

    def test_creates_validated_backup(self, tmp_path: Path):
        src = tmp_path / "src.db"
        _populate(src, blocks=3)
        path = create_backup(str(src), suffix="test")
        assert path is not None
        bak = Path(path)
        assert bak.exists()
        assert "test" in bak.name
        # Backup should have matching content.
        assert _row_counts(bak)["blocks"] == 3
