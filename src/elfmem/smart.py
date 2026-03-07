"""SmartMemory — agent-friendly wrapper for MemorySystem with explicit dreaming.

Internal to elfmem. Not part of the public API.

Three rhythms:
    HEARTBEAT (ms):   remember() — fast learn to inbox
    BREATHING (s):    dream() — deep consolidation
    SLEEP (min):      curate() — maintenance
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig
from elfmem.types import (
    ConsolidateResult,
    CurateResult,
    FrameResult,
    LearnResult,
    OutcomeResult,
    ScoredBlock,
    SystemStatus,
)


class SmartMemory:
    """MemorySystem with lazy session start and explicit dreaming (consolidation).

    Learn fast (remember) → Dream deep (consolidate) → Curate (maintain).

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
        """Open → yield → close. Safety net: dreams on exit if pending.

        Ensures consolidation happens even if agent forgot to call dream().
        """
        mem = await cls.open(db_path, config=config)
        try:
            yield mem
        finally:
            # Safety net: consolidate any pending blocks before session closes.
            if mem.should_dream:
                await mem.dream()
            await mem.close()

    async def close(self) -> None:
        """End any active session and dispose the DB engine."""
        await self._system.end_session()
        await self._system.close()

    @property
    def should_dream(self) -> bool:
        """Check if consolidation is needed.

        True when inbox has accumulated to or beyond the threshold.
        Call dream() when True, or let the session context manager handle it.
        """
        return self._pending >= self._threshold

    async def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        category: str = "knowledge",
    ) -> LearnResult:
        """Fast-path learn: store in inbox without blocking on consolidation.

        Cost: Instant (zero LLM calls, pure DB insert).
        After creation, check should_dream to see if consolidation is needed.
        """
        await self._system.begin_session()
        result = await self._system.learn(content, tags=tags, category=category)
        if result.status == "created":
            self._pending += 1
        return result

    async def dream(self) -> ConsolidateResult | None:
        """Deep consolidation: embed, align, detect contradictions, build graph.

        Cost: LLM call per inbox block. Slow if many pending blocks.
        Safe to call when should_dream is True, or at natural pause points.
        Idempotent: safe to call multiple times (returns None if no pending).

        Returns: ConsolidateResult with counts (processed, promoted, etc.), or None
                if no blocks were pending.
        """
        if self._pending == 0:
            return None
        result = await self._system.consolidate()
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
