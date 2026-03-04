"""Pydantic response models for structured LLM output via instructor."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AlignmentScore(BaseModel):
    """Structured response for self-alignment scoring."""

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-alignment score: 0=unrelated, 1=core identity",
    )


class SelfTagInference(BaseModel):
    """Structured response for self-tag inference.

    Tags are the raw LLM output. Filtering against the valid tag vocabulary
    is the adapter's responsibility, not this model's.
    """

    tags: list[str] = Field(
        default_factory=list,
        description="Self/* tags inferred by the LLM.",
    )


class ContradictionScore(BaseModel):
    """Structured response for contradiction detection."""

    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Contradiction score: 0=compatible, 1=directly contradictory",
    )
