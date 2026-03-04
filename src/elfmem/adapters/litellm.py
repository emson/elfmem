"""Real LLM and embedding service adapters backed by LiteLLM + instructor."""

from __future__ import annotations

import instructor
import litellm
import numpy as np

from elfmem.adapters.models import AlignmentScore, ContradictionScore, SelfTagInference
from elfmem.prompts import (
    CONTRADICTION_PROMPT,
    SELF_ALIGNMENT_PROMPT,
    SELF_TAG_PROMPT,
    VALID_SELF_TAGS,
)


class LiteLLMAdapter:
    """LLM service backed by any LiteLLM-supported provider.

    API keys are read from environment variables by LiteLLM automatically:
      OpenAI     → OPENAI_API_KEY
      Anthropic  → ANTHROPIC_API_KEY
      Groq       → GROQ_API_KEY
      Ollama     → no key needed (base_url required)

    Args:
        model: LiteLLM model string (e.g. "gpt-4o-mini",
               "anthropic/claude-haiku-4-5-20251001").
        temperature: Sampling temperature. Default 0.0 (deterministic).
        max_tokens: Maximum response tokens. Default 512.
        timeout: Request timeout in seconds. Default 30.
        max_retries: instructor retry count on malformed output. Default 3.
        base_url: Optional base URL for local/proxy endpoints (e.g. Ollama).
        alignment_model: Per-call model override for alignment scoring.
        tags_model: Per-call model override for tag inference.
        contradiction_model: Per-call model override for contradiction detection.
        alignment_prompt: Override alignment prompt template.
        tag_prompt: Override tag prompt template.
        contradiction_prompt: Override contradiction prompt template.
        valid_self_tags: Override valid tag vocabulary.
    """

    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout: int = 30,
        max_retries: int = 3,
        base_url: str | None = None,
        alignment_model: str | None = None,
        tags_model: str | None = None,
        contradiction_model: str | None = None,
        alignment_prompt: str | None = None,
        tag_prompt: str | None = None,
        contradiction_prompt: str | None = None,
        valid_self_tags: frozenset[str] | None = None,
    ) -> None:
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._max_retries = max_retries
        self._base_url = base_url

        self._alignment_model = alignment_model
        self._tags_model = tags_model
        self._contradiction_model = contradiction_model

        # Resolve prompts at construction time (no I/O per call)
        self._alignment_prompt = (
            alignment_prompt if alignment_prompt is not None else SELF_ALIGNMENT_PROMPT
        )
        self._tag_prompt = tag_prompt if tag_prompt is not None else SELF_TAG_PROMPT
        self._contradiction_prompt = (
            contradiction_prompt if contradiction_prompt is not None else CONTRADICTION_PROMPT
        )
        self._valid_self_tags: frozenset[str] = (
            valid_self_tags if valid_self_tags is not None else VALID_SELF_TAGS
        )

        self._client = instructor.from_litellm(litellm.acompletion)

    def _call_kwargs(self, model_override: str | None = None) -> dict[str, object]:
        """Build kwargs for instructor/litellm call."""
        kwargs: dict[str, object] = {
            "model": model_override if model_override is not None else self._model,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "timeout": self._timeout,
        }
        if self._base_url is not None:
            kwargs["api_base"] = self._base_url
        return kwargs

    async def score_self_alignment(self, block: str, self_context: str) -> float:
        """Score how much a block reflects the agent's identity."""
        prompt = self._alignment_prompt.format(self_context=self_context, block=block)
        result: AlignmentScore = await self._client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            response_model=AlignmentScore,
            max_retries=self._max_retries,
            **self._call_kwargs(self._alignment_model),
        )
        return result.score

    async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
        """Infer self/* tags for a block, filtered to the valid vocabulary."""
        prompt = self._tag_prompt.format(self_context=self_context, block=block)
        result: SelfTagInference = await self._client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            response_model=SelfTagInference,
            max_retries=self._max_retries,
            **self._call_kwargs(self._tags_model),
        )
        return [tag for tag in result.tags if tag in self._valid_self_tags]

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Score how contradictory two blocks are."""
        prompt = self._contradiction_prompt.format(block_a=block_a, block_b=block_b)
        result: ContradictionScore = await self._client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            response_model=ContradictionScore,
            max_retries=self._max_retries,
            **self._call_kwargs(self._contradiction_model),
        )
        return result.score


class LiteLLMEmbeddingAdapter:
    """Embedding service backed by any LiteLLM-supported embedding provider.

    Args:
        model: LiteLLM embedding model (e.g. "text-embedding-3-small").
        dimensions: Expected embedding dimensions (must match stored embeddings).
        timeout: Request timeout in seconds. Default 30.
        base_url: Optional base URL for local/proxy endpoints.
    """

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        timeout: int = 30,
        base_url: str | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout
        self._base_url = base_url

    async def embed(self, text: str) -> np.ndarray:
        """Embed text and return a unit-normalised float32 ndarray."""
        kwargs: dict[str, object] = {
            "model": self._model,
            "input": [text],
            "timeout": self._timeout,
        }
        if self._base_url is not None:
            kwargs["api_base"] = self._base_url

        response = await litellm.aembedding(**kwargs)
        raw = response.data[0]["embedding"]
        vec = np.array(raw, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec
