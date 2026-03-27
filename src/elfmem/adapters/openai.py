"""LLM and embedding adapters backed by the official OpenAI Python SDK.

LLM (OpenAILLMAdapter):
    Covers OpenAI models and any OpenAI-compatible API (Ollama, Groq, Together,
    Mistral) via the base_url parameter.
    API key: OPENAI_API_KEY for OpenAI; provider-specific env vars for others.

Embedding (OpenAIEmbeddingAdapter):
    Same provider coverage via base_url.
    WARNING: embedding model is fixed at first use — changing it on an existing
    database requires re-embedding all stored blocks.
"""

from __future__ import annotations

import json

import numpy as np
import openai
from openai.types.chat import ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONObject
from pydantic import ValidationError

from elfmem.adapters.models import BlockAnalysisModel, ContradictionScore
from elfmem.prompts import BLOCK_ANALYSIS_PROMPT, CONTRADICTION_PROMPT, VALID_SELF_TAGS
from elfmem.token_counter import TokenCounter
from elfmem.types import BlockAnalysis


class OpenAILLMAdapter:
    """LLM service backed by the official OpenAI Python SDK.

    Uses JSON mode for structured outputs. Validates responses with Pydantic
    and retries on schema violations up to max_retries times.

    JSON mode is tested on the first call. Providers that reject it (some Ollama
    models) are remembered per adapter instance and fall back to plain text on all
    subsequent calls. The existing prompts already request JSON, so the fallback
    is reliable without any further changes.

    API key:    OPENAI_API_KEY for OpenAI. Other providers read their own env vars.
    base_url:   Provider's OpenAI-compatible endpoint (Ollama, Groq, Together, etc.)
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
        self._max_retries = max_retries
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
        # None = not yet tested, True = JSON mode works, False = not supported.
        # Tested lazily on first call to avoid BadRequestError on every retry.
        self._json_mode_supported: bool | None = None
        # Client is created lazily on first use so that operations which never
        # call the LLM (status, history, etc.) don't fail when OPENAI_API_KEY
        # is absent. The key is validated by the SDK when the first call is made.
        self._client_kwargs = {
            "api_key": api_key,
            "timeout": float(timeout),
            "max_retries": max_retries,
            "base_url": base_url,
        }
        self._client: openai.AsyncOpenAI | None = None

    @property
    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI(**self._client_kwargs)  # type: ignore[arg-type]
        return self._client

    def _effective_model(self, override: str | None) -> str:
        return override if override is not None else self._model

    def _record_usage(self, usage: openai.types.CompletionUsage | None) -> None:
        if usage is not None and self._token_counter is not None:
            self._token_counter.record_llm(
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
            )

    async def _complete(self, prompt: str, model: str) -> str:
        """Single chat completion. Tries JSON mode; falls back to plain text.

        JSON mode detection is cached per adapter instance so BadRequestError
        is raised at most once regardless of how many calls are made.
        """
        messages: list[ChatCompletionMessageParam] = [{"role": "user", "content": prompt}]
        client = self._get_client
        if self._json_mode_supported is not False:
            try:
                response = await client.chat.completions.create(
                    model=model,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                    messages=messages,
                    response_format=ResponseFormatJSONObject(type="json_object"),
                )
                self._json_mode_supported = True
                self._record_usage(response.usage)
                return response.choices[0].message.content or ""
            except openai.BadRequestError:
                # Provider does not support JSON mode — remember and use plain text.
                self._json_mode_supported = False
        response = await client.chat.completions.create(
            model=model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            messages=messages,
        )
        self._record_usage(response.usage)
        return response.choices[0].message.content or ""

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Analyse a memory block: alignment score, self-tags, and summary.

        USE WHEN:   Called by consolidate() for each inbox block.
        DON'T USE:  Testing — use MockLLMService instead (no API cost).
        COST:       1 OpenAI API call. Retries up to max_retries on schema violations.
        RETURNS:    BlockAnalysis with alignment_score ∈ [0,1], filtered tags, summary.
        NEXT:       Result stored to blocks table by consolidate().
        """
        prompt = self._process_block_prompt.format(
            self_context=self_context, block=block
        )
        model = self._effective_model(self._process_block_model)
        last_exc: Exception | None = None
        for _ in range(self._max_retries):
            text = await self._complete(prompt, model)
            try:
                result = BlockAnalysisModel.model_validate_json(text)
                filtered_tags = [t for t in result.tags if t in self._valid_self_tags]
                return BlockAnalysis(
                    alignment_score=result.alignment_score,
                    tags=filtered_tags,
                    summary=result.summary,
                )
            except (ValidationError, json.JSONDecodeError) as exc:
                last_exc = exc
        raise last_exc  # type: ignore[misc]

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Score the logical contradiction between two memory blocks.

        USE WHEN:   Called by consolidate() for candidate pairs above the cosine prefilter.
        DON'T USE:  Testing — use MockLLMService instead (no API cost).
        COST:       1 OpenAI API call. Retries up to max_retries on schema violations.
        RETURNS:    float ∈ [0.0, 1.0]; >= contradiction_threshold means active contradiction.
        NEXT:       Score compared against MemoryConfig.contradiction_threshold in consolidate().
        """
        prompt = self._contradiction_prompt.format(block_a=block_a, block_b=block_b)
        model = self._effective_model(self._contradiction_model)
        last_exc: Exception | None = None
        for _ in range(self._max_retries):
            text = await self._complete(prompt, model)
            try:
                result = ContradictionScore.model_validate_json(text)
                return float(result.score)
            except (ValidationError, json.JSONDecodeError) as exc:
                last_exc = exc
        raise last_exc  # type: ignore[misc]


class OpenAIEmbeddingAdapter:
    """Embedding service backed by the official OpenAI Python SDK.

    Works with any OpenAI-compatible embedding API via base_url:
      OpenAI:  text-embedding-3-small (default), text-embedding-3-large
      Ollama:  base_url="http://localhost:11434/v1", model="nomic-embed-text"

    All vectors are unit-normalised to float32.

    WARNING: The embedding model is fixed at first use. Changing the model on an
    existing database requires re-embedding all stored blocks.
    """

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        timeout: int = 30,
        base_url: str | None = None,
        api_key: str | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._model = model
        self._dimensions = dimensions  # Stored for reference; not sent to API.
        self._token_counter = token_counter
        # Lazy client creation — same rationale as OpenAILLMAdapter.
        self._client_kwargs = {
            "api_key": api_key,
            "timeout": float(timeout),
            "base_url": base_url,
        }
        self._client: openai.AsyncOpenAI | None = None

    @property
    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            self._client = openai.AsyncOpenAI(**self._client_kwargs)  # type: ignore[arg-type]
        return self._client

    def _record_usage(self, usage: object) -> None:
        """Record embedding token usage. Uses getattr for SDK version safety."""
        if self._token_counter is None:
            return
        tokens = getattr(usage, "prompt_tokens", None)
        if tokens:
            self._token_counter.record_embedding(tokens=int(tokens))

    @staticmethod
    def _normalise(raw: list[float]) -> np.ndarray:
        vec = np.array(raw, dtype=np.float32)
        norm = float(np.linalg.norm(vec))
        return (vec / norm) if norm > 0 else vec

    async def embed(self, text: str) -> np.ndarray:
        """Embed a single text and return a unit-normalised float32 vector.

        USE WHEN:   Single-text embedding (query vectors for frame retrieval).
        DON'T USE:  Multiple texts — use embed_batch() for ~5x fewer API calls.
        COST:       1 OpenAI embeddings API call.
        RETURNS:    Unit-normalised float32 ndarray.
        """
        response = await self._get_client.embeddings.create(
            model=self._model, input=[text]
        )
        self._record_usage(response.usage)
        return self._normalise(response.data[0].embedding)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts in a single API call.

        USE WHEN:   Batch consolidation — embeds all inbox blocks in one round-trip.
        DON'T USE:  Testing — use MockEmbeddingService instead (no API cost).
        COST:       1 OpenAI embeddings API call regardless of batch size.
        RETURNS:    List of unit-normalised float32 ndarrays, same order as input.
        """
        if not texts:
            return []
        response = await self._get_client.embeddings.create(
            model=self._model, input=texts
        )
        self._record_usage(response.usage)
        return [self._normalise(item.embedding) for item in response.data]
