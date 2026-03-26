"""Adapter factory — selects and constructs the correct adapters from ElfmemConfig.

Detection rule for LLM provider:
    model starts with "claude"  →  AnthropicLLMAdapter  (Anthropic Messages API)
    everything else             →  OpenAILLMAdapter     (OpenAI-compatible API)

All embedding providers use OpenAIEmbeddingAdapter (OpenAI-compatible embeddings API).
Prompt templates and valid tag vocabulary are resolved once here, not inside the adapters.
"""

from __future__ import annotations

from elfmem.adapters.anthropic import AnthropicLLMAdapter
from elfmem.adapters.openai import OpenAIEmbeddingAdapter, OpenAILLMAdapter
from elfmem.config import ElfmemConfig
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.token_counter import TokenCounter


def make_llm_adapter(cfg: ElfmemConfig, token_counter: TokenCounter) -> LLMService:
    """Construct the LLM adapter appropriate for the configured model.

    Detection: model names beginning with "claude" use AnthropicLLMAdapter.
    All other names use OpenAILLMAdapter (covers OpenAI, Ollama, Groq, Together,
    and any provider with an OpenAI-compatible chat completions endpoint).

    Prompt templates and valid_self_tags are resolved from PromptsConfig here so
    the adapters receive pre-resolved strings rather than config objects.
    """
    process_block_prompt = cfg.prompts.resolve_process_block()
    contradiction_prompt = cfg.prompts.resolve_contradiction()
    valid_self_tags = cfg.prompts.resolve_valid_tags()

    if cfg.llm.model.startswith("claude"):
        return AnthropicLLMAdapter(
            model=cfg.llm.model,
            temperature=cfg.llm.temperature,
            max_tokens=cfg.llm.max_tokens,
            timeout=cfg.llm.timeout,
            max_retries=cfg.llm.max_retries,
            process_block_model=cfg.llm.process_block_model,
            contradiction_model=cfg.llm.contradiction_model,
            process_block_prompt=process_block_prompt,
            contradiction_prompt=contradiction_prompt,
            valid_self_tags=valid_self_tags,
            token_counter=token_counter,
        )
    return OpenAILLMAdapter(
        model=cfg.llm.model,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        timeout=cfg.llm.timeout,
        max_retries=cfg.llm.max_retries,
        base_url=cfg.llm.base_url,
        process_block_model=cfg.llm.process_block_model,
        contradiction_model=cfg.llm.contradiction_model,
        process_block_prompt=process_block_prompt,
        contradiction_prompt=contradiction_prompt,
        valid_self_tags=valid_self_tags,
        token_counter=token_counter,
    )


def make_embedding_adapter(
    cfg: ElfmemConfig, token_counter: TokenCounter
) -> EmbeddingService:
    """Construct the embedding adapter for the configured model.

    All supported embedding providers (OpenAI, Ollama) use the OpenAI
    embeddings API format. Custom providers configure via embeddings.base_url.
    """
    return OpenAIEmbeddingAdapter(
        model=cfg.embeddings.model,
        dimensions=cfg.embeddings.dimensions,
        timeout=cfg.embeddings.timeout,
        base_url=cfg.embeddings.base_url,
        token_counter=token_counter,
    )
