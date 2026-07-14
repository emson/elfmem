"""Tests for project.py's MCP snippet generation and env-file loading (ADR 0008)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from elfmem.project import (
    _resolve_elfmem_command,
    load_env_file,
    mcp_json_snippet,
    parse_env_file,
)


class TestResolveElfmemCommand:
    def test_returns_absolute_path(self):
        resolved = _resolve_elfmem_command()
        assert Path(resolved).is_absolute()

    def test_falls_back_to_executable_sibling_when_which_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # shutil.which("elfmem") failing (e.g. no PATH entry for a project
        # venv) must not fall through to sys.argv[0] when the console script
        # sits right next to the running interpreter, as it always does for
        # a pip/uv-installed venv.
        monkeypatch.setattr("elfmem.project.shutil.which", lambda name: None)
        resolved = _resolve_elfmem_command()
        expected_sibling = Path(sys.executable).parent / "elfmem"
        assert resolved == str(expected_sibling.resolve())
        # The unresolved venv bin/ dir is the right answer — resolving
        # sys.executable first would follow its symlink to a shared
        # interpreter directory with no elfmem console script in it.
        assert expected_sibling.parent != Path(sys.executable).resolve().parent

    def test_falls_back_to_argv0_when_no_sibling_exists(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        # Neither which() nor a sibling console script resolves (e.g.
        # invoked via 'python -m elfmem.cli' from a bare interpreter) —
        # last-resort fallback to argv[0], not a crash.
        monkeypatch.setattr("elfmem.project.shutil.which", lambda name: None)
        fake_python = tmp_path / "python3"
        fake_python.write_text("", encoding="utf-8")
        monkeypatch.setattr("elfmem.project.sys.executable", str(fake_python))
        monkeypatch.setattr("elfmem.project.sys.argv", ["/some/argv0/path"])
        resolved = _resolve_elfmem_command()
        assert resolved == str(Path("/some/argv0/path").resolve())


class TestMcpJsonSnippet:
    def test_command_is_absolute_path_not_bare_string(self):
        snippet = json.loads(mcp_json_snippet(config_path="/x/.elfmem/config.yaml"))
        command = snippet["mcpServers"]["elfmem"]["command"]
        assert command != "elfmem"
        assert Path(command).is_absolute()

    def test_args_contains_config_path(self):
        snippet = json.loads(mcp_json_snippet(config_path="/x/.elfmem/config.yaml"))
        args = snippet["mcpServers"]["elfmem"]["args"]
        assert args == ["serve", "--config", "/x/.elfmem/config.yaml"]

    def test_no_env_file_flag_when_project_root_omitted(self):
        snippet = json.loads(mcp_json_snippet(config_path="/x/.elfmem/config.yaml"))
        assert "--env-file" not in snippet["mcpServers"]["elfmem"]["args"]

    def test_no_env_file_flag_when_dotenv_absent(self, tmp_path: Path):
        snippet = json.loads(
            mcp_json_snippet(
                config_path="/x/.elfmem/config.yaml", project_root=tmp_path
            )
        )
        assert "--env-file" not in snippet["mcpServers"]["elfmem"]["args"]

    def test_env_file_flag_added_when_dotenv_present(self, tmp_path: Path):
        (tmp_path / ".env").write_text("OPENAI_API_KEY=x\n", encoding="utf-8")
        snippet = json.loads(
            mcp_json_snippet(
                config_path="/x/.elfmem/config.yaml", project_root=tmp_path
            )
        )
        args = snippet["mcpServers"]["elfmem"]["args"]
        assert "--env-file" in args
        env_file_arg = args[args.index("--env-file") + 1]
        assert env_file_arg == str((tmp_path / ".env").resolve())

    def test_never_embeds_literal_secret_values(self, tmp_path: Path):
        (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-super-secret\n", encoding="utf-8")
        snippet = mcp_json_snippet(
            config_path="/x/.elfmem/config.yaml", project_root=tmp_path
        )
        assert "sk-super-secret" not in snippet


class TestParseEnvFile:
    def test_parses_key_value_pairs(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text("FOO=bar\nBAZ=qux\n", encoding="utf-8")
        assert parse_env_file(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_comments_and_blank_lines(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text("# a comment\n\nFOO=bar\n\n# another\n", encoding="utf-8")
        assert parse_env_file(f) == {"FOO": "bar"}

    def test_strips_quotes(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text('FOO="bar"\nBAZ=\'qux\'\n', encoding="utf-8")
        assert parse_env_file(f) == {"FOO": "bar", "BAZ": "qux"}

    def test_skips_lines_without_equals(self, tmp_path: Path):
        f = tmp_path / ".env"
        f.write_text("not a valid line\nFOO=bar\n", encoding="utf-8")
        assert parse_env_file(f) == {"FOO": "bar"}


class TestLoadEnvFile:
    def test_sets_missing_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("ELFMEM_TEST_VAR", raising=False)
        f = tmp_path / ".env"
        f.write_text("ELFMEM_TEST_VAR=from_file\n", encoding="utf-8")
        load_env_file(f)
        assert os.environ["ELFMEM_TEST_VAR"] == "from_file"

    def test_never_overrides_existing_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ELFMEM_TEST_VAR", "from_shell")
        f = tmp_path / ".env"
        f.write_text("ELFMEM_TEST_VAR=from_file\n", encoding="utf-8")
        load_env_file(f)
        assert os.environ["ELFMEM_TEST_VAR"] == "from_shell"
