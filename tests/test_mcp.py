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
    ConsolidateResult,
    CurateResult,
    FrameResult,
    LearnResult,
    MindOutcomeResult,
    MindPredictResult,
    MindShowResult,
    MindSummary,
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

    # ── dream v0.13.3 flag parity (closes #50 item 2) ────────────────────────

    async def test_dream_threads_rescore_flag(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_dream

        mock_mem.dream.return_value = ConsolidateResult(
            processed=0, promoted=0, deduplicated=0, edges_created=0,
            rescored=5, rescore_failed=0,
        )
        await _tool_dream(rescore=True, rescore_max=5)
        mock_mem.dream.assert_called_once_with(
            skip_llm=False, skip_contradictions=False,
            rescore=True, rescore_max=5,
        )

    async def test_dream_threads_no_llm_flag(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_dream

        mock_mem.dream.return_value = ConsolidateResult(
            processed=3, promoted=3, deduplicated=0, edges_created=0,
        )
        await _tool_dream(no_llm=True)
        mock_mem.dream.assert_called_once_with(
            skip_llm=True, skip_contradictions=False,
            rescore=False, rescore_max=None,
        )

    async def test_dream_threads_skip_contradictions_flag(
        self, mock_mem: AsyncMock
    ) -> None:
        from elfmem.mcp import _tool_dream

        mock_mem.dream.return_value = ConsolidateResult(
            processed=2, promoted=2, deduplicated=0, edges_created=1,
        )
        await _tool_dream(skip_contradictions=True)
        mock_mem.dream.assert_called_once_with(
            skip_llm=False, skip_contradictions=True,
            rescore=False, rescore_max=None,
        )

    async def test_dream_default_flags_unchanged(self, mock_mem: AsyncMock) -> None:
        """Default invocation must match pre-feature behaviour."""
        from elfmem.mcp import _tool_dream

        mock_mem.dream.return_value = ConsolidateResult(
            processed=1, promoted=1, deduplicated=0, edges_created=0,
        )
        await _tool_dream()
        mock_mem.dream.assert_called_once_with(
            skip_llm=False, skip_contradictions=False,
            rescore=False, rescore_max=None,
        )

    # ── Theory of Mind MCP tools (closes #50 item 3) ────────────────────────

    async def test_mind_create_returns_block_id(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_mind_create

        mock_mem.mind_create.return_value = LearnResult(
            block_id="mind-abc", status="created"
        )
        result = await _tool_mind_create(subject="Alice", goals=["ship"])
        assert result["block_id"] == "mind-abc"
        mock_mem.mind_create.assert_called_once_with(
            "Alice", goals=["ship"], beliefs=None, fears=None, motivations=None
        )

    async def test_mind_predict_returns_decision_id(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_mind_predict

        mock_mem.mind_predict.return_value = MindPredictResult(
            mind_block_id="m1",
            decision_block_id="d1",
            prediction="X",
            verify_at="2026-06-01",
            edge_action="created",
        )
        result = await _tool_mind_predict(
            mind_block_id="m1", prediction="X", verify_at="2026-06-01"
        )
        assert result["decision_block_id"] == "d1"
        assert result["edge_action"] == "created"

    async def test_mind_list_returns_list_of_dicts(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_mind_list

        mock_mem.mind_list.return_value = [
            MindSummary(
                block_id="m1", subject="Alice", confidence=0.7,
                prediction_count=3, hit_count=2, miss_count=1,
            )
        ]
        result = await _tool_mind_list()
        assert isinstance(result, list)
        assert result[0]["subject"] == "Alice"
        assert result[0]["hit_count"] == 2

    async def test_mind_show_returns_full_view(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_mind_show

        mock_mem.mind_show.return_value = MindShowResult(
            block_id="m1", subject="Alice", content="...", confidence=0.7,
            predictions=[],
        )
        result = await _tool_mind_show(mind_block_id="m1")
        assert result["subject"] == "Alice"
        assert result["predictions"] == []

    async def test_mind_outcome_returns_deltas(self, mock_mem: AsyncMock) -> None:
        from elfmem.mcp import _tool_mind_outcome

        mock_mem.mind_outcome.return_value = MindOutcomeResult(
            mind_block_id="m1",
            decision_block_id="d1",
            hit=True,
            reason="observed",
            mind_confidence_delta=0.08,
            decision_confidence_delta=0.10,
            validates_edge_action="created",
        )
        result = await _tool_mind_outcome(
            decision_block_id="d1", hit=True, reason="observed"
        )
        assert result["hit"] is True
        assert result["mind_confidence_delta"] == 0.08


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
