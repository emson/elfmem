"""Tests for smart.py — SmartMemory and format helpers."""
from __future__ import annotations

import pytest

from elfmem.api import MemorySystem
from elfmem.smart import SmartMemory, _format_block, format_recall_response
from elfmem.types import (
    FrameResult,
    LearnResult,
    ScoredBlock,
    SystemStatus,
)


# ── Test helpers ──────────────────────────────────────────────────────────────


def _make_scored_block(
    id: str = "test-id",
    content: str = "test content",
    score: float = 0.5,
    tags: list[str] | None = None,
) -> ScoredBlock:
    return ScoredBlock(
        id=id,
        content=content,
        tags=tags or [],
        similarity=0.5,
        confidence=0.5,
        recency=0.5,
        centrality=0.5,
        reinforcement=0.5,
        score=score,
    )


def _make_frame_result(
    text: str = "",
    block_id: str = "test-id",
    score: float = 0.5,
    frame_name: str = "attention",
    blocks: list[ScoredBlock] | None = None,
) -> FrameResult:
    if blocks is None:
        blocks = [_make_scored_block(id=block_id, score=score)]
    return FrameResult(text=text, blocks=blocks, frame_name=frame_name)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def smart_memory(test_engine, mock_llm, mock_embedding) -> SmartMemory:  # type: ignore[misc]
    """SmartMemory backed by in-memory test engine. threshold=3 for fast consolidation."""
    system = MemorySystem(
        engine=test_engine,
        llm_service=mock_llm,
        embedding_service=mock_embedding,
    )
    mem = SmartMemory(system, threshold=3, pending=0)
    yield mem  # type: ignore[misc]
    await mem.close()


# ── Lifecycle tests ───────────────────────────────────────────────────────────


class TestSmartMemoryLifecycle:
    async def test_managed_yields_smart_memory(self, tmp_path: object) -> None:
        # managed() context manager yields a SmartMemory instance
        async with SmartMemory.managed(str(tmp_path / "test.db")) as mem:  # type: ignore[operator]
            assert isinstance(mem, SmartMemory)

    async def test_close_safe_without_session(self, smart_memory: SmartMemory) -> None:
        # close() on a never-used instance must not raise
        await smart_memory.close()

    async def test_close_safe_to_call_twice(self, smart_memory: SmartMemory) -> None:
        # close() is idempotent — end_session() returns 0.0 when no session active
        await smart_memory.close()
        await smart_memory.close()


# ── Remember tests ────────────────────────────────────────────────────────────


class TestRemember:
    async def test_remember_returns_created_status(self, smart_memory: SmartMemory) -> None:
        result = await smart_memory.remember("User prefers dark mode")
        assert result.status == "created"

    async def test_remember_duplicate_returns_rejected_status(
        self, smart_memory: SmartMemory
    ) -> None:
        await smart_memory.remember("same content")
        result = await smart_memory.remember("same content")
        assert result.status == "duplicate_rejected"

    async def test_remember_consolidates_when_threshold_reached(
        self, smart_memory: SmartMemory
    ) -> None:
        # threshold is 3 — learn 3 unique blocks to trigger consolidation
        for i in range(3):
            await smart_memory.remember(f"unique fact number {i}")
        result = await smart_memory.status()
        assert result.inbox_count == 0

    async def test_remember_does_not_consolidate_below_threshold(
        self, smart_memory: SmartMemory
    ) -> None:
        # Learn 2 blocks (threshold=3), inbox should still have 2
        for i in range(2):
            await smart_memory.remember(f"fact below threshold {i}")
        result = await smart_memory.status()
        assert result.inbox_count == 2


# ── Recall tests ──────────────────────────────────────────────────────────────


class TestRecall:
    async def test_recall_returns_frame_result(self, smart_memory: SmartMemory) -> None:
        result = await smart_memory.recall("preferences")
        assert isinstance(result, FrameResult)

    async def test_recall_has_text_attribute(self, smart_memory: SmartMemory) -> None:
        result = await smart_memory.recall("anything")
        assert isinstance(result.text, str)

    async def test_recall_has_blocks_attribute(self, smart_memory: SmartMemory) -> None:
        result = await smart_memory.recall("anything")
        assert isinstance(result.blocks, list)

    async def test_recall_empty_query_does_not_raise(
        self, smart_memory: SmartMemory
    ) -> None:
        # Empty string treated as queryless recall — must not raise
        result = await smart_memory.recall("")
        assert result is not None


# ── Delegation tests ──────────────────────────────────────────────────────────


class TestDelegation:
    async def test_status_returns_system_status(self, smart_memory: SmartMemory) -> None:
        result = await smart_memory.status()
        assert isinstance(result, SystemStatus)

    def test_guide_returns_non_empty_string(self, smart_memory: SmartMemory) -> None:
        result = smart_memory.guide()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_guide_method_returns_method_documentation(
        self, smart_memory: SmartMemory
    ) -> None:
        result = smart_memory.guide("learn")
        assert "learn" in result


# ── format_recall_response tests ─────────────────────────────────────────────


class TestFormatRecallResponse:
    """Tests for the format_recall_response() pure function."""

    def test_includes_text(self) -> None:
        result = _make_frame_result(text="## Context\nsome content")
        assert format_recall_response(result)["text"] == "## Context\nsome content"

    def test_includes_block_ids(self) -> None:
        result = _make_frame_result(block_id="abc123")
        blocks = format_recall_response(result)["blocks"]
        assert blocks[0]["id"] == "abc123"

    def test_scores_rounded_to_3dp(self) -> None:
        result = _make_frame_result(score=0.123456789)
        blocks = format_recall_response(result)["blocks"]
        assert blocks[0]["score"] == 0.123

    def test_empty_blocks_returns_empty_list(self) -> None:
        result = _make_frame_result(blocks=[])
        assert format_recall_response(result)["blocks"] == []

    def test_includes_frame_name(self) -> None:
        result = _make_frame_result(frame_name="attention")
        assert format_recall_response(result)["frame_name"] == "attention"

    def test_includes_cached_flag(self) -> None:
        fr = FrameResult(text="", blocks=[], frame_name="attention", cached=True)
        assert format_recall_response(fr)["cached"] is True


# ── _format_block tests ───────────────────────────────────────────────────────


class TestFormatBlock:
    def test_includes_id(self) -> None:
        block = _make_scored_block(id="xyz789")
        assert _format_block(block)["id"] == "xyz789"

    def test_includes_content(self) -> None:
        block = _make_scored_block(content="some content")
        assert _format_block(block)["content"] == "some content"

    def test_score_is_rounded(self) -> None:
        block = _make_scored_block(score=0.99999)
        assert _format_block(block)["score"] == 1.0

    def test_includes_tags(self) -> None:
        block = _make_scored_block(tags=["a", "b"])
        assert _format_block(block)["tags"] == ["a", "b"]
