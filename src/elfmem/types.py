"""Core domain types for elfmem.

These are the shared vocabulary for every layer. Changing these types requires
updating every module that imports them — treat as stable contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


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


@dataclass
class FrameResult:
    text: str
    blocks: list[ScoredBlock]
    frame_name: str
    cached: bool = False


@dataclass
class LearnResult:
    block_id: str
    status: str  # "created" | "duplicate_rejected" | "near_duplicate_superseded"


@dataclass
class ConsolidateResult:
    processed: int
    promoted: int
    deduplicated: int
    edges_created: int


@dataclass
class CurateResult:
    archived: int
    edges_pruned: int
    reinforced: int


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
