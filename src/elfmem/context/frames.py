"""Frame registry — built-in frame definitions and frame cache."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

from elfmem.scoring import (
    ATTENTION_WEIGHTS,
    SELF_WEIGHTS,
    SIMULATE_WEIGHTS,
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
    score_boosts: dict[str, float] | None = None
    # Tag patterns that disqualify a block from a *guaranteed* slot. It can
    # still be retrieved on merit; it just cannot pre-empt the blocks the
    # frame exists to protect. SELF needs this because `self/constitutional`
    # is assigned by the consolidating LLM and has spread to 39 blocks in a
    # mature instance, nine of them inbound peer letters -- which then won
    # identity slots from elf's own principles.
    guarantee_excludes: list[str] = field(default_factory=list)
    # A queryless frame ignores any query handed to it. Identity is not a
    # search result: the SELF frame answers "who am I", not "what do I know
    # about X" (that is ATTENTION). Declaring it here rather than relying on
    # callers to pass query=None is what makes the frame cache correct --
    # a result that cannot depend on the query is safe to cache per frame.
    queryless: bool = False


SELF_FRAME = FrameDefinition(
    name="self",
    weights=SELF_WEIGHTS,
    filters=FrameFilters(tag_patterns=["self/%"]),
    guarantees=["self/constitutional"],
    # `self/role/%` is the authored vocabulary `init --seed` lays down, one
    # role per principle, and would be a tighter guarantee than
    # `self/constitutional` (which the consolidating LLM also assigns, and
    # which has spread to 40 blocks in a mature instance).
    #
    # An earlier version of this comment claimed role tags "do not survive:
    # consolidation rewrites a seeded block and re-tags it from the LLM's own
    # vocabulary." That cause is wrong, and was load-bearing enough to be
    # cited downstream as grounds for adding a whole new column, so: measured
    # directly, consolidation UNIONS tags (`{*declared, *inferred}` ->
    # `add_tags`) and a declared `self/role/x` is still present afterwards.
    # Caller-declared tags are therefore a stable, LLM-proof key -- use one
    # when you need to find "the block holding principle 7" again.
    #
    # What IS true is the observation: elf's own instance has only 2 of 40
    # constitutional blocks carrying a role tag, and only 4 distinct role
    # tags exist at all. That erosion happened somewhere other than
    # consolidation (blocks predating the current seed vocabulary is the
    # likeliest), so the guarantee stays on `self/constitutional` until the
    # real cause is found -- switching it now would silently drop 38 blocks
    # out of the guarantee on live instances.
    #
    # Excluding correspondence is the discriminator that *is* structural --
    # `peer/*` tags are applied by the peer channel, never inferred.
    guarantee_excludes=["peer/%"],
    template="self",
    token_budget=600,
    cache=CachePolicy(
        ttl_seconds=3600,
        invalidate_on=["self_block_change", "curate_complete"],
    ),
    source="builtin",
    queryless=True,
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

SIMULATE_FRAME = FrameDefinition(
    name="simulate",
    weights=SIMULATE_WEIGHTS,
    filters=FrameFilters(),
    guarantees=["self/constitutional", "mind/%"],
    template="simulate",
    token_budget=2000,
    cache=None,
    source="builtin",
    score_boosts={
        "tag:self/": 10.0,
        "mind": 6.0,
        "decision": 5.0,
    },
)

BUILTIN_FRAMES: dict[str, FrameDefinition] = {
    "self": SELF_FRAME,
    "attention": ATTENTION_FRAME,
    "task": TASK_FRAME,
    "simulate": SIMULATE_FRAME,
}


def get_frame_definition(name: str) -> FrameDefinition:
    """Get a frame definition by name. Phase 1: built-in frames only."""
    if name in BUILTIN_FRAMES:
        return BUILTIN_FRAMES[name]
    raise ValueError(f"Unknown frame: {name!r}. Available: {list(BUILTIN_FRAMES)}")


class FrameCache:
    """TTL cache for frame results, keyed on every input that shapes them.

    Only queryless frames are cacheable, so the key is (frame, top_k): a
    queryless result depends on nothing else a caller can vary. Keying on
    the frame name alone -- as this did until v0.17.1 -- meant a
    ``top_k=3`` call was served the ten-block result cached by an earlier
    ``top_k=10`` call, silently ignoring the argument.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], tuple[float, FrameResult]] = {}

    def get(self, frame_name: str, top_k: int) -> FrameResult | None:
        """Return cached result or None if expired/missing."""
        key = (frame_name, top_k)
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, result = entry
        if time.monotonic() >= expires_at:
            del self._cache[key]
            return None
        return result

    def set(
        self, frame_name: str, result: FrameResult, ttl_seconds: int, top_k: int
    ) -> None:
        """Cache a result with TTL."""
        expires_at = time.monotonic() + ttl_seconds
        self._cache[(frame_name, top_k)] = (expires_at, result)

    def invalidate(self, frame_name: str) -> None:
        """Invalidate every cached result for a frame, at any top_k."""
        for key in [k for k in self._cache if k[0] == frame_name]:
            del self._cache[key]

    def clear(self) -> None:
        """Clear all cached frames."""
        self._cache.clear()
