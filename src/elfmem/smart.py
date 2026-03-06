"""SmartMemory — auto-managed MemorySystem for MCP and CLI interfaces.

Internal to elfmem. Not part of the public API.
Session management and inbox consolidation are handled automatically.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig
from elfmem.types import (
    CurateResult,
    FrameResult,
    LearnResult,
    OutcomeResult,
    ScoredBlock,
    SystemStatus,
)


class SmartMemory:
    """MemorySystem with lazy session start and auto-consolidation.

    For tool interfaces only. Not for library users.
    """

    def __init__(
        self,
        system: MemorySystem,
        threshold: int,
        pending: int = 0,
    ) -> None:
        self._system = system
        self._threshold = threshold
        self._pending = pending

    @classmethod
    async def open(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
    ) -> SmartMemory:
        """Open a database and seed inbox count from current state."""
        system = await MemorySystem.from_config(db_path, config)
        status = await system.status()
        return cls(system, status.inbox_threshold, status.inbox_count)

    @classmethod
    @asynccontextmanager
    async def managed(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
    ) -> AsyncIterator[SmartMemory]:
        """Open → yield → close. For short-lived CLI invocations."""
        mem = await cls.open(db_path, config=config)
        try:
            yield mem
        finally:
            await mem.close()

    async def close(self) -> None:
        """End any active session and dispose the DB engine."""
        await self._system.end_session()
        await self._system.close()

    async def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        category: str = "knowledge",
    ) -> LearnResult:
        """learn() + auto-session + auto-consolidate when inbox fills."""
        await self._system.begin_session()
        result = await self._system.learn(content, tags=tags, category=category)
        if result.status == "created":
            self._pending += 1
        if self._pending >= self._threshold:
            await self._system.consolidate()
            self._pending = 0
        return result

    async def recall(
        self,
        query: str,
        top_k: int = 5,
        frame: str = "attention",
    ) -> FrameResult:
        """frame() + auto-session. text field is ready for prompt injection."""
        await self._system.begin_session()
        return await self._system.frame(frame, query=query or None, top_k=top_k)

    async def status(self) -> SystemStatus:
        return await self._system.status()

    async def outcome(
        self,
        block_ids: list[str],
        signal: float,
        weight: float = 1.0,
        source: str = "",
    ) -> OutcomeResult:
        return await self._system.outcome(
            block_ids, signal, weight=weight, source=source
        )

    async def curate(self) -> CurateResult:
        return await self._system.curate()

    def guide(self, method: str | None = None) -> str:
        return self._system.guide(method)


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_recall_response(result: FrameResult) -> dict[str, Any]:
    """Format FrameResult for agent tool responses.

    FrameResult.to_dict() is compact and omits per-block detail intentionally.
    Agents need block IDs to call outcome() — this function includes them.
    Used by both MCP and CLI --json output.
    """
    return {
        "text": result.text,
        "frame_name": result.frame_name,
        "cached": result.cached,
        "blocks": [_format_block(b) for b in result.blocks],
    }


def _format_block(block: ScoredBlock) -> dict[str, Any]:
    """Extract agent-relevant fields from a ScoredBlock."""
    return {
        "id": block.id,
        "content": block.content,
        "score": round(block.score, 3),
        "tags": block.tags,
    }
