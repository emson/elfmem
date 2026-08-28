"""Tests for adapters/factory.py — provider-aware API key resolution (v2 step 5).

Previously every OpenAI-compatible adapter always read the literal
OPENAI_API_KEY env var regardless of base_url, so a provider whose key isn't
named that (Together.ai, Groq, OpenRouter, ...) had no way to work without
misnaming its own key OPENAI_API_KEY. LLMConfig.api_key_env /
EmbeddingConfig.api_key_env fix that.
"""

from __future__ import annotations

import pytest

from elfmem.adapters.factory import (
    _resolve_api_key,
    make_embedding_adapter,
    make_llm_adapter,
)
from elfmem.config import ElfmemConfig, EmbeddingConfig, LLMConfig
from elfmem.token_counter import TokenCounter


class TestResolveApiKey:
    def test_uses_default_env_when_unconfigured(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-default")
        assert _resolve_api_key(None, default_env="OPENAI_API_KEY") == "sk-default"

    def test_configured_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("TOGETHER_API_KEY", "sk-together")
        assert (
            _resolve_api_key("TOGETHER_API_KEY", default_env="OPENAI_API_KEY")
            == "sk-together"
        )

    def test_returns_none_when_neither_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SOME_UNSET_KEY", raising=False)
        assert _resolve_api_key("SOME_UNSET_KEY", default_env="ALSO_UNSET") is None


class TestMakeLlmAdapterKeyResolution:
    def test_claude_model_defaults_to_anthropic_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
        captured: dict[str, object] = {}

        class _FakeAnthropicClient:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(
            "elfmem.adapters.anthropic.anthropic.AsyncAnthropic", _FakeAnthropicClient
        )
        cfg = ElfmemConfig(llm=LLMConfig(model="claude-haiku-4-5-20251001"))
        make_llm_adapter(cfg, TokenCounter())
        assert captured["api_key"] == "sk-ant-real"

    def test_claude_model_respects_api_key_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong")
        monkeypatch.setenv("CORP_ANTHROPIC_KEY", "sk-ant-via-proxy")
        captured: dict[str, object] = {}

        class _FakeAnthropicClient:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        monkeypatch.setattr(
            "elfmem.adapters.anthropic.anthropic.AsyncAnthropic", _FakeAnthropicClient
        )
        cfg = ElfmemConfig(
            llm=LLMConfig(
                model="claude-haiku-4-5-20251001", api_key_env="CORP_ANTHROPIC_KEY"
            )
        )
        make_llm_adapter(cfg, TokenCounter())
        assert captured["api_key"] == "sk-ant-via-proxy"

    def test_non_claude_model_defaults_to_openai_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        cfg = ElfmemConfig(llm=LLMConfig(model="gpt-4o-mini"))
        adapter = make_llm_adapter(cfg, TokenCounter())
        assert adapter._client_kwargs["api_key"] == "sk-openai-real"  # type: ignore[attr-defined]

    def test_together_ai_uses_configured_api_key_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-unrelated-openai-key")
        monkeypatch.setenv("TOGETHER_API_KEY", "sk-together-real")
        cfg = ElfmemConfig(
            llm=LLMConfig(
                model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
                base_url="https://api.together.xyz/v1",
                api_key_env="TOGETHER_API_KEY",
            )
        )
        adapter = make_llm_adapter(cfg, TokenCounter())
        assert adapter._client_kwargs["api_key"] == "sk-together-real"  # type: ignore[attr-defined]
        assert adapter._client_kwargs["base_url"] == "https://api.together.xyz/v1"  # type: ignore[attr-defined]

    def test_missing_configured_key_resolves_to_none_not_wrong_key(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """A misconfigured api_key_env must not silently fall back to
        OPENAI_API_KEY and send an unrelated key to the wrong provider."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-be-used")
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        cfg = ElfmemConfig(
            llm=LLMConfig(model="some-model", api_key_env="TOGETHER_API_KEY")
        )
        adapter = make_llm_adapter(cfg, TokenCounter())
        assert adapter._client_kwargs["api_key"] is None  # type: ignore[attr-defined]


class TestMakeEmbeddingAdapterKeyResolution:
    def test_defaults_to_openai_api_key(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-real")
        cfg = ElfmemConfig(embeddings=EmbeddingConfig())
        adapter = make_embedding_adapter(cfg, TokenCounter())
        assert adapter._client_kwargs["api_key"] == "sk-openai-real"  # type: ignore[attr-defined]

    def test_together_ai_uses_configured_api_key_env(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("TOGETHER_API_KEY", "sk-together-embeddings")
        cfg = ElfmemConfig(
            embeddings=EmbeddingConfig(
                base_url="https://api.together.xyz/v1",
                api_key_env="TOGETHER_API_KEY",
            )
        )
        adapter = make_embedding_adapter(cfg, TokenCounter())
        assert adapter._client_kwargs["api_key"] == "sk-together-embeddings"  # type: ignore[attr-defined]
