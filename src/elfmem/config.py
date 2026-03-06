"""ElfmemConfig — configuration models for the elfmem memory system."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """Configuration for the LLM service.

    API keys are NOT stored here — they come from environment variables.
    LiteLLM reads standard env vars automatically:
      OpenAI     → OPENAI_API_KEY
      Anthropic  → ANTHROPIC_API_KEY
      Groq       → GROQ_API_KEY
    """

    model: str = "claude-haiku-4-5-20251001"
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 30
    max_retries: int = 3
    base_url: str | None = None

    # Per-call model overrides — None = use model above
    process_block_model: str | None = None
    contradiction_model: str | None = None


class EmbeddingConfig(BaseModel):
    """Configuration for the embedding service.

    WARNING: dimensions must match what is stored in the database.
    Changing the embedding model requires re-embedding all blocks.
    """

    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    timeout: int = 30
    base_url: str | None = None


class MemoryConfig(BaseModel):
    """Configuration for memory system thresholds and tunables."""

    # Lifecycle thresholds
    inbox_threshold: int = 10
    curate_interval_hours: float = 40.0
    prune_threshold: float = 0.05
    search_window_hours: float = 200.0
    vector_n_seeds_multiplier: int = 4

    # Quality thresholds
    self_alignment_threshold: float = 0.70
    contradiction_threshold: float = 0.80
    near_dup_exact_threshold: float = 0.95
    near_dup_near_threshold: float = 0.90

    # Graph
    similarity_edge_threshold: float = 0.60
    edge_degree_cap: int = 10
    edge_prune_threshold: float = 0.10
    edge_reinforce_delta: float = 0.10

    # Scoring
    top_k: int = 5

    # Curate
    curate_reinforce_top_n: int = 5

    # Outcome scoring
    outcome_prior_strength: float = 2.0
    # Weight of LLM alignment prior in Bayesian update.
    # 2.0 = alignment has the weight of 2 observations; evidence dominates after ~10 outcomes.

    outcome_reinforce_threshold: float = 0.5
    # Minimum signal to trigger block reinforcement and Hebbian edge learning.
    # Blocks below this threshold receive no reinforcement (decay naturally).

    penalize_threshold: float = 0.20
    # Signal below this triggers decay acceleration (decay_lambda *= penalty_factor).
    # Dead-band: signals in [penalize_threshold, reinforce_threshold] adjust confidence only.

    penalty_factor: float = 2.0
    # Multiplier applied to decay_lambda on penalization. Capped at lambda_ceiling.

    lambda_ceiling: float = 0.050
    # Maximum decay_lambda after penalization. Equals EPHEMERAL tier (0.050).


class PromptsConfig(BaseModel):
    """Configuration for LLM prompt templates.

    Three levels of override:
    1. Inline string (self_alignment, self_tags, contradiction)
    2. File path (self_alignment_file, etc.) — resolved relative to cwd
    3. Subclassing LiteLLMAdapter (escape hatch for full control)

    Inline takes priority over file. If neither is set, library defaults
    from elfmem.prompts are used.
    """

    # Level 1: Inline overrides — None = use library default
    process_block: str | None = None
    contradiction: str | None = None

    # Level 2: File path overrides — None = not used
    process_block_file: str | None = None
    contradiction_file: str | None = None

    # Tag vocabulary override — None = use VALID_SELF_TAGS from prompts.py
    valid_self_tags: list[str] | None = None

    def resolve_process_block(self) -> str:
        """Resolve the block analysis prompt: inline > file > default."""
        return self._resolve(self.process_block, self.process_block_file, "process_block")

    def resolve_contradiction(self) -> str:
        """Resolve the contradiction prompt: inline > file > default."""
        return self._resolve(self.contradiction, self.contradiction_file, "contradiction")

    def resolve_valid_tags(self) -> frozenset[str]:
        """Resolve the valid tag vocabulary."""
        if self.valid_self_tags is not None:
            return frozenset(self.valid_self_tags)
        from elfmem.prompts import VALID_SELF_TAGS
        return VALID_SELF_TAGS

    def validate_templates(self) -> None:
        """Raise ValueError if any resolved prompt is missing required variables."""
        _check_vars(self.resolve_process_block(), ["self_context", "block"], "process_block")
        _check_vars(self.resolve_contradiction(), ["block_a", "block_b"], "contradiction")

    @staticmethod
    def _resolve(inline: str | None, filepath: str | None, prompt_name: str) -> str:
        if inline is not None:
            return inline
        if filepath is not None:
            return Path(filepath).read_text(encoding="utf-8")
        from elfmem import prompts
        defaults = {
            "process_block": prompts.BLOCK_ANALYSIS_PROMPT,
            "contradiction": prompts.CONTRADICTION_PROMPT,
        }
        return defaults[prompt_name]


def _check_vars(template: str, required: list[str], name: str) -> None:
    missing = [v for v in required if f"{{{v}}}" not in template]
    if missing:
        raise ValueError(
            f"Prompt '{name}' is missing required variables: {missing}"
        )


class ElfmemConfig(BaseModel):
    """Top-level configuration for the elfmem memory system.

    Supports construction from:
    - Python code: ElfmemConfig(llm=LLMConfig(...), ...)
    - YAML file: ElfmemConfig.from_yaml("config.yaml")
    - Dict: ElfmemConfig.model_validate({...})
    - Environment: ElfmemConfig.from_env()
    - Defaults: ElfmemConfig()
    """

    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)

    @classmethod
    def from_yaml(cls, path: str) -> ElfmemConfig:
        """Load config from a YAML file. All sections are optional."""
        with open(Path(path).expanduser()) as f:
            data = yaml.safe_load(f)
        # Remove None values so empty sections (e.g., "prompts:" with only comments)
        # trigger default_factory instead of validation error.
        data = {k: v for k, v in (data or {}).items() if v is not None}
        return cls.model_validate(data)

    @classmethod
    def from_env(cls) -> ElfmemConfig:
        """Load config from ELFMEM_ prefixed environment variables."""
        data: dict[str, dict[str, object]] = {"llm": {}, "embeddings": {}}
        if model := os.getenv("ELFMEM_LLM_MODEL"):
            data["llm"]["model"] = model
        if base_url := os.getenv("ELFMEM_LLM_BASE_URL"):
            data["llm"]["base_url"] = base_url
        if emb_model := os.getenv("ELFMEM_EMBEDDING_MODEL"):
            data["embeddings"]["model"] = emb_model
        if emb_dim := os.getenv("ELFMEM_EMBEDDING_DIMENSIONS"):
            data["embeddings"]["dimensions"] = int(emb_dim)
        if emb_url := os.getenv("ELFMEM_EMBEDDING_BASE_URL"):
            data["embeddings"]["base_url"] = emb_url
        return cls.model_validate(data)


def render_default_config() -> str:
    """Render a commented config.yaml string from ElfmemConfig() defaults.

    Values are sourced from ElfmemConfig() so the output always reflects
    current code defaults. Used by ``elfmem init`` to generate config files.
    """
    import textwrap
    d = ElfmemConfig()
    return textwrap.dedent(f"""\
        # elfmem configuration
        # Generated by: elfmem init
        # Edit as needed. All sections are optional — missing keys use code defaults.
        # API keys are NOT stored here. Set them as environment variables:
        #   ANTHROPIC_API_KEY, OPENAI_API_KEY, GROQ_API_KEY, etc.

        llm:
          model: "{d.llm.model}"
          temperature: {d.llm.temperature}
          max_tokens: {d.llm.max_tokens}
          timeout: {d.llm.timeout}
          max_retries: {d.llm.max_retries}

        embeddings:
          model: "{d.embeddings.model}"
          dimensions: {d.embeddings.dimensions}
          timeout: {d.embeddings.timeout}

        memory:
          inbox_threshold: {d.memory.inbox_threshold}
          curate_interval_hours: {d.memory.curate_interval_hours}
          self_alignment_threshold: {d.memory.self_alignment_threshold}
          contradiction_threshold: {d.memory.contradiction_threshold}
          near_dup_exact_threshold: {d.memory.near_dup_exact_threshold}
          near_dup_near_threshold: {d.memory.near_dup_near_threshold}
          similarity_edge_threshold: {d.memory.similarity_edge_threshold}
          edge_degree_cap: {d.memory.edge_degree_cap}
          top_k: {d.memory.top_k}
          search_window_hours: {d.memory.search_window_hours}
          outcome_prior_strength: {d.memory.outcome_prior_strength}
          outcome_reinforce_threshold: {d.memory.outcome_reinforce_threshold}
          penalize_threshold: {d.memory.penalize_threshold}

        # Custom prompts (optional — uncomment to override library defaults):
        # prompts:
        #   process_block_file: "~/.elfmem/prompts/process_block.txt"
        #   contradiction_file: "~/.elfmem/prompts/contradiction.txt"
    """)
