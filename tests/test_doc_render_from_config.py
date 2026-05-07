"""Tests that the agent doc render uses live config (Bug A fix in v0.13.2).

The 0.13.x series shipped with init writing CLAUDE.md/AGENTS.md from
inferred defaults (directory basename + ``~/.elfmem/databases/{dir}.db``)
rather than from the live ``.elfmem/config.yaml``. This caused the rendered
section to lie about where the DB actually lived — same shape of failure
as the 0.13.0 path-resolution regression. v0.13.2 fixes it by reading the
config inside the render path.
"""

from __future__ import annotations

from pathlib import Path

from elfmem.project import _build_section, read_render_values_from_config


class TestReadRenderValuesFromConfig:
    def test_full_project_section(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: my-app\n  db: /abs/path/x.db\n")
        name, db = read_render_values_from_config(cfg)
        assert name == "my-app"
        assert db == "/abs/path/x.db"

    def test_missing_name_returns_empty(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  db: x.db\n")
        name, db = read_render_values_from_config(cfg)
        assert name == ""
        assert db == "x.db"

    def test_missing_db_returns_empty(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: x\n")
        name, db = read_render_values_from_config(cfg)
        assert name == "x"
        assert db == ""

    def test_no_project_section_returns_empties(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("llm:\n  model: foo\n")
        name, db = read_render_values_from_config(cfg)
        assert name == ""
        assert db == ""

    def test_missing_file_returns_empties(self, tmp_path: Path):
        name, db = read_render_values_from_config(tmp_path / "absent.yaml")
        assert name == ""
        assert db == ""

    def test_malformed_yaml_returns_empties(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("not: valid: yaml: at all: [\n")
        name, db = read_render_values_from_config(cfg)
        # Never raises; renders empty so caller can omit lines.
        assert name == ""
        assert db == ""

    def test_strips_whitespace(self, tmp_path: Path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("project:\n  name: '  my-app  '\n  db: '  x.db  '\n")
        name, db = read_render_values_from_config(cfg)
        assert name == "my-app"
        assert db == "x.db"


class TestBuildSectionRendering:
    def test_includes_marker_comment(self):
        out = _build_section(
            name="x", db_path="x.db",
            config_path="/tmp/c.yaml", identity="",
        )
        assert "auto-generated from" in out
        assert "edit OUTSIDE these markers" in out

    def test_omits_project_when_name_empty(self):
        out = _build_section(
            name="", db_path="x.db",
            config_path="/tmp/c.yaml", identity="",
        )
        assert "Project:**" not in out
        assert "Database:** `x.db`" in out

    def test_omits_database_when_db_empty(self):
        out = _build_section(
            name="x", db_path="",
            config_path="/tmp/c.yaml", identity="",
        )
        assert "Project:** x" in out
        assert "Database:**" not in out

    def test_omits_both_when_both_empty(self):
        # Faithful to config: missing fields are missing, not fabricated.
        out = _build_section(
            name="", db_path="",
            config_path="/tmp/c.yaml", identity="",
        )
        assert "Project:**" not in out
        assert "Database:**" not in out
        # Config line still present — the file path is always knowable.
        assert "Config:** `/tmp/c.yaml`" in out

    def test_renders_provided_values_verbatim(self):
        # Bug A regression test: caller passes config-derived name/db,
        # render uses them as-is. Critical that the render does NOT
        # second-guess the caller (no inference, no normalization).
        out = _build_section(
            name="elf_vault",  # NOT 'elf-vault-proj' (the dir basename)
            db_path="elf_vault.db",  # NOT '~/.elfmem/databases/...' (default)
            config_path="/proj/.elfmem/config.yaml",
            identity="",
        )
        assert "Project:** elf_vault" in out
        assert "Database:** `elf_vault.db`" in out
        # Crucially: the inferred-default values should NOT appear.
        assert "elf-vault-proj" not in out
        assert ".elfmem/databases" not in out

    def test_includes_init_in_quick_commands(self):
        out = _build_section(
            name="x", db_path="x.db",
            config_path="/tmp/c.yaml", identity="",
        )
        # init is now the canonical idempotent setup verb; it's listed.
        assert "elfmem init" in out
        # rescue is still listed for the orphan-recovery case.
        assert "elfmem rescue" in out
