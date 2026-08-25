"""Adapter factory — selects and constructs the correct adapters from ElfmemConfig.

Detection rule for LLM provider:
    model starts with "claude"  →  AnthropicLLMAdapter  (Anthropic Messages API)
    everything else             →  OpenAILLMAdapter     (OpenAI-compatible API)

All embedding providers use OpenAIEmbeddingAdapter (OpenAI-compatible embeddings API).
Prompt templates and valid tag vocabulary are resolved once here, not inside the adapters.
"""

from __future__ import annotations

import os

from elfmem.config import ElfmemConfig
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.token_counter import TokenCounter


def _resolve_api_key(configured_env: str | None, *, default_env: str) -> str | None:
    """Resolve an API key from the configured or default env var (v2 step 5).

    Previously every OpenAI-compatible adapter always read the literal
    OPENAI_API_KEY variable regardless of base_url, so a provider whose key
    isn't named that (Together.ai, Groq, OpenRouter, ...) had no way to work
    without misnaming its own key OPENAI_API_KEY. ``configured_env`` (from
    LLMConfig.api_key_env / EmbeddingConfig.api_key_env) lets a config say
    which variable actually holds the key for this provider.

    When ``configured_env`` is unset, behaviour is unchanged: reads
    ``default_env``. May return None either way — the caller passes that
    straight through to the adapter, which is exactly today's lazy-failure
    behaviour (operations that never call the LLM/embedding service don't
    fail on a missing key; ``elfmem doctor --resolve`` is the place that
    proactively catches a misconfigured or missing key with a real call).
    """
    return os.getenv(configured_env or default_env)


def make_llm_adapter(cfg: ElfmemConfig, token_counter: TokenCounter) -> LLMService:
    """Construct the LLM adapter appropriate for the configured model.

    Detection: model names beginning with "claude" use AnthropicLLMAdapter.
    All other names use OpenAILLMAdapter (covers OpenAI, Ollama, Groq, Together,
    and any provider with an OpenAI-compatible chat completions endpoint).

    Prompt templates and valid_self_tags are resolved from PromptsConfig here so
    the adapters receive pre-resolved strings rather than config objects.
    """
    process_block_prompt = cfg.prompts.resolve_process_block()
    valid_self_tags = cfg.prompts.resolve_valid_tags()

    if cfg.llm.model.startswith("claude"):
        # Imported here, not at module scope: the anthropic and openai SDKs cost
        # ~600ms of import time between them, and retrieval-only entry points
        # (a queryless frame, `elfmem ls`, a prompt hook) construct neither
        # adapter. Only the branch actually taken pays for its SDK.
        from elfmem.adapters.anthropic import AnthropicLLMAdapter

        api_key = _resolve_api_key(cfg.llm.api_key_env, default_env="ANTHROPIC_API_KEY")
        return AnthropicLLMAdapter(
            model=cfg.llm.model,
            temperature=cfg.llm.temperature,
            max_tokens=cfg.llm.max_tokens,
            timeout=cfg.llm.timeout,
            max_retries=cfg.llm.max_retries,
            api_key=api_key,
            process_block_model=cfg.llm.process_block_model,
            process_block_prompt=process_block_prompt,
            valid_self_tags=valid_self_tags,
            token_counter=token_counter,
        )
    from elfmem.adapters.openai import OpenAILLMAdapter

    api_key = _resolve_api_key(cfg.llm.api_key_env, default_env="OPENAI_API_KEY")
    return OpenAILLMAdapter(
        model=cfg.llm.model,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        timeout=cfg.llm.timeout,
        max_retries=cfg.llm.max_retries,
        base_url=cfg.llm.base_url,
        api_key=api_key,
        process_block_model=cfg.llm.process_block_model,
        process_block_prompt=process_block_prompt,
        valid_self_tags=valid_self_tags,
        token_counter=token_counter,
    )


def make_embedding_adapter(
    cfg: ElfmemConfig, token_counter: TokenCounter
) -> EmbeddingService:
    """Construct the embedding adapter for the configured model.

    All supported embedding providers (OpenAI, Ollama, Together) use the
    OpenAI embeddings API format. Custom providers configure via
    embeddings.base_url and, if their key isn't OPENAI_API_KEY,
    embeddings.api_key_env (v2 step 5 — same mechanism as the LLM side).
    """
    from elfmem.adapters.openai import OpenAIEmbeddingAdapter

    api_key = _resolve_api_key(cfg.embeddings.api_key_env, default_env="OPENAI_API_KEY")
    return OpenAIEmbeddingAdapter(
        model=cfg.embeddings.model,
        dimensions=cfg.embeddings.dimensions,
        timeout=cfg.embeddings.timeout,
        base_url=cfg.embeddings.base_url,
        api_key=api_key,
        token_counter=token_counter,
    )
