"""Port protocols — the stable contracts between business logic and infrastructure.

All adapters (real and mock) must satisfy these protocols.
All tests code against these protocols, never against concrete implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np

from elfmem.types import BlockAnalysis


@runtime_checkable
class LLMService(Protocol):
    """LLM operations required by the elfmem memory system."""

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Analyse a block in one call: score alignment, infer tags, generate summary.

        Combines the formerly separate score_self_alignment and infer_self_tags
        calls into a single structured output request. Returns a BlockAnalysis
        with alignment_score in [0.0, 1.0], tags filtered to the valid vocabulary,
        and a normalised summary ready for embedding and rendering.
        """
        ...

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Return a float in [0.0, 1.0] indicating the contradiction strength
        between two blocks. >= threshold means active contradiction."""
        ...


@runtime_checkable
class EmbeddingService(Protocol):
    """Embedding operations required by the elfmem memory system."""

    async def embed(self, text: str) -> np.ndarray:
        """Return a normalised float32 embedding vector for the given text."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Return normalised float32 embedding vectors for a list of texts."""
        ...
