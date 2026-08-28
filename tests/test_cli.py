"""Tests for cli.py — CLI commands via CliRunner."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from elfmem.api import MemorySystem
from elfmem.cli import _doctor_preflight, _load_project_env, app
from elfmem.types import (
    BlockSummary,
    CurateResult,
    EditResult,
    ForgetResult,
    FrameResult,
    LearnResult,
    OutcomeResult,
    SystemStatus,
    TokenUsage,
)

runner = CliRunner()


# ── Test helpers ──────────────────────────────────────────────────────────────


def _make_system_status(health: str = "good") -> SystemStatus:
    return SystemStatus(
        session_active=False,
        session_hours=None,
        inbox_count=0,
        inbox_threshold=10,
        active_count=5,
        archived_count=2,
        total_active_hours=1.0,
        last_consolidated="2024-01-01T00:00:00",
        health=health,
        suggestion="Memory healthy. No action required.",
        session_tokens=TokenUsage(),
        lifetime_tokens=TokenUsage(),
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_managed(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Mock MemorySystem.managed() to yield a pre-configured mock."""
    mem: AsyncMock = AsyncMock(spec=MemorySystem)
    mem.remember.return_value = LearnResult(block_id="abc12345", status="created")
    mem.frame.return_value = FrameResult(
        text="recalled context", blocks=[], frame_name="attention"
    )
    mem.status.return_value = _make_system_status(health="good")
    mem.curate.return_value = CurateResult(edges_pruned=0, reinforced=0)
    mem.outcome.return_value = OutcomeResult(
        blocks_updated=1,
        mean_confidence_delta=0.0,
        edges_reinforced=0,
        blocks_penalized=0,
    )
    mem.dream.return_value = None
    mem.should_dream = False
    mem.edit.return_value = EditResult(block_id="abc12345")
    mem.forget.return_value = ForgetResult(block_id="abc12345", status="forgotten")
    mem.ls.return_value = [
        BlockSummary(
            id="abc12345",
            content="a listed block",
            category="knowledge",
            tags=["python"],
            created_at="2024-01-01T00:00:00",
            reinforcement_count=1,
        )
    ]

    @asynccontextmanager
    async def _managed(*args: object, **kwargs: object) -> object:
        yield mem

    monkeypatch.setattr(MemorySystem, "managed", _managed)
    return mem


# ── remember command ──────────────────────────────────────────────────────────


