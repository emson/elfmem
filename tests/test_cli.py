"""Tests for cli.py — CLI commands via CliRunner."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

from elfmem.api import MemorySystem
from elfmem.cli import app
from elfmem.types import (
    CurateResult,
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
    mem.curate.return_value = CurateResult(archived=0, edges_pruned=0, reinforced=0)
    mem.outcome.return_value = OutcomeResult(
        blocks_updated=1,
        mean_confidence_delta=0.0,
        edges_reinforced=0,
        blocks_penalized=0,
    )
    mem.dream.return_value = None
    mem.should_dream = False

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

    def test_missing_db_exits_nonzero(self) -> None:
        result = runner.invoke(app, ["remember", "fact"])  # no --db
        assert result.exit_code != 0
        assert "ELFMEM_DB" in result.output

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

    def test_json_has_archived_key(self, mock_managed: AsyncMock) -> None:
        result = runner.invoke(app, ["curate", "--db", "test.db", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "archived" in data


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
