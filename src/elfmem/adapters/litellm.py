"""Real LLM and embedding service adapters backed by LiteLLM + instructor."""

from __future__ import annotations

import instructor
import litellm
import numpy as np

from elfmem.adapters.models import BlockAnalysisModel, ContradictionScore
from elfmem.prompts import (
    BLOCK_ANALYSIS_PROMPT,
    CONTRADICTION_PROMPT,
    VALID_SELF_TAGS,
)
from elfmem.token_counter import TokenCounter
from elfmem.types import BlockAnalysis


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
        process_block_model: Per-call model override for process_block.
        contradiction_model: Per-call model override for contradiction detection.
        process_block_prompt: Override the combined block analysis prompt template.
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
        self._timeout = timeout
        self._max_retries = max_retries
        self._base_url = base_url

        self._process_block_model = process_block_model
        self._contradiction_model = contradiction_model

        # Resolve prompts at construction time (no I/O per call)
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

    def _record_llm_usage(self, completion: object) -> None:
        """Record token usage from a completion response, if counter is active."""
        if self._token_counter is None:
            return
        usage = getattr(completion, "usage", None)
        if usage is None:
            return
        self._token_counter.record_llm(
            input_tokens=getattr(usage, "prompt_tokens", None) or 0,
            output_tokens=getattr(usage, "completion_tokens", None) or 0,
        )

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Analyse a block: alignment score + self-tags + summary in one LLM call."""
        prompt = self._process_block_prompt.format(
            self_context=self_context, block=block
        )
        result, completion = await self._client.chat.completions.create_with_completion(
            messages=[{"role": "user", "content": prompt}],
            response_model=BlockAnalysisModel,
            max_retries=self._max_retries,
            **self._call_kwargs(self._process_block_model),
        )
        self._record_llm_usage(completion)
        filtered_tags = [tag for tag in result.tags if tag in self._valid_self_tags]
        return BlockAnalysis(
            alignment_score=result.alignment_score,
            tags=filtered_tags,
            summary=result.summary,
        )

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Score how contradictory two blocks are."""
        prompt = self._contradiction_prompt.format(block_a=block_a, block_b=block_b)
        result, completion = await self._client.chat.completions.create_with_completion(
            messages=[{"role": "user", "content": prompt}],
            response_model=ContradictionScore,
            max_retries=self._max_retries,
            **self._call_kwargs(self._contradiction_model),
        )
        self._record_llm_usage(completion)
        return float(result.score)


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
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout
        self._base_url = base_url
        self._token_counter = token_counter

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

        if self._token_counter is not None:
            usage = getattr(response, "usage", None)
            if usage is not None:
                self._token_counter.record_embedding(
                    tokens=getattr(usage, "prompt_tokens", None) or 0,
                )

        raw = response.data[0]["embedding"]
        vec = np.array(raw, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        if norm > 0:
            vec = vec / norm
        return vec

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts in a single API call.

        OPTIMIZATION: Batch embedding calls reduce API overhead by ~5x.
        Example: 100 texts → 1 API call (instead of 100).

        Args:
            texts: List of texts to embed.

        Returns:
            List of unit-normalised float32 ndarrays, same length as input.
        """
        if not texts:
            return []

        kwargs: dict[str, object] = {
            "model": self._model,
            "input": texts,
            "timeout": self._timeout,
        }
        if self._base_url is not None:
            kwargs["api_base"] = self._base_url

        response = await litellm.aembedding(**kwargs)

        if self._token_counter is not None:
            usage = getattr(response, "usage", None)
            if usage is not None:
                self._token_counter.record_embedding(
                    tokens=getattr(usage, "prompt_tokens", None) or 0,
                )

        vecs: list[np.ndarray] = []
        for item in response.data:
            raw = item["embedding"]
            vec = np.array(raw, dtype=np.float32)
            norm = float(np.linalg.norm(vec))
            if norm > 0:
                vec = vec / norm
            vecs.append(vec)

        return vecs
