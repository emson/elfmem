"""SmartMemory — thin compatibility shim over MemorySystem.

MemorySystem now owns the full "three rhythms" API:
    HEARTBEAT (ms):   remember() — fast learn to inbox
    BREATHING (s):    dream() — deep consolidation
    SLEEP (min):      curate() — maintenance

SmartMemory delegates all operations to an inner MemorySystem.
Prefer MemorySystem.from_config() directly in new code. SmartMemory
remains for backwards compatibility and MCP tool wrappers.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from elfmem.api import MemorySystem
from elfmem.config import ElfmemConfig
from elfmem.policy import ConsolidationPolicy
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
    """Compatibility shim: delegates all operations to an inner MemorySystem.

    All state (pending counter, session lifecycle, policy) lives in the
    wrapped MemorySystem. This class adds no behaviour of its own.

    Prefer MemorySystem directly for new code::

        system = await MemorySystem.from_config("agent.db", policy=policy)
        result = await system.remember("...")
        if system.should_dream:
            await system.dream()
    """

    def __init__(self, system: MemorySystem) -> None:
        self._system = system

    @classmethod
    async def open(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
        policy: ConsolidationPolicy | None = None,
    ) -> SmartMemory:
        """Open a database and return a SmartMemory wrapping a MemorySystem."""
        system = await MemorySystem.from_config(db_path, config, policy=policy)
        return cls(system)

    @classmethod
    @asynccontextmanager
    async def managed(
        cls,
        db_path: str,
        config: ElfmemConfig | str | dict[str, Any] | None = None,
        policy: ConsolidationPolicy | None = None,
    ) -> AsyncIterator[SmartMemory]:
        """Open → yield → close. Safety net: dreams on exit if pending."""
        mem = await cls.open(db_path, config=config, policy=policy)
        try:
            yield mem
        finally:
            if mem.should_dream:
                await mem.dream()
            await mem.close()

    async def close(self) -> None:
        """End any active session and dispose the DB engine."""
        await self._system.end_session()
        await self._system.close()

    @property
    def should_dream(self) -> bool:
        """True when pending blocks have reached the consolidation threshold."""
        return self._system.should_dream

    async def remember(
        self,
        content: str,
        tags: list[str] | None = None,
        category: str = "knowledge",
    ) -> LearnResult:
        """Fast-path learn: auto-starts session, stores in inbox without blocking."""
        return await self._system.remember(content, tags=tags, category=category)

    async def dream(self) -> ConsolidateResult | None:
        """Deep consolidation at a natural pause point. Returns None if nothing pending."""
        return await self._system.dream()

    async def recall(
        self,
        query: str,
        top_k: int = 5,
        frame: str = "attention",
    ) -> FrameResult:
        """frame() + auto-session. result.text is ready for prompt injection."""
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
