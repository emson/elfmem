"""`doctor` validates where an MCP entry points, not just that one exists.

The old check was `"elfmem" in file.read_text()`. That reported healthy for
an entry naming a different project's config and database — a server started
from it reads another instance's memory while every surface still says fine.
That drift hid in plain sight twice before this existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elfmem.project import check_mcp_entries

PROJECT_CONFIG = "/proj/.elfmem/config.yaml"
PROJECT_DB = "/dbs/proj.db"


def _real_binary(root: Path) -> str:
    """An absolute command path that exists — the check flags ones that
    don't, so a fake path would fail for the wrong reason."""
    binary = root / ".venv" / "bin" / "elfmem"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()
    return str(binary)


def _write_claude_json(root: Path, entry: dict) -> None:
    (root / ".claude.json").write_text(
        json.dumps({"mcpServers": {"elfmem": entry}}), encoding="utf-8"
    )


def _check(root: Path):
    return check_mcp_entries(
        root, expected_config=Path(PROJECT_CONFIG), expected_db=PROJECT_DB
    )


class TestCorrectEntry:
    def test_entry_pointing_at_this_project_is_clean(self, tmp_path: Path):
        _write_claude_json(tmp_path, {
            "command": _real_binary(tmp_path),
            "args": ["serve", "--config", PROJECT_CONFIG],
        })
        reports = _check(tmp_path)
        assert len(reports) == 1
        assert reports[0].ok, reports[0].issues

    def test_no_entry_anywhere_reports_nothing_rather_than_passing(
        self, tmp_path: Path
    ):
        assert _check(tmp_path) == []


class TestWrongTarget:
    def test_config_naming_another_project_is_caught(self, tmp_path: Path):
        _write_claude_json(tmp_path, {
            "command": _real_binary(tmp_path),
            "args": ["serve", "--config", "/other/.elfmem/config.yaml"],
        })
        issues = _check(tmp_path)[0].issues
        assert any("--config points at" in i for i in issues)

    def test_db_naming_another_instance_is_caught(self, tmp_path: Path):
        _write_claude_json(tmp_path, {
            "command": _real_binary(tmp_path),
            "args": ["serve", "--config", PROJECT_CONFIG, "--db", "/dbs/other.db"],
        })
        issues = _check(tmp_path)[0].issues
        assert any("--db points at" in i for i in issues)

    def test_missing_config_arg_is_caught(self, tmp_path: Path):
        """Without --config the server falls back to a global config, so it
        may not read this project's memory at all."""
        _write_claude_json(tmp_path, {
            "command": _real_binary(tmp_path), "args": ["serve"],
        })
        assert any("no --config" in i for i in _check(tmp_path)[0].issues)

    def test_bare_command_name_is_caught(self, tmp_path: Path):
        """A bare name resolves against the spawning shell's PATH, which
        misses a project-local uv venv (ADR 0008)."""
        _write_claude_json(tmp_path, {
            "command": "elfmem", "args": ["serve", "--config", PROJECT_CONFIG],
        })
        assert any("bare name" in i for i in _check(tmp_path)[0].issues)

    def test_uv_is_not_flagged_as_a_bare_name(self, tmp_path: Path):
        """`uv run ... elfmem serve` is the documented project-local form."""
        _write_claude_json(tmp_path, {
            "command": "uv",
            "args": ["run", "elfmem", "serve", "--config", PROJECT_CONFIG],
        })
        assert _check(tmp_path)[0].ok

    def test_absolute_command_that_does_not_exist_is_caught(self, tmp_path: Path):
        _write_claude_json(tmp_path, {
            "command": "/nowhere/bin/elfmem",
            "args": ["serve", "--config", PROJECT_CONFIG],
        })
        assert any("does not exist" in i for i in _check(tmp_path)[0].issues)


class TestEveryFileIsChecked:
    def test_a_second_stale_file_is_not_hidden_by_a_good_first_one(
        self, tmp_path: Path
    ):
        """`detect_mcp_config` returns the first match, so a stale second file
        stays invisible: the entry in use looks fine while a wrong one waits
        for whichever tool reads that file instead."""
        _write_claude_json(tmp_path, {
            "command": _real_binary(tmp_path),
            "args": ["serve", "--config", PROJECT_CONFIG],
        })
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "claude-code.yaml").write_text(
            "mcpServers:\n"
            "  elfmem:\n"
            "    command: elfmem\n"
            "    args:\n"
            "      - serve\n"
            "      - --config\n"
            "      - /other/.elfmem/config.yaml\n",
            encoding="utf-8",
        )
        reports = _check(tmp_path)
        assert len(reports) == 2
        assert sum(1 for r in reports if not r.ok) == 1

    def test_nested_project_shape_is_read(self, tmp_path: Path):
        """~/.claude.json nests entries under projects[path].mcpServers."""
        (tmp_path / ".claude.json").write_text(
            json.dumps({
                "projects": {
                    "/proj": {"mcpServers": {"elfmem": {
                        "command": _real_binary(tmp_path),
                        "args": ["serve", "--config", "/other/x.yaml"],
                    }}}
                }
            }),
            encoding="utf-8",
        )
        reports = _check(tmp_path)
        assert len(reports) == 1
        assert not reports[0].ok


class TestRobustness:
    @pytest.mark.parametrize("body", ["not json at all", "", "[]"])
    def test_unparseable_config_is_skipped_not_raised(
        self, tmp_path: Path, body: str
    ):
        (tmp_path / ".claude.json").write_text(body, encoding="utf-8")
        assert _check(tmp_path) == []

    def test_non_elfmem_servers_are_ignored(self, tmp_path: Path):
        (tmp_path / ".claude.json").write_text(
            json.dumps({"mcpServers": {"other": {"command": "x"}}}),
            encoding="utf-8",
        )
        assert _check(tmp_path) == []
