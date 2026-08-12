"""Pydantic response models for structured LLM output."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BlockAnalysisModel(BaseModel):
    """Structured response model for combined block analysis.

    Used by AnthropicLLMAdapter (tool use input_schema) and
    OpenAILLMAdapter (Pydantic JSON parse). Converted to
    types.BlockAnalysis after filtering tags against VALID_SELF_TAGS.
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


class AmendmentProposalModel(BaseModel):
    """Structured response for constitutional amendment proposals (v0.18).

    Used by AnthropicLLMAdapter (tool use input_schema) and
    OpenAILLMAdapter (Pydantic JSON parse). Converted to a plain
    dict[str, str] by ``LLMService.propose_amendment`` before returning,
    so callers don't depend on Pydantic internals.
    """

    proposed_content: str = Field(
        description=(
            "Complete replacement content for the constitutional block. "
            "Same voice and length range (±50%) as the original. May equal "
            "the original verbatim if the evidence does not justify a change."
        ),
    )
    rationale: str = Field(
        description=(
            "One or two sentences describing what the recent-activity evidence "
            "says about the constitutional block. Plain, specific, audit-readable."
        ),
    )


class GoalDirectedEdgeModel(BaseModel):
    """One proposed goal-directed connection (edge-metabolism Stage A)."""

    candidate_id: str = Field(
        description="Must exactly match a candidate id from the prompt's candidate list.",
    )
    reasoning: str = Field(
        description="One sentence naming the specific goal this connection serves.",
    )


class GoalDirectedEdgeProposalsModel(BaseModel):
    """Structured response for goal-directed edge proposals (edge-metabolism
    Stage A — see docs/plans/plan_edge_metabolism.md). Dry-run only: the
    caller never writes these to the edges table without a separate,
    explicitly-approved Stage B."""

    proposals: list[GoalDirectedEdgeModel] = Field(
        default_factory=list,
        description="0 to max_edges proposed connections. Empty is a valid, common answer.",
    )
