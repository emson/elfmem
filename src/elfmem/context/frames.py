"""Frame registry — built-in frame definitions and frame cache."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from elfmem.scoring import (
    ATTENTION_WEIGHTS,
    SELF_WEIGHTS,
    TASK_WEIGHTS,
    ScoringWeights,
)
from elfmem.types import FrameResult


@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int = 3600
    invalidate_on: list[str] = field(
        default_factory=lambda: ["self_block_change"]
    )


@dataclass(frozen=True)
class FrameFilters:
    tag_patterns: list[str] | None = None
    categories: list[str] | None = None
    search_window_hours: float = 200.0


@dataclass(frozen=True)
class FrameDefinition:
    name: str
    weights: ScoringWeights
    filters: FrameFilters
    guarantees: list[str]
    template: str
    token_budget: int
    cache: CachePolicy | None
    source: Literal["builtin", "user"] = "user"


SELF_FRAME = FrameDefinition(
    name="self",
    weights=SELF_WEIGHTS,
    filters=FrameFilters(tag_patterns=["self/%"]),
    guarantees=["self/constitutional"],
    template="self",
    token_budget=600,
    cache=CachePolicy(
        ttl_seconds=3600,
        invalidate_on=["self_block_change", "curate_complete"],
    ),
    source="builtin",
)

ATTENTION_FRAME = FrameDefinition(
    name="attention",
    weights=ATTENTION_WEIGHTS,
    filters=FrameFilters(),
    guarantees=[],
    template="attention",
    token_budget=2000,
    cache=None,
    source="builtin",
)

TASK_FRAME = FrameDefinition(
    name="task",
    weights=TASK_WEIGHTS,
    filters=FrameFilters(),
    guarantees=["self/goal"],
    template="task",
    token_budget=800,
    cache=None,
    source="builtin",
)

BUILTIN_FRAMES: dict[str, FrameDefinition] = {
    "self": SELF_FRAME,
    "attention": ATTENTION_FRAME,
    "task": TASK_FRAME,
}


def get_frame_definition(name: str) -> FrameDefinition:
    """Get a frame definition by name. Phase 1: built-in frames only."""
    if name in BUILTIN_FRAMES:
        return BUILTIN_FRAMES[name]
    raise ValueError(f"Unknown frame: {name!r}. Available: {list(BUILTIN_FRAMES)}")


class FrameCache:
    """Simple TTL cache for frame results. Scoped per session."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, FrameResult]] = {}

    def get(self, frame_name: str) -> FrameResult | None:
        """Return cached result or None if expired/missing."""
        entry = self._cache.get(frame_name)
        if entry is None:
            return None
        expires_at, result = entry
        if time.monotonic() >= expires_at:
            del self._cache[frame_name]
            return None
        return result

    def set(self, frame_name: str, result: FrameResult, ttl_seconds: int) -> None:
        """Cache a result with TTL."""
        expires_at = time.monotonic() + ttl_seconds
        self._cache[frame_name] = (expires_at, result)

    def invalidate(self, frame_name: str) -> None:
        """Invalidate a specific frame's cache entry."""
        self._cache.pop(frame_name, None)

    def clear(self) -> None:
        """Clear all cached frames."""
        self._cache.clear()
