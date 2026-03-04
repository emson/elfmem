"""Port protocols — the stable contracts between business logic and infrastructure.

All adapters (real and mock) must satisfy these protocols.
All tests code against these protocols, never against concrete implementations.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class LLMService(Protocol):
    """LLM operations required by the elfmem memory system."""

    async def score_self_alignment(self, block: str, self_context: str) -> float:
        """Return a float in [0.0, 1.0] indicating how well this block aligns
        with the agent's established identity/preferences."""
        ...

    async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
        """Return a list of self/* tags applicable to this block (may be empty)."""
        ...

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Return a float in [0.0, 1.0] indicating the contradiction strength
        between two blocks. ≥ threshold means active contradiction."""
        ...


@runtime_checkable
class EmbeddingService(Protocol):
    """Embedding operations required by the elfmem memory system."""

    async def embed(self, text: str) -> np.ndarray:
        """Return a normalised float32 embedding vector for the given text."""
        ...
