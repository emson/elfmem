"""Core domain types for elfmem.

These are the shared vocabulary for every layer. Changing these types requires
updating every module that imports them — treat as stable contracts.

Agent-friendly surface
----------------------
Every result type implements three methods:
- ``__str__``   → one-line summary optimised for agent context windows
- ``summary``   → property; same as ``__str__`` (single source of truth)
- ``to_dict()`` → JSON-serialisable dict for programmatic access
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class BlockStatus(StrEnum):
    INBOX = "inbox"
    ACTIVE = "active"
    ARCHIVED = "archived"


class ArchiveReason(StrEnum):
    DECAYED = "decayed"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class DecayTier(StrEnum):
    PERMANENT = "permanent"  # λ = 0.00001
    DURABLE = "durable"  # λ = 0.001
    STANDARD = "standard"  # λ = 0.010
    EPHEMERAL = "ephemeral"  # λ = 0.050


@dataclass
class ScoredBlock:
    id: str
    content: str
    tags: list[str]
    similarity: float
    confidence: float
    recency: float
    centrality: float
    reinforcement: float
    score: float
    was_expanded: bool = False
    status: str = BlockStatus.ACTIVE.value

    @property
    def summary(self) -> str:
        tag_str = f" [{', '.join(self.tags[:3])}]" if self.tags else ""
        truncated = self.content[:80] + ("…" if len(self.content) > 80 else "")
        return f"[{self.score:.2f}] {truncated}{tag_str}"

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "tags": self.tags,
            "score": self.score,
            "similarity": self.similarity,
            "confidence": self.confidence,
            "recency": self.recency,
            "centrality": self.centrality,
            "reinforcement": self.reinforcement,
            "was_expanded": self.was_expanded,
            "status": self.status,
        }


@dataclass
class FrameResult:
    text: str
    blocks: list[ScoredBlock]
    frame_name: str
    cached: bool = False

    @property
    def summary(self) -> str:
        count = len(self.blocks)
        cached_note = " (cached)" if self.cached else ""
        if count == 0:
            return f"{self.frame_name} frame: no blocks found."
        noun = "block" if count == 1 else "blocks"
        return f"{self.frame_name} frame: {count} {noun} returned{cached_note}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict:
        return {
            "frame_name": self.frame_name,
            "block_count": len(self.blocks),
            "cached": self.cached,
            "text": self.text,
        }


@dataclass
class LearnResult:
    block_id: str
    status: str  # "created" | "duplicate_rejected" | "near_duplicate_superseded"

    @property
    def summary(self) -> str:
        short_id = self.block_id[:8]
        if self.status == "created":
            return f"Stored block {short_id}. Status: created."
        if self.status == "duplicate_rejected":
            return f"Duplicate — block {short_id} already exists."
        if self.status == "near_duplicate_superseded":
            return f"Updated block {short_id} (superseded near-duplicate)."
        # Fallback for future status values
        return f"Block {short_id}. Status: {self.status}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, str]:
        return {"block_id": self.block_id, "status": self.status}


@dataclass
class ConsolidateResult:
    processed: int
    promoted: int
    deduplicated: int
    edges_created: int

    @property
    def summary(self) -> str:
        if self.processed == 0:
            return "Nothing to consolidate. Inbox was empty."
        parts: list[str] = [f"{self.promoted} promoted"]
        if self.deduplicated:
            parts.append(f"{self.deduplicated} deduped")
        parts.append(f"{self.edges_created} edges")
        return f"Consolidated {self.processed}: {', '.join(parts)}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "promoted": self.promoted,
            "deduplicated": self.deduplicated,
            "edges_created": self.edges_created,
        }


@dataclass
class CurateResult:
    archived: int
    edges_pruned: int
    reinforced: int

    @property
    def summary(self) -> str:
        if not any([self.archived, self.edges_pruned, self.reinforced]):
            return "Curated: nothing required."
        parts: list[str] = []
        if self.archived:
            parts.append(f"{self.archived} archived")
        if self.edges_pruned:
            parts.append(f"{self.edges_pruned} edges pruned")
        if self.reinforced:
            parts.append(f"{self.reinforced} reinforced")
        return f"Curated: {', '.join(parts)}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, int]:
        return {
            "archived": self.archived,
            "edges_pruned": self.edges_pruned,
            "reinforced": self.reinforced,
        }


@dataclass(frozen=True)
class OutcomeResult:
    """Result of an outcome() call — Bayesian confidence updates from domain signals.

    Agent-friendly surface
    ----------------------
    - ``__str__`` → one-line summary optimised for agent context windows
    - ``summary`` → property; same as ``__str__``
    - ``to_dict()`` → JSON-serialisable dict for programmatic access
    """

    blocks_updated: int
    mean_confidence_delta: float
    edges_reinforced: int
    blocks_penalized: int = 0

    @property
    def summary(self) -> str:
        if self.blocks_updated == 0:
            return "Outcome recorded: nothing to update."
        noun = "block" if self.blocks_updated == 1 else "blocks"
        delta_str = f"{self.mean_confidence_delta:+.3f}"
        parts = [f"{self.blocks_updated} {noun} updated ({delta_str} avg confidence)"]
        if self.edges_reinforced:
            parts.append(f"{self.edges_reinforced} edges reinforced")
        if self.blocks_penalized:
            pen_noun = "block" if self.blocks_penalized == 1 else "blocks"
            parts.append(f"{self.blocks_penalized} {pen_noun} penalized")
        return f"Outcome recorded: {', '.join(parts)}."

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocks_updated": self.blocks_updated,
            "mean_confidence_delta": self.mean_confidence_delta,
            "edges_reinforced": self.edges_reinforced,
            "blocks_penalized": self.blocks_penalized,
        }


@dataclass(frozen=True)
class TokenUsage:
    """Immutable snapshot of token consumption for a time window.

    Returned via ``SystemStatus.session_tokens`` (current session) and
    ``SystemStatus.lifetime_tokens`` (all-time total).

    Agent-friendly surface
    ----------------------
    - ``str(usage)`` → compact one-liner for context injection
    - ``to_dict()``  → JSON-serialisable dict for programmatic access
    - ``usage_a + usage_b`` → combine two windows into one snapshot
    """

    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    embedding_tokens: int = 0
    llm_calls: int = 0
    embedding_calls: int = 0

    @property
    def llm_total_tokens(self) -> int:
        return self.llm_input_tokens + self.llm_output_tokens

    @property
    def total_tokens(self) -> int:
        return self.llm_total_tokens + self.embedding_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            llm_input_tokens=self.llm_input_tokens + other.llm_input_tokens,
            llm_output_tokens=self.llm_output_tokens + other.llm_output_tokens,
            embedding_tokens=self.embedding_tokens + other.embedding_tokens,
            llm_calls=self.llm_calls + other.llm_calls,
            embedding_calls=self.embedding_calls + other.embedding_calls,
        )

    @property
    def summary(self) -> str:
        if self.total_tokens == 0 and self.llm_calls == 0 and self.embedding_calls == 0:
            return "no token usage recorded"
        parts: list[str] = []
        if self.llm_calls:
            parts.append(f"LLM: {self.llm_total_tokens:,} tokens ({self.llm_calls} calls)")
        else:
            parts.append("LLM: \u2014")
        if self.embedding_calls:
            parts.append(f"Embed: {self.embedding_tokens:,} tokens ({self.embedding_calls} calls)")
        else:
            parts.append("Embed: \u2014")
        return " | ".join(parts)

    def __str__(self) -> str:
        return self.summary

    def to_dict(self) -> dict[str, int]:
        return {
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "embedding_tokens": self.embedding_tokens,
            "llm_calls": self.llm_calls,
            "embedding_calls": self.embedding_calls,
        }


@dataclass
class SystemStatus:
    """Snapshot of system state returned by MemorySystem.status().

    Use ``health`` and ``suggestion`` to decide on next actions.
    Use ``__str__`` for a compact one-line summary suitable for agent context.
    """

    session_active: bool
    session_hours: float | None   # None when no session is active
    inbox_count: int
    inbox_threshold: int
    active_count: int
    archived_count: int
    total_active_hours: float
    last_consolidated: str        # ISO timestamp string or "never"
    health: str                   # "good" | "attention" | "degraded"
    suggestion: str               # one actionable sentence
    session_tokens: TokenUsage = field(default_factory=TokenUsage)
    lifetime_tokens: TokenUsage = field(default_factory=TokenUsage)

    @property
    def summary(self) -> str:
        if self.session_active and self.session_hours is not None:
            session_str = f"active ({self.session_hours:.1f}h)"
        elif self.session_active:
            session_str = "active"
        else:
            session_str = "inactive"
        return (
            f"Session: {session_str} | "
            f"Inbox: {self.inbox_count}/{self.inbox_threshold} | "
            f"Active: {self.active_count} blocks | "
            f"Health: {self.health}"
        )

    def __str__(self) -> str:
        return (
            f"{self.summary}\n"
            f"Tokens this session: {self.session_tokens}\n"
            f"Suggestion: {self.suggestion}"
        )

    def to_dict(self) -> dict:
        return {
            "session_active": self.session_active,
            "session_hours": self.session_hours,
            "inbox_count": self.inbox_count,
            "inbox_threshold": self.inbox_threshold,
            "active_count": self.active_count,
            "archived_count": self.archived_count,
            "total_active_hours": self.total_active_hours,
            "last_consolidated": self.last_consolidated,
            "health": self.health,
            "suggestion": self.suggestion,
            "session_tokens": self.session_tokens.to_dict(),
            "lifetime_tokens": self.lifetime_tokens.to_dict(),
        }


@dataclass
class OperationRecord:
    """A single entry in the MemorySystem operation history.

    Returned by ``MemorySystem.history()``. In-memory only; resets on restart.
    """

    operation: str   # method name, e.g. "learn", "consolidate"
    summary: str     # str(result) captured at call time
    timestamp: str   # ISO 8601 UTC timestamp

    def __str__(self) -> str:
        # Show HH:MM:SS from the ISO timestamp for compact display
        time_str = self.timestamp[11:19] if len(self.timestamp) >= 19 else self.timestamp
        return f"{self.operation}()  →  {self.summary}  [{time_str}]"

    def to_dict(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "summary": self.summary,
            "timestamp": self.timestamp,
        }


@dataclass
class BlockAnalysis:
    """Result of combined block analysis during consolidation.

    Returned by LLMService.process_block(). Contains all three outputs
    produced in a single LLM call: alignment score, self-tags, and a
    normalised summary for embedding and rendering.
    """

    alignment_score: float  # [0.0, 1.0] — how strongly identity-aligned
    tags: list[str]         # self/* tags, already filtered to valid vocabulary
    summary: str            # factual distillation of raw content


@dataclass
class Edge:
    from_id: str  # canonical: min(A, B)
    to_id: str  # canonical: max(A, B)
    weight: float

    @staticmethod
    def canonical(id_a: str, id_b: str) -> tuple[str, str]:
        return (min(id_a, id_b), max(id_a, id_b))


@dataclass
class ContradictionRecord:
    block_a_id: str
    block_b_id: str
    score: float
    resolved: bool = False
    resolution: str = field(default="")
