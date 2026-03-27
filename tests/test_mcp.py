"""Tests for mcp.py — MCP tool functions.

Tests call the private _tool_* coroutines directly, not the @mcp.tool()
FunctionTool wrappers. FastMCP 2.x replaces decorated functions with
FunctionTool Pydantic model objects (not callable). Business logic lives in
_tool_* so it stays testable without going through the MCP protocol layer.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from elfmem.api import MemorySystem
from elfmem.types import (
    CurateResult,
    FrameResult,
    LearnResult,
    OutcomeResult,
    ScoredBlock,
    SystemStatus,
    TokenUsage,
)

# ── Test helpers ───────────────────────────────────────────────────────────────


def _make_scored_block(id: str = "test-id") -> ScoredBlock:
    return ScoredBlock(
        id=id,
        content="test content",
        tags=[],
        similarity=0.5,
        confidence=0.5,
        recency=0.5,
        centrality=0.5,
        reinforcement=0.5,
        score=0.5,
    )


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


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_mem(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Inject a mock MemorySystem into the mcp module."""
    import elfmem.mcp as mcp_module

    mem: AsyncMock = AsyncMock(spec=MemorySystem)
    mem.guide = MagicMock(return_value="guide text")
    mem.should_dream = False  # synchronous property — set as plain attribute on mock
    monkeypatch.setattr(mcp_module, "_memory", mem)
    return mem


# ── Tool tests ─────────────────────────────────────────────────────────────────


class TestMcpTools:
    async def test_remember_returns_dict_with_block_id(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_remember

        mock_mem.remember.return_value = LearnResult(block_id="abc123", status="created")
        result = await _tool_remember(content="test fact")
        assert result["block_id"] == "abc123"

    async def test_remember_returns_dict_with_status(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_remember

        mock_mem.remember.return_value = LearnResult(block_id="abc123", status="created")
        result = await _tool_remember(content="test fact")
        assert result["status"] == "created"

    async def test_recall_returns_dict_with_text(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_recall

        mock_mem.frame.return_value = FrameResult(
            text="context text", blocks=[], frame_name="attention"
        )
        result = await _tool_recall(query="test")
        assert result["text"] == "context text"

    async def test_recall_includes_blocks_key(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_recall

        mock_mem.frame.return_value = FrameResult(
            text="text", blocks=[], frame_name="attention"
        )
        result = await _tool_recall(query="test")
        assert "blocks" in result

    async def test_recall_includes_block_ids(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_recall

        block = _make_scored_block(id="xyz789")
        mock_mem.frame.return_value = FrameResult(
            text="text", blocks=[block], frame_name="attention"
        )
        result = await _tool_recall(query="test")
        assert result["blocks"][0]["id"] == "xyz789"

    async def test_status_returns_dict_with_health(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_status

        mock_mem.status.return_value = _make_system_status(health="good")
        result = await _tool_status()
        assert result["health"] == "good"

    async def test_outcome_returns_dict_with_blocks_updated(
        self, mock_mem: AsyncMock
    ) -> None:
        from elfmem.mcp import _tool_outcome

        mock_mem.outcome.return_value = OutcomeResult(
            blocks_updated=2,
            mean_confidence_delta=0.05,
            edges_reinforced=1,
            blocks_penalized=0,
        )
        result = await _tool_outcome(block_ids=["a", "b"], signal=0.9)
        assert result["blocks_updated"] == 2

    async def test_curate_returns_dict(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_curate

        mock_mem.curate.return_value = CurateResult(
            archived=1, edges_pruned=2, reinforced=3
        )
        result = await _tool_curate()
        assert result["archived"] == 1

    async def test_guide_returns_string(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_guide

        result = await _tool_guide(method=None)
        assert isinstance(result, str)

    async def test_guide_calls_with_method_name(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_guide

        mock_mem.guide = MagicMock(return_value="learn docs")
        result = await _tool_guide(method="learn")
        assert result == "learn docs"
        mock_mem.guide.assert_called_once_with("learn")


# ── _mem() guard tests ─────────────────────────────────────────────────────────


class TestMemGuard:
    async def test_raises_runtime_error_when_not_initialised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import elfmem.mcp as mcp_module
        from elfmem.mcp import _tool_status

        monkeypatch.setattr(mcp_module, "_memory", None)
        with pytest.raises(RuntimeError, match="not initialised"):
            await _tool_status()