class TestRememberCommand:
    def test_text_output_shows_stored(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["remember", "test fact", "--db", "test.db"])
        assert result.exit_code == 0
        assert "Stored" in result.output

    def test_json_output_has_block_id(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["remember", "fact", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "block_id" in data

    def test_no_db_flag_uses_discovery_chain(
        self, mock_managed: AsyncMock, monkeypatch
    ) -> None:
        # Without --db, the command uses the discovery chain. Under pytest the
        # global ~/.elfmem/agent.db fallback is guarded — this test asserts the
        # chain still works end-to-end when the caller opts in (the safe path
        # for CI environments where no real config exists).
        monkeypatch.setenv("ELFMEM_ALLOW_GLOBAL_FALLBACK", "1")
        result = runner.invoke(app, ["remember", "fact"])
        assert result.exit_code == 0

    def test_tags_are_passed_through(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app, ["remember", "fact", "--db", "test.db", "--tags", "a,b"]
        )
        assert result.exit_code == 0
        _, kwargs = mock_managed.remember.call_args
        assert kwargs.get("tags") == ["a", "b"]


# ── recall command ────────────────────────────────────────────────────────────


class TestRecallCommand:
    def test_text_output_is_rendered_content(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["recall", "query", "--db", "test.db"])
        assert result.exit_code == 0
        # result.text from FrameResult, not the frame summary
        assert "recalled context" in result.output

    def test_json_output_has_blocks_key(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["recall", "query", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "blocks" in data

    def test_json_output_has_text_key(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["recall", "query", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "text" in data


# ── edit / forget / ls commands ────────────────────────────────────────────────


class TestEditCommand:
    def test_text_output_shows_edited(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app, ["edit", "abc12345", "new content", "--db", "test.db"]
        )
        assert result.exit_code == 0
        assert "Edited" in result.output

    def test_content_passed_through(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app, ["edit", "abc12345", "new content", "--db", "test.db"]
        )
        assert result.exit_code == 0
        args, _ = mock_managed.edit.call_args
        assert args == ("abc12345", "new content")

    def test_json_output_has_block_id(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app, ["edit", "abc12345", "new content", "--db", "test.db", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["block_id"] == "abc12345"


class TestForgetCommand:
    def test_text_output_shows_forgot(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["forget", "abc12345", "--db", "test.db"])
        assert result.exit_code == 0
        assert "Forgot" in result.output

    def test_json_output_has_status(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app, ["forget", "abc12345", "--db", "test.db", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "forgotten"


class TestLsCommand:
    def test_text_output_lists_blocks(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["ls", "--db", "test.db"])
        assert result.exit_code == 0
        assert "a listed block" in result.output

    def test_json_output_is_a_list(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["ls", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["id"] == "abc12345"

    def test_tag_filter_passed_through(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app, ["ls", "--db", "test.db", "--tag", "self/%"]
        )
        assert result.exit_code == 0
        args, _ = mock_managed.ls.call_args
        assert args[0] == "self/%"

    def test_empty_result_shows_message(self, mock_managed: AsyncMock) -> None:
        mock_managed.ls.return_value = []
        result = runner.invoke(app, ["ls", "--db", "test.db"])
        assert result.exit_code == 0
        assert "No active blocks" in result.output


# ── status command ────────────────────────────────────────────────────────────


class TestStatusCommand:
    def test_exits_zero(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["status", "--db", "test.db"])
        assert result.exit_code == 0

    def test_json_has_health_key(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["status", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "health" in data

    def test_text_output_contains_health(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["status", "--db", "test.db"])
        assert result.exit_code == 0
        assert "good" in result.output.lower() or "Health" in result.output


# ── curate command ────────────────────────────────────────────────────────────


class TestCurateCommand:
    def test_exits_zero(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["curate", "--db", "test.db"])
        assert result.exit_code == 0

    def test_json_has_edges_pruned_key(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["curate", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "edges_pruned" in data


# ── dream --metabolism-dry-run (edge-metabolism Stage A) ────────────────────


class TestDreamMetabolismDryRun:
    def test_calls_metabolism_dry_run_not_dream(self, mock_managed: AsyncMock) -> None:
        from elfmem.types import MetabolismDryRunResult

        mock_managed.metabolism_dry_run.return_value = MetabolismDryRunResult(
            blocks_considered=2, self_goals=["a goal"],
        )
        result = runner.invoke(
            app, ["dream", "--db", "test.db", "--metabolism-dry-run"]
        )
        assert result.exit_code == 0
        mock_managed.metabolism_dry_run.assert_awaited_once()
        mock_managed.dream.assert_not_awaited()

    def test_json_output_has_proposals_key(self, mock_managed: AsyncMock) -> None:
        from elfmem.types import GoalDirectedEdgeProposal, MetabolismDryRunResult

        mock_managed.metabolism_dry_run.return_value = MetabolismDryRunResult(
            blocks_considered=1, self_goals=["a goal"],
            proposals=[GoalDirectedEdgeProposal("b1", "c1", "reason")],
        )
        result = runner.invoke(
            app, ["dream", "--db", "test.db", "--metabolism-dry-run", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "proposals" in data
        assert data["proposals"][0]["candidate_id"] == "c1"

    def test_text_output_lists_each_proposal(self, mock_managed: AsyncMock) -> None:
        from elfmem.types import GoalDirectedEdgeProposal, MetabolismDryRunResult

        mock_managed.metabolism_dry_run.return_value = MetabolismDryRunResult(
            blocks_considered=1, self_goals=["a goal"],
            proposals=[GoalDirectedEdgeProposal("b1", "c1", "serves goal X")],
        )
        result = runner.invoke(
            app, ["dream", "--db", "test.db", "--metabolism-dry-run"]
        )
        assert result.exit_code == 0
        assert "b1 -> c1: serves goal X" in result.output


# ── inbox command ────────────────────────────────────────────────────────────


class TestInboxCommand:
    def test_empty_inbox_message(self, mock_managed: AsyncMock) -> None:
        mock_managed.inbox.return_value = []
        result = runner.invoke(app, ["inbox", "--db", "test.db"])
        assert result.exit_code == 0
        assert "Inbox empty." in result.output

    def test_json_output_lists_pending_blocks(self, mock_managed: AsyncMock) -> None:
        from elfmem.types import InboxBlockSummary

        mock_managed.inbox.return_value = [
            InboxBlockSummary(
                id="b1", content="pending fact", category="knowledge",
                tags=["self/goal"], created_at="2026-01-01T00:00:00",
            ),
        ]
        result = runner.invoke(app, ["inbox", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data[0]["id"] == "b1"
        assert data[0]["tags"] == ["self/goal"]

    def test_max_flag_threads_through(self, mock_managed: AsyncMock) -> None:
        mock_managed.inbox.return_value = []
        runner.invoke(app, ["inbox", "--db", "test.db", "--max", "3"])
        mock_managed.inbox.assert_awaited_once_with(3)


# ── dream --host-analyses ────────────────────────────────────────────────────


class TestDreamHostAnalyses:
    def test_reads_and_threads_host_analyses_file(
        self, mock_managed: AsyncMock, tmp_path,
    ) -> None:
        analyses_file = tmp_path / "analyses.json"
        analyses_file.write_text(json.dumps({
            "b1": {"alignment_score": 0.8, "tags": ["self/goal"], "summary": "s"},
        }))
        mock_managed.dream.return_value = None
        result = runner.invoke(
            app,
            ["dream", "--db", "test.db", "--host-analyses", str(analyses_file)],
        )
        assert result.exit_code == 0, result.output
        _, kwargs = mock_managed.dream.call_args
        assert kwargs["host_analyses"] == {
            "b1": {"alignment_score": 0.8, "tags": ["self/goal"], "summary": "s"},
        }

    def test_malformed_json_file_gives_clean_error(
        self, mock_managed: AsyncMock, tmp_path,
    ) -> None:
        analyses_file = tmp_path / "bad.json"
        analyses_file.write_text("not valid json{{{")
        result = runner.invoke(
            app,
            ["dream", "--db", "test.db", "--host-analyses", str(analyses_file)],
        )
        assert result.exit_code == 1
        assert "Error reading --host-analyses" in result.output

    def test_missing_file_gives_clean_error(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(
            app,
            ["dream", "--db", "test.db", "--host-analyses", "/no/such/file.json"],
        )
        assert result.exit_code == 1
        assert "Error reading --host-analyses" in result.output

    def test_combined_with_metabolism_dry_run_errors(
        self, mock_managed: AsyncMock, tmp_path,
    ) -> None:
        analyses_file = tmp_path / "analyses.json"
        analyses_file.write_text("{}")
        result = runner.invoke(
            app,
            [
                "dream", "--db", "test.db",
                "--metabolism-dry-run", "--host-analyses", str(analyses_file),
            ],
        )
        assert result.exit_code == 1
        assert "read-only pass" in result.output


# ── guide command ─────────────────────────────────────────────────────────────


class TestGuideCommand:
    def test_no_db_required(self) -> None:
        # guide works without --db
        result = runner.invoke(app, ["guide"])
        assert result.exit_code == 0
        assert len(result.output) > 0

    def test_method_shows_documentation(self) -> None:
        result = runner.invoke(app, ["guide", "learn"])
        assert result.exit_code == 0
        assert "learn" in result.output.lower()

    def test_overview_lists_operations(self) -> None:
        result = runner.invoke(app, ["guide"])
        assert result.exit_code == 0
        assert "recall" in result.output


# ── Error handling ────────────────────────────────────────────────────────────


class TestErrorHandling:
    def test_elfmem_error_shows_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from elfmem.exceptions import ElfmemError

        @asynccontextmanager
        async def _bad_managed(*args: object, **kwargs: object) -> object:
            mem: AsyncMock = AsyncMock(spec=MemorySystem)
            mem.remember.side_effect = ElfmemError("bad frame", recovery="try again")
            yield mem

        monkeypatch.setattr(MemorySystem, "managed", _bad_managed)
        result = runner.invoke(app, ["remember", "x", "--db", "test.db"])
        assert result.exit_code != 0
        assert "Recovery:" in result.output


# ── Help ──────────────────────────────────────────────────────────────────────


class TestHelp:
    def test_help_lists_all_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd in ("remember", "recall", "status", "outcome", "curate", "guide", "serve"):
            assert cmd in result.output


# ── Seed templates ─────────────────────────────────────────────────────────────


class TestTemplatesCommand:
    def test_templates_lists_available(self) -> None:
        result = runner.invoke(app, ["templates"])
        assert result.exit_code == 0
        assert "coding" in result.output
        assert "research" in result.output
        assert "assistant" in result.output

    def test_templates_json(self) -> None:
        result = runner.invoke(app, ["templates", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        names = [t["name"] for t in data["templates"]]
        assert "coding" in names
        assert "research" in names
        assert "assistant" in names

    def test_templates_json_has_description(self) -> None:
        result = runner.invoke(app, ["templates", "--json"])
        data = json.loads(result.output)
        for t in data["templates"]:
            assert "description" in t
            assert len(t["description"]) > 0


# ── .env auto-loading (v2 step 3) ───────────────────────────────────────────


class TestLoadProjectEnv:
    def test_loads_env_when_project_found(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELFMEM_TEST_VAR", raising=False)
        (tmp_path / ".git").mkdir()
        (tmp_path / ".env").write_text("ELFMEM_TEST_VAR=from_dotenv\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        found = _load_project_env()
        assert found == tmp_path / ".env"
        assert os.environ["ELFMEM_TEST_VAR"] == "from_dotenv"

    def test_real_env_var_wins_over_dotenv(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ELFMEM_TEST_VAR", "from_shell")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".env").write_text("ELFMEM_TEST_VAR=from_dotenv\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        _load_project_env()
        assert os.environ["ELFMEM_TEST_VAR"] == "from_shell"

    def test_returns_none_when_no_env_file(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        assert _load_project_env() is None


# ── doctor --resolve preflight (v2 step 3) ──────────────────────────────────


class _FakeLLM:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def process_block(self, block: str, self_context: str):
        if self._fail:
            raise ConnectionError("could not reach base_url")
        from elfmem.types import BlockAnalysis
        return BlockAnalysis(alignment_score=0.5, tags=[], summary=None)


class TestDoctorPreflight:
    async def test_reports_ok_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "elfmem.adapters.factory.make_llm_adapter",
            lambda cfg, counter: _FakeLLM(),
        )
        ok, detail = await _doctor_preflight(None)
        assert ok is True
        assert "OK" in detail

    async def test_reports_failure_without_raising(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "elfmem.adapters.factory.make_llm_adapter",
            lambda cfg, counter: _FakeLLM(fail=True),
        )
        ok, detail = await _doctor_preflight(None)
        assert ok is False
        assert "ConnectionError" in detail


class TestDoctorResolveFlag:
    def test_resolve_flag_triggers_preflight_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        async def fake_preflight(config):
            return True, "mock-model — OK (5ms)"

        monkeypatch.setattr("elfmem.cli._doctor_preflight", fake_preflight)
        result = runner.invoke(
            app, ["doctor", "--db", str(tmp_path / "x.db"), "--resolve"]
        )
        assert "LLM preflight" in result.output
        assert "mock-model" in result.output

    def test_without_resolve_flag_no_preflight_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        async def fake_preflight(config):
            raise AssertionError("preflight should not run without --resolve")

        monkeypatch.setattr("elfmem.cli._doctor_preflight", fake_preflight)
        result = runner.invoke(app, ["doctor", "--db", str(tmp_path / "x.db")])
        assert "LLM preflight" not in result.output


# ── init command — seed defaults (v2 step 4) ────────────────────────────────


class TestInitSeedDefault:
    """A fresh install writes zero memory blocks unless --seed is passed.

    Every invocation passes --db explicitly into tmp_path: init's default DB
    path is ~/.elfmem/databases/<project-name>.db (global, keyed only by
    project name — see project.default_db_path), not scoped under tmp_path.
    pytest reuses a rotating set of tmp_path basenames across runs, so
    without an explicit --db a "fresh install" test can silently observe an
    already-seeded DB left by an earlier run and report created=0.
    """

    def _fresh_project(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> str:
        (tmp_path / ".git").mkdir()
        monkeypatch.chdir(tmp_path)
        return str(tmp_path / "test.db")

    def test_default_creates_no_constitutional_blocks(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = self._fresh_project(tmp_path, monkeypatch)
        result = runner.invoke(app, ["init", "--no-docs", "--db", db_path, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "constitutional_blocks" not in data or data["constitutional_blocks"].get(
            "skipped"
        )

    def test_default_text_output_shows_skipped_hint(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = self._fresh_project(tmp_path, monkeypatch)
        result = runner.invoke(app, ["init", "--no-docs", "--db", db_path])
        assert result.exit_code == 0
        assert "Skipped" in result.output
        assert "--seed" in result.output

    def test_explicit_seed_flag_creates_constitutional_blocks(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = self._fresh_project(tmp_path, monkeypatch)
        result = runner.invoke(
            app, ["init", "--no-docs", "--db", db_path, "--seed", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["constitutional_blocks"]["created"] == 10

    def test_no_seed_flag_still_works_explicitly(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        db_path = self._fresh_project(tmp_path, monkeypatch)
        result = runner.invoke(
            app, ["init", "--no-docs", "--db", db_path, "--no-seed", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data.get("constitutional_blocks", {}).get("skipped") is True
