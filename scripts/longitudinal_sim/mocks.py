"""Topic-aware mock services for the longitudinal harness.

Both inherit the protocol shape (async embed / process_block / detect_contradiction)
but use the ground-truth Topic space instead of hash-based pseudo-randomness.
"""
from __future__ import annotations

import numpy as np

from elfmem.types import BlockAnalysis

from .topic import DIM, Topic, content_to_topic


class TopicEmbeddingService:
    """Returns the block's Topic vector as the embedding (padded to `dimensions`).

    If the block content doesn't carry a [TOPIC=...] marker (e.g. SELF seed
    blocks loaded by elfmem itself), falls back to a deterministic zero-vector
    (centred — neutral to all queries).
    """

    def __init__(self, *, dimensions: int = 64) -> None:
        if dimensions < DIM:
            raise ValueError(f"dimensions must be ≥ {DIM}")
        self._dimensions = dimensions
        self.embed_calls = 0

    @property
    def model_name(self) -> str:
        return "topic-mock"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> np.ndarray:
        self.embed_calls += 1
        topic = content_to_topic(text)
        vec = np.zeros(self._dimensions, dtype=np.float32)
        if topic is not None:
            for i, v in enumerate(topic.values):
                vec[i] = v
            n = float(np.linalg.norm(vec))
            if n > 0:
                vec = vec / n
        return vec.astype(np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        return [await self.embed(t) for t in texts]


class TopicAlignmentLLM:
    """Mock LLM whose alignment_score is the cosine between the block's Topic
    and a fixed simulated SELF Topic.

    Returns BlockAnalysis(alignment_score=cos(block, self), tags=[], summary=...).
    Contradiction score is the *negative* cosine clipped to [0, 1] — opposed
    topics signal contradiction.
    """

    def __init__(self, *, self_topic: Topic) -> None:
        self._self = self_topic
        self.process_block_calls = 0
        self.contradiction_calls = 0

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        self.process_block_calls += 1
        topic = content_to_topic(block)
        if topic is None:
            alignment = 0.5
        else:
            cos = topic.cosine(self._self)
            alignment = (cos + 1.0) / 2.0
        return BlockAnalysis(
            alignment_score=float(alignment),
            tags=["sim/topic"],
            summary=f"sim-summary({block[:40]}...)",
        )

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        self.contradiction_calls += 1
        ta = content_to_topic(block_a)
        tb = content_to_topic(block_b)
        if ta is None or tb is None:
            return 0.1
        cos = ta.cosine(tb)
        return max(0.0, -cos)
