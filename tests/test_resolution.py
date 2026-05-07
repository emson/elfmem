"""Tests for project.py resolution: env vars, relative paths, test-mode guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from elfmem.exceptions import ConfigError
from elfmem.project import (
    _CONFIG_ENV_NAMES,
    _DB_ENV_NAMES,
    _read_env,
    _read_project_db,
    resolve_config,
    resolve_db,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip elfmem env vars and the deprecation-warned cache before each test."""
    for name in (*_CONFIG_ENV_NAMES, *_DB_ENV_NAMES, "ELFMEM_ALLOW_GLOBAL_FALLBACK"):
        monkeypatch.delenv(name, raising=False)
    # Reset the warned-once memo so deprecation warnings fire again per test.
    from elfmem import project
    project._warned_envs.clear()


class TestReadEnv:
    def test_only_canonical_set(self, monkeypatch):
        monkeypatch.setenv("ELFMEM_DB", "/x")
        value, source = _read_env(_DB_ENV_NAMES)
        assert value == "/x"
        assert source == "ELFMEM_DB"

    def test_only_deprecated_set(self, monkeypatch, capsys):
        monkeypatch.setenv("ELFMEM_DB_PATH", "/y")
        value, source = _read_env(_DB_ENV_NAMES)
        assert value == "/y"
        assert source == "ELFMEM_DB_PATH"
        # One-time stderr deprecation warning.
        assert "deprecated" in capsys.readouterr().err

    def test_both_set_same_value(self, monkeypatch, capsys):
        monkeypatch.setenv("ELFMEM_DB", "/x")
        monkeypatch.setenv("ELFMEM_DB_PATH", "/x")
        value, source = _read_env(_DB_ENV_NAMES)
        assert value == "/x"
        assert source == "ELFMEM_DB"
        assert "deprecated" in capsys.readouterr().err

    def test_both_set_conflict_raises(self, monkeypatch):
        monkeypatch.setenv("ELFMEM_DB", "/x")
        monkeypatch.setenv("ELFMEM_DB_PATH", "/y")
        with pytest.raises(ConfigError) as ei:
            _read_env(_DB_ENV_NAMES)
        assert "ELFMEM_DB" in str(ei.value)
        assert "ELFMEM_DB_PATH" in str(ei.value)
        assert ei.value.recovery == "unset ELFMEM_DB_PATH"

    def test_neither_set(self):
        value, source = _read_env(_DB_ENV_NAMES)
        assert value is None
        assert source is None

    def test_warning_fires_only_once(self, monkeypatch, capsys):
        monkeypatch.setenv("ELFMEM_DB_PATH", "/y")
        _read_env(_DB_ENV_NAMES)
        first = capsys.readouterr().err
        _read_env(_DB_ENV_NAMES)
        second = capsys.readouterr().err
        assert "deprecated" in first
        assert second == ""  # silent on subsequent calls


class TestRelativeProjectDb:
    def test_absolute_path_kept(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  db: /abs/path/x.db\n", encoding="utf-8")
        assert _read_project_db(str(cfg)) == "/abs/path/x.db"

    def test_tilde_expanded(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  db: ~/foo.db\n", encoding="utf-8")
        result = _read_project_db(str(cfg))
        assert result == str(Path.home() / "foo.db")

    def test_relative_kept_relative(self, tmp_path: Path):
        # Bare-relative paths are kept as-is (cwd-relative), matching 0.12.x.
        # The 0.13.0 config-dir-relative semantics silently relocated existing
        # users' DBs and have been reverted in 0.13.1.
        config_dir = tmp_path / "myproj" / ".elfmem"
        config_dir.mkdir(parents=True)
        cfg = config_dir / "config.yaml"
        cfg.write_text("project:\n  db: elf_vault.db\n", encoding="utf-8")
        result = _read_project_db(str(cfg))
        # The string is left unmodified — caller resolves via Path() at use time.
        assert result == "elf_vault.db"

    def test_relative_dot_prefix_kept(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  db: ./db/local.db\n", encoding="utf-8")
        # ./prefix is preserved; resolution happens at call site against cwd.
        assert _read_project_db(str(cfg)) == "db/local.db"


class TestResolveDbWithConfig:
    def test_uses_config_project_db(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  db: /from/config.db\n", encoding="utf-8")
        # Allow the global fallback path (we don't take it here, but just in case).
        monkeypatch.setenv("ELFMEM_ALLOW_GLOBAL_FALLBACK", "1")
        path, source = resolve_db(None, str(cfg))
        assert path == "/from/config.db"
        assert "project.db in config" in source

    def test_explicit_wins(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("ELFMEM_DB", "/from/env.db")
        path, source = resolve_db("/from/flag.db", None)
        assert path == "/from/flag.db"
        assert source == "explicit flag"

    def test_env_wins_over_config(self, tmp_path: Path, monkeypatch):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  db: /from/config.db\n", encoding="utf-8")
        monkeypatch.setenv("ELFMEM_DB", "/from/env.db")
        path, _ = resolve_db(None, str(cfg))
        assert path == "/from/env.db"


class TestPytestGuard:
    def test_global_fallback_blocked_under_pytest(self):
        # No env vars, no config — would normally fall through to ~/.elfmem/agent.db.
        # Under pytest, this is a leak, so we refuse.
        with pytest.raises(ConfigError) as ei:
            resolve_db(None, None)
        assert "global fallback" in str(ei.value).lower() or "pytest" in str(ei.value).lower()
        assert "ELFMEM_DB" in str(ei.value)

    def test_opt_out_allows_fallback(self, monkeypatch):
        monkeypatch.setenv("ELFMEM_ALLOW_GLOBAL_FALLBACK", "1")
        path, source = resolve_db(None, None)
        assert path.endswith("agent.db")
        assert "global fallback" in source

    def test_explicit_db_bypasses_guard(self):
        # Guard only applies to the fallback step; explicit paths skip it.
        path, _ = resolve_db("/some/explicit.db", None)
        assert path == "/some/explicit.db"

    def test_env_db_bypasses_guard(self, monkeypatch):
        monkeypatch.setenv("ELFMEM_DB", "/from/env.db")
        path, _ = resolve_db(None, None)
        assert path == "/from/env.db"


class TestResolveConfigEnv:
    def test_env_uses_canonical(self, monkeypatch):
        monkeypatch.setenv("ELFMEM_CONFIG", "/canonical.yaml")
        path, source = resolve_config()
        assert path == "/canonical.yaml"
        assert "ELFMEM_CONFIG env" in source

    def test_env_accepts_deprecated(self, monkeypatch, capsys):
        monkeypatch.setenv("ELFMEM_CONFIG_PATH", "/old.yaml")
        path, source = resolve_config()
        assert path == "/old.yaml"
        assert "ELFMEM_CONFIG_PATH env" in source
        assert "deprecated" in capsys.readouterr().err
