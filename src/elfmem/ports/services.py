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

    async def propose_amendment(
        self,
        *,
        block_content: str,
        block_summary: str | None,
        drift_score: float,
        evidence_summaries: list[str],
    ) -> dict[str, str]:
        """Propose a single amendment for a drifted constitutional block (v0.18).

        Returns ``{"proposed_content": str, "rationale": str}``. Adapters
        MUST raise on parse / schema / API failure rather than return a
        sentinel — the orchestration counts raises as failed_proposal and
        continues. See ``operations/review.py::review_constitutional``.
        """
        ...

    async def propose_goal_directed_edges(
        self,
        *,
        block_content: str,
        block_summary: str | None,
        self_goals: list[str],
        candidates: list[tuple[str, str]],
        max_edges: int,
    ) -> list[dict[str, str]]:
        """Propose up to ``max_edges`` connections judged against the agent's
        own stated goals, not similarity (edge-metabolism Stage A — see
        docs/plans/plan_edge_metabolism.md). ``candidates`` is
        ``[(block_id, content_or_summary), ...]``.

        Returns ``[{"candidate_id": str, "reasoning": str}, ...]`` — possibly
        empty. Adapters MUST raise on parse / schema / API failure rather
        than return a sentinel, same contract as ``propose_amendment``; the
        orchestration treats a raise as zero proposals for this block and
        continues to the next one. Dry-run only in Stage A — nothing calling
        this may write to the ``edges`` table without a separately-approved
        Stage B.
        """
        ...


@runtime_checkable
class EmbeddingService(Protocol):
    """Embedding operations required by the elfmem memory system."""

    @property
    def model_name(self) -> str:
        """Identifier for the embedding model (e.g. 'text-embedding-nomic-embed-text-v1.5').
        Stored alongside block embeddings so the DB records which model produced them."""
        ...

    async def embed(self, text: str) -> np.ndarray:
        """Return a normalised float32 embedding vector for the given text."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Return normalised float32 embedding vectors for a list of texts."""
        ...
