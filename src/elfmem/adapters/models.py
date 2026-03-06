"""Pydantic response models for structured LLM output via instructor."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BlockAnalysisModel(BaseModel):
    """Instructor-structured response for combined block analysis.

    Used internally by LiteLLMAdapter.process_block(). The adapter
    converts this to types.BlockAnalysis after filtering tags.
    """

    alignment_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-alignment score: 0=unrelated, 1=core identity",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Self/* tags inferred by the LLM.",
    )
    summary: str = Field(
        description=(
            "Factual 1-2 sentence distillation of the block. "
            "Preserves all specific details. Third person."
        ),
    )


class ContradictionScore(BaseModel):
    """Structured response for contradiction detection."""

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Contradiction score: 0=compatible, 1=directly contradictory",
    )
