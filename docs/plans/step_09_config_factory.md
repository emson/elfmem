# Step 9: Config + Factory — Implementation Plan

## Overview

Build the configuration system and factory entry point. This is the final step
that wires everything together into a one-line setup experience. After this step,
a user can create a fully configured `MemorySystem` from a YAML file, dict,
environment variables, or programmatically.

**Key design decisions (locked):**
- `ElfmemConfig` (renamed from `AMGSConfig`) is the single config source of truth
- Config accepts: None / str (YAML path) / dict / ElfmemConfig object
- API keys always from env vars — LiteLLM reads standard vars automatically
- `PromptsConfig` supports inline, file-based, and vocabulary overrides
- `MemorySystem.from_config()` is the primary user-facing factory
- Config validation happens at load time via Pydantic
- All tunables in one place: LLM, embedding, memory thresholds, prompts

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/config.py` | Create | ElfmemConfig, LLMConfig, EmbeddingConfig, MemoryConfig, PromptsConfig |
| `src/elfmem/api.py` | Modify | Add from_config() and from_env() factory class methods |

---

## Module Design

### 1. `src/elfmem/config.py`

**Purpose:** Pydantic configuration models for the entire elfmem system.
Validates all settings at load time. Supports YAML, dict, env var, and
programmatic construction.

**Imports:**
```python
from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
```

**Models:**

```python
class LLMConfig(BaseModel):
    """Configuration for the LLM service.

    API keys are NOT stored here — they come from environment variables.
    LiteLLM reads standard env vars automatically:
      OpenAI → OPENAI_API_KEY
      Anthropic → ANTHROPIC_API_KEY
      Groq → GROQ_API_KEY
    """
    model: str = "gpt-4o-mini"
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 30
    max_retries: int = 3
    base_url: str | None = None

    # Per-call model overrides — None = use model above
    alignment_model: str | None = None
    tags_model: str | None = None
    contradiction_model: str | None = None
```

```python
class EmbeddingConfig(BaseModel):
    """Configuration for the embedding service.

    WARNING: dimensions must match what is stored in the database.
    Changing the embedding model requires re-embedding all blocks.
    """
    model: str = "text-embedding-3-small"
    dimensions: int = 1536
    timeout: int = 30
    base_url: str | None = None
```

```python
class MemoryConfig(BaseModel):
    """Configuration for memory system thresholds and tunables.

    All values have sensible defaults for Phase 1 (50-500 blocks).
    """
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
```

```python
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
    self_alignment: str | None = None
    self_tags: str | None = None
    contradiction: str | None = None

    # Level 2: File path overrides — None = not used
    self_alignment_file: str | None = None
    self_tags_file: str | None = None
    contradiction_file: str | None = None

    # Tag vocabulary override — None = use VALID_SELF_TAGS from prompts.py
    valid_self_tags: list[str] | None = None

    def resolve_self_alignment(self) -> str:
        """Resolve the alignment prompt: inline > file > default."""
        return self._resolve(
            self.self_alignment,
            self.self_alignment_file,
            "self_alignment",
        )

    def resolve_self_tags(self) -> str:
        """Resolve the tag inference prompt: inline > file > default."""
        return self._resolve(
            self.self_tags,
            self.self_tags_file,
            "self_tags",
        )

    def resolve_contradiction(self) -> str:
        """Resolve the contradiction prompt: inline > file > default."""
        return self._resolve(
            self.contradiction,
            self.contradiction_file,
            "contradiction",
        )

    def resolve_valid_tags(self) -> frozenset[str]:
        """Resolve the valid tag vocabulary."""
        if self.valid_self_tags is not None:
            return frozenset(self.valid_self_tags)
        from elfmem.prompts import VALID_SELF_TAGS
        return VALID_SELF_TAGS

    def validate_templates(self) -> None:
        """Raise ValueError if any resolved prompt is missing required variables.

        Call at startup for early misconfiguration detection.
        """

    @staticmethod
    def _resolve(
        inline: str | None,
        filepath: str | None,
        prompt_name: str,
    ) -> str:
        """Resolve a prompt: inline > file > default constant."""
        if inline is not None:
            return inline
        if filepath is not None:
            return Path(filepath).read_text(encoding="utf-8")
        # Import defaults lazily to avoid circular imports
        from elfmem import prompts
        defaults = {
            "self_alignment": prompts.SELF_ALIGNMENT_PROMPT,
            "self_tags": prompts.SELF_TAG_PROMPT,
            "contradiction": prompts.CONTRADICTION_PROMPT,
        }
        return defaults[prompt_name]
