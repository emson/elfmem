"""Tests for lifecycle.is_established_instance — the state detector that
makes ``elfmem init`` state-aware in v0.13.2."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from elfmem.lifecycle import EstablishmentState, is_established_instance


def _empty_db(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE blocks (id TEXT PRIMARY KEY);
        CREATE TABLE peer_roster (did TEXT PRIMARY KEY);
        CREATE TABLE block_tags (block_id TEXT, tag TEXT);
        CREATE TABLE edges (a TEXT, b TEXT);
    """)
    conn.commit()
    conn.close()
    return path


def _populate(db: Path, *, blocks: int = 0, peers: int = 0) -> Path:
    _empty_db(db)
    conn = sqlite3.connect(db)
    for i in range(blocks):
        conn.execute("INSERT INTO blocks VALUES (?)", (f"b{i}",))
    for i in range(peers):
        conn.execute("INSERT INTO peer_roster VALUES (?)", (f"p{i}",))
    conn.commit()
    conn.close()
    return db


class TestFreshBranches:
    def test_no_config_is_fresh(self):
        state = is_established_instance(None, None)
        assert state.kind == "fresh"
        assert not state.established
        assert state.suggested_command == "elfmem init"

    def test_config_path_does_not_exist_is_fresh(self, tmp_path: Path):
        state = is_established_instance(tmp_path / "missing.yaml", None)
        assert state.kind == "fresh"

    def test_no_db_path_is_fresh(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        state = is_established_instance(cfg, None)
        assert state.kind == "fresh"

    def test_db_file_missing_is_fresh(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        state = is_established_instance(cfg, tmp_path / "x.db")
        assert state.kind == "fresh"
        assert "does not exist" in state.reason

    def test_empty_db_is_fresh(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        db = _empty_db(tmp_path / "x.db")
        state = is_established_instance(cfg, db)
        assert state.kind == "fresh"
        assert "no content rows" in state.reason


class TestEstablishedBranch:
    def test_populated_db_is_established(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        db = _populate(tmp_path / "x.db", blocks=5, peers=2)
        state = is_established_instance(cfg, db)
        assert state.established
        assert state.kind == "established"
        assert state.block_count == 5
        assert state.peer_count == 2
        assert state.suggested_command == "elfmem init"

    def test_one_block_is_established(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        db = _populate(tmp_path / "x.db", blocks=1)
        state = is_established_instance(cfg, db)
        assert state.kind == "established"


class TestOrphanBranch:
    def test_empty_configured_with_populated_neighbour_is_orphan(
        self, tmp_path: Path,
    ):
        # Reproduces the 0.13.0 disaster shape.
        proj = tmp_path / "vault"
        elfmem = proj / ".elfmem"
        elfmem.mkdir(parents=True)
        cfg = elfmem / "config.yaml"
        cfg.write_text("project:\n  name: vault\n  db: vault.db\n")
        configured = _empty_db(elfmem / "vault.db")
        _populate(proj / "vault.db", blocks=42)
        state = is_established_instance(cfg, configured)
        assert state.kind == "orphan"
        assert not state.established
        assert state.suggested_command == "elfmem rescue"
        assert state.rescue_plan is not None
        assert state.rescue_plan.action == "rebind"


class TestUnreadableBranch:
    def test_corrupt_db_does_not_classify_as_established(
        self, tmp_path: Path,
    ):
        # SQLite is permissive about non-DB files in connect() — they may
        # appear as empty DBs (counts=0) rather than producing errors.
        # Either outcome is acceptable; we just verify never-established.
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        broken = tmp_path / "x.db"
        broken.write_bytes(b"this is not a sqlite database at all")
        state = is_established_instance(cfg, broken)
        assert state.kind in ("unreadable", "fresh")
        assert not state.established


class TestSerialisation:
    def test_to_dict_includes_lifecycle_fields(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n  db: x.db\n")
        db = _populate(tmp_path / "x.db", blocks=3, peers=1)
        state = is_established_instance(cfg, db)
        d = state.to_dict()
        assert d["established"] is True
        assert d["kind"] == "established"
        assert d["block_count"] == 3
        assert d["peer_count"] == 1
        assert d["suggested_command"] == "elfmem init"
        assert d["rescue_plan"] is None

    def test_to_dict_orphan_includes_rescue_plan(self, tmp_path: Path):
        proj = tmp_path / "p"
        elfmem = proj / ".elfmem"
        elfmem.mkdir(parents=True)
        cfg = elfmem / "config.yaml"
        cfg.write_text("project:\n  name: p\n  db: p.db\n")
        configured = _empty_db(elfmem / "p.db")
        _populate(proj / "p.db", blocks=10)
        state = is_established_instance(cfg, configured)
        d = state.to_dict()
        assert d["kind"] == "orphan"
        assert d["rescue_plan"] is not None
        assert d["rescue_plan"]["action"] == "rebind"


class TestEstablishmentStateSurface:
    def test_suggested_command_branches(self):
        for kind, expected in (
            ("fresh", "elfmem init"),
            ("established", "elfmem init"),
            ("orphan", "elfmem rescue"),
            ("unreadable", "elfmem doctor"),
        ):
            s = EstablishmentState(established=False, kind=kind, reason="x")
            assert s.suggested_command == expected
