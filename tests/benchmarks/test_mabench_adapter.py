"""Tests for MemoryAgentBench adapter utilities."""

from benchmarks.memoryagentbench.adapter import _context_budget_words, build_elfmem_config
from benchmarks.memoryagentbench.config import MABenchConfig


class TestContextBudgetWords:
    def test_default_config_budget_positive(self) -> None:
        config = MABenchConfig()
        budget = _context_budget_words(config)
        assert budget > 100

    def test_larger_context_window_increases_budget(self) -> None:
        small = MABenchConfig(context_window_tokens=2048)
        large = MABenchConfig(context_window_tokens=8192)
        assert _context_budget_words(large) > _context_budget_words(small)

    def test_minimum_budget_is_100(self) -> None:
        tiny = MABenchConfig(context_window_tokens=100)
        assert _context_budget_words(tiny) == 100


class TestBuildElfmemConfig:
    def test_returns_valid_config(self) -> None:
        config = MABenchConfig()
        elfmem_cfg = build_elfmem_config(config)
        assert elfmem_cfg.llm.model == config.elfmem_llm_model
        assert elfmem_cfg.embeddings.model == config.elfmem_embedding_model
        assert elfmem_cfg.memory.top_k == config.top_k

    def test_contradiction_prefilter_forwarded(self) -> None:
        config = MABenchConfig(contradiction_similarity_prefilter=0.65)
        elfmem_cfg = build_elfmem_config(config)
        assert elfmem_cfg.memory.contradiction_similarity_prefilter == 0.65