```

```python
class ElfmemConfig(BaseModel):
    """Top-level configuration for the elfmem memory system.

    Combines all sub-configs. Supports construction from:
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
        """Load config from a YAML file.

        All sections are optional — missing sections use defaults.

        Args:
            path: Path to YAML config file.

        Returns:
            Validated ElfmemConfig.
        """
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data or {})

    @classmethod
    def from_env(cls) -> ElfmemConfig:
        """Load config from ELFMEM_ prefixed environment variables.

        Reads:
          ELFMEM_LLM_MODEL, ELFMEM_LLM_BASE_URL,
          ELFMEM_EMBEDDING_MODEL, ELFMEM_EMBEDDING_DIMENSIONS,
          ELFMEM_EMBEDDING_BASE_URL

        Falls back to defaults for unset variables.
        """
        data: dict = {"llm": {}, "embeddings": {}}
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
```

**Key implementation notes:**
- All config models use Pydantic v2 (`BaseModel`)
- `from_yaml` uses `yaml.safe_load` — no arbitrary code execution
- `from_env` reads `ELFMEM_` prefixed vars (not `AMGS_` — renamed per project naming)
- `PromptsConfig._resolve` defers imports to avoid circular dependencies
- `validate_templates()` checks that resolved prompts contain required `{variables}`
- All defaults are sensible for Phase 1 — no required fields

---

### 2. `src/elfmem/api.py` — Modifications

**Add factory class methods to MemorySystem:**

```python
@classmethod
async def from_config(
    cls,
    db_path: str,
    config: ElfmemConfig | str | dict | None = None,
) -> MemorySystem:
    """Create a MemorySystem from configuration.

    This is the primary entry point for users. Handles all wiring:
    database engine, LLM adapter, embedding adapter.

    Args:
        db_path: Path to SQLite database file (created if not exists).
        config: Configuration source:
            - None: reads ELFMEM_CONFIG env var for YAML path, or uses defaults
            - str: path to YAML config file
            - dict: configuration values (validated by Pydantic)
            - ElfmemConfig: pre-built config object

    Returns:
        Fully configured MemorySystem, ready for session().

    Example:
        system = await MemorySystem.from_config("agent.db")
        system = await MemorySystem.from_config("agent.db", "elfmem.yaml")
        system = await MemorySystem.from_config("agent.db", {
            "llm": {"model": "ollama/llama3.2", "base_url": "http://localhost:11434"}
        })
    """
```

**Implementation approach:**

```python
    if config is None:
        config_path = os.getenv("ELFMEM_CONFIG")
        cfg = ElfmemConfig.from_yaml(config_path) if config_path else ElfmemConfig()
    elif isinstance(config, str):
        cfg = ElfmemConfig.from_yaml(config)
    elif isinstance(config, dict):
        cfg = ElfmemConfig.model_validate(config)
    else:
        cfg = config

    # Optionally validate prompt templates early
    cfg.prompts.validate_templates()

    # Create engine
    engine = await create_engine(db_path)

    # Create adapters
    llm_svc = LiteLLMAdapter(
        model=cfg.llm.model,
        temperature=cfg.llm.temperature,
        max_tokens=cfg.llm.max_tokens,
        timeout=cfg.llm.timeout,
        max_retries=cfg.llm.max_retries,
        base_url=cfg.llm.base_url,
        alignment_model=cfg.llm.alignment_model,
        tags_model=cfg.llm.tags_model,
        contradiction_model=cfg.llm.contradiction_model,
        alignment_prompt=cfg.prompts.resolve_self_alignment(),
        tag_prompt=cfg.prompts.resolve_self_tags(),
        contradiction_prompt=cfg.prompts.resolve_contradiction(),
        valid_self_tags=cfg.prompts.resolve_valid_tags(),
    )

    embedding_svc = LiteLLMEmbeddingAdapter(
        model=cfg.embeddings.model,
        dimensions=cfg.embeddings.dimensions,
        timeout=cfg.embeddings.timeout,
        base_url=cfg.embeddings.base_url,
    )

    return cls(engine=engine, llm_service=llm_svc, embedding_service=embedding_svc)
```

**Additional factory method:**

```python
@classmethod
async def from_env(cls, db_path: str) -> MemorySystem:
    """Create a MemorySystem from ELFMEM_ environment variables.

    Convenience wrapper around from_config with env-based config.
    """
    cfg = ElfmemConfig.from_env()
    return await cls.from_config(db_path, cfg)
```

**Add imports to api.py:**
```python
import os
from elfmem.config import ElfmemConfig
from elfmem.adapters.litellm import LiteLLMAdapter, LiteLLMEmbeddingAdapter
```

---

## Key Invariants

1. **All config is validated at load time** — Pydantic rejects invalid values
   immediately; no silent corruption
2. **API keys never in config objects** — LiteLLM reads env vars automatically;
   ElfmemConfig has no key fields
3. **Defaults are always valid** — `ElfmemConfig()` with no args produces a
   valid, working config (assuming API keys in env)
4. **YAML safe_load only** — no arbitrary code execution from config files
5. **Prompt resolution is lazy** — file I/O happens at adapter construction,
   not at config import
6. **from_config is async** — engine creation is async; callers must await
7. **Config immutable after creation** — Pydantic models are not mutated
   after construction

## Security Considerations

1. **yaml.safe_load** — prevents arbitrary Python execution in YAML files
2. **No API keys in config** — keys MUST be in env vars; config files are
   safe to commit to source control
3. **File path resolution** — `PromptsConfig._resolve` reads files relative
   to cwd; no path traversal protection needed (config author controls paths)
4. **No credential logging** — config `__repr__` does not include secrets
   (there are none to include)

## Edge Cases

1. **Config file not found** — `from_yaml` raises `FileNotFoundError`
2. **Empty YAML file** — `yaml.safe_load` returns None; handled by `data or {}`
3. **Unknown config keys** — Pydantic v2 ignores extra fields by default;
   no error for forward-compatible config files
4. **Missing env vars** — defaults used; no error unless API call fails
5. **ELFMEM_CONFIG env var not set** — `from_config(None)` uses defaults
6. **Prompt file not found** — `Path(filepath).read_text()` raises
   `FileNotFoundError` at adapter construction time
7. **Invalid YAML syntax** — `yaml.safe_load` raises `yaml.YAMLError`
8. **Embedding dimensions mismatch** — config says 1536 but model returns
   768; detected at first embed() call, not at config load
9. **Multiple config sources** — from_config resolves exactly one source
   (None → env/defaults, str → YAML, dict → validate, object → use directly)

## Dependencies

- `pydantic` (already in pyproject.toml) — config validation
- `pyyaml` (already in pyproject.toml) — YAML config file parsing
- `elfmem.prompts` (Step 8) — default prompt templates
- `elfmem.adapters.litellm` (Step 8) — real adapter classes
- `elfmem.db.engine` (Step 3) — async engine creation
- `elfmem.api` (Steps 5-7) — MemorySystem class

## Done Criteria

1. `from elfmem.config import ElfmemConfig, LLMConfig, EmbeddingConfig, MemoryConfig, PromptsConfig` importable
2. `ElfmemConfig()` produces valid defaults (all fields populated)
3. `ElfmemConfig.from_yaml("test.yaml")` loads and validates YAML config
4. `ElfmemConfig.model_validate({"llm": {"model": "gpt-4o"}})` validates dict
5. `ElfmemConfig.from_env()` reads ELFMEM_ env vars
6. `PromptsConfig().resolve_self_alignment()` returns default prompt
7. `PromptsConfig(self_alignment="custom").resolve_self_alignment()` returns "custom"
8. `PromptsConfig(self_alignment_file="path").resolve_self_alignment()` reads file
9. `PromptsConfig().validate_templates()` passes with defaults
10. `PromptsConfig(self_alignment="no vars").validate_templates()` raises ValueError
11. Integration test (with API key): `system = await MemorySystem.from_config("test.db")`
    → system created, `async with system.session()` works
12. Integration test: End-to-end from exploration 023 worked example:
    ```python
    system = await MemorySystem.from_config("test.db", {"llm": {"model": "gpt-4o-mini"}})
    async with system.session():
        await system.learn("I prefer explicit error handling.")
        result = await system.frame("attention", query="error handling")
        assert result.text  # non-empty
    ```
13. `mypy --strict` passes on all new files
14. `ruff check` clean
