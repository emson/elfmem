"""LLM adapter backed by the official Anthropic Python SDK.

Supported:  any claude-* model via the Anthropic Messages API.
API key:    ANTHROPIC_API_KEY environment variable.

Structured outputs use forced tool use — Anthropic guarantees the response
matches the declared input_schema, eliminating JSON parsing and retry.
"""

from __future__ import annotations

import anthropic

from elfmem.adapters.models import (
    AmendmentProposalModel,
    BlockAnalysisModel,
    ContradictionScore,
)
from elfmem.prompts import (
    AMENDMENT_PROPOSAL_PROMPT,
    BLOCK_ANALYSIS_PROMPT,
    CONTRADICTION_PROMPT,
    VALID_SELF_TAGS,
)
from elfmem.token_counter import TokenCounter
from elfmem.types import BlockAnalysis

# Tool names used for forced structured-output calls.
_ANALYZE_BLOCK_TOOL = "analyze_block"
_SCORE_CONTRADICTION_TOOL = "score_contradiction"
_PROPOSE_AMENDMENT_TOOL = "propose_amendment"


class AnthropicLLMAdapter:
    """LLM service backed by the official Anthropic Python SDK.

    Uses tool use with forced tool choice for structured outputs. Anthropic
    guarantees the response matches the tool input_schema so no JSON parsing
    or retry loop is needed.

    API key:   ANTHROPIC_API_KEY environment variable.
    Providers: all claude-* model identifiers via the Anthropic API.
    """

    def __init__(
        self,
        *,
        model: str = "claude-haiku-4-5-20251001",
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout: int = 30,
        max_retries: int = 3,
        api_key: str | None = None,
        process_block_model: str | None = None,
        contradiction_model: str | None = None,
        process_block_prompt: str | None = None,
        contradiction_prompt: str | None = None,
        valid_self_tags: frozenset[str] | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._process_block_model = process_block_model
        self._contradiction_model = contradiction_model
        self._process_block_prompt = (
            process_block_prompt if process_block_prompt is not None
            else BLOCK_ANALYSIS_PROMPT
        )
        self._contradiction_prompt = (
            contradiction_prompt if contradiction_prompt is not None
            else CONTRADICTION_PROMPT
        )
        self._valid_self_tags: frozenset[str] = (
            valid_self_tags if valid_self_tags is not None else VALID_SELF_TAGS
        )
        self._token_counter = token_counter
        # api_key=None is the SDK's own documented default: fall back to
        # ANTHROPIC_API_KEY. Passing it explicitly (v2 step 5) only changes
        # behaviour when the caller resolved a key from a differently-named
        # env var via LLMConfig.api_key_env.
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=float(timeout),
            max_retries=max_retries,
        )

    def _effective_model(self, override: str | None) -> str:
        return override if override is not None else self._model

    def _record_usage(self, usage: anthropic.types.Usage | None) -> None:
        """Always record the call; tokens are best-effort.

        Symmetric with the OpenAI adapters. Anthropic's SDK always returns
        usage in practice, but ``None`` is handled for safety.
        """
        if self._token_counter is None:
            return
        if usage is None:
            self._token_counter.record_llm()
            return
        self._token_counter.record_llm(
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
        )

    async def _call_tool(
        self,
        tool_name: str,
        description: str,
        schema: dict[str, object],
        prompt: str,
        model: str,
    ) -> object:
        """Make one Anthropic Messages API call with forced tool use.

        Returns tool_block.input (a dict matching the schema).
        Anthropic guarantees a ToolUseBlock when tool_choice forces a specific tool.
        """
        response = await self._client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            tools=[
                {
                    "name": tool_name,
                    "description": description,
                    "input_schema": schema,
                }
            ],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(response.usage)
        tool_block = response.content[0]
        assert isinstance(tool_block, anthropic.types.ToolUseBlock), (
            f"Expected ToolUseBlock, got {type(tool_block).__name__}. "
            "This should not happen with forced tool_choice."
        )
        return tool_block.input

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Analyse a memory block: alignment score, self-tags, and summary.

        USE WHEN:   Called by consolidate() for each inbox block.
        DON'T USE:  Testing — use MockLLMService instead (no API cost).
        COST:       1 Anthropic API call via forced tool use.
        RETURNS:    BlockAnalysis with alignment_score ∈ [0,1], filtered tags, summary.
        NEXT:       Result stored to blocks table by consolidate().
        """
        prompt = self._process_block_prompt.format(
            self_context=self_context, block=block
        )
        raw = await self._call_tool(
            tool_name=_ANALYZE_BLOCK_TOOL,
            description="Analyze a memory block for self-alignment, tags, and a summary.",
            schema=BlockAnalysisModel.model_json_schema(),
            prompt=prompt,
            model=self._effective_model(self._process_block_model),
        )
        result = BlockAnalysisModel.model_validate(raw)
        filtered_tags = [t for t in result.tags if t in self._valid_self_tags]
        return BlockAnalysis(
            alignment_score=result.alignment_score,
            tags=filtered_tags,
            summary=result.summary,
        )

    async def propose_amendment(
        self,
        *,
        block_content: str,
        block_summary: str | None,
        drift_score: float,
        evidence_summaries: list[str],
    ) -> dict[str, str]:
        """Propose a single amendment for a drifted constitutional block.

        USE WHEN:   Called by review_constitutional() for each over-threshold block.
        DON'T USE:  Testing — use MockLLMService.
        COST:       1 Anthropic API call via forced tool use.
        RETURNS:    {"proposed_content": str, "rationale": str}.
        NEXT:       The orchestration wraps this in a ProposedAmendment and
                    surfaces it to the agent for explicit accept/reject.
        """
        evidence_block = (
            "\n".join(f"- {s}" for s in evidence_summaries)
            if evidence_summaries
            else "(no recent reinforced activity summaries available)"
        )
        prompt = AMENDMENT_PROPOSAL_PROMPT.format(
            block_content=block_content,
            block_summary=block_summary or "(no summary)",
            drift_score=f"{drift_score:.3f}",
            evidence_summaries=evidence_block,
        )
        raw = await self._call_tool(
            tool_name=_PROPOSE_AMENDMENT_TOOL,
            description=(
                "Propose an amendment to a constitutional memory block based "
                "on the agent's recent operational activity."
            ),
            schema=AmendmentProposalModel.model_json_schema(),
            prompt=prompt,
            model=self._effective_model(self._process_block_model),
        )
        result = AmendmentProposalModel.model_validate(raw)
        return {
            "proposed_content": result.proposed_content,
            "rationale": result.rationale,
        }

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Score the logical contradiction between two memory blocks.

        USE WHEN:   Called by consolidate() for candidate pairs above the cosine prefilter.
        DON'T USE:  Testing — use MockLLMService instead (no API cost).
        COST:       1 Anthropic API call via forced tool use.
        RETURNS:    float ∈ [0.0, 1.0]; >= contradiction_threshold means active contradiction.
        NEXT:       Score compared against MemoryConfig.contradiction_threshold in consolidate().
        """
        prompt = self._contradiction_prompt.format(block_a=block_a, block_b=block_b)
        raw = await self._call_tool(
            tool_name=_SCORE_CONTRADICTION_TOOL,
            description="Score the logical contradiction between two memory blocks.",
            schema=ContradictionScore.model_json_schema(),
            prompt=prompt,
            model=self._effective_model(self._contradiction_model),
        )
        result = ContradictionScore.model_validate(raw)
        return float(result.score)
