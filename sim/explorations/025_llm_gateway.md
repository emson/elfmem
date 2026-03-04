# Title: LLM Gateway — Configuration, Providers, and Structured Outputs

## Status: complete

## Question

AMGS makes LLM calls during `consolidate()` and `curate()`: three distinct
operations that need text-in / structured-data-out semantics. It also calls an
embedding service for every block ingested and every recall() with a query.

These external calls need to be:
- Configurable: choose any provider without changing library code
- Swappable: change from OpenAI to Anthropic (or local) in config only
- Reliable: structured outputs must not fail silently
- Testable: mock both services cleanly in tests
- Simple: the gateway itself should be thin — not a framework

How should this be designed?

---

## What AMGS Calls LLMs For

The three `LLMService` methods and their requirements:

| Call | When | Input → Output | Reliability requirement |
|------|------|----------------|------------------------|
| `score_self_alignment` | `consolidate()` per new block | block + self_context → float 0.0–1.0 | High — score affects decay profile |
| `infer_self_tags` | `consolidate()` if alignment ≥ 0.75 | block + self_context → list[str] | High — tags persist permanently |
| `detect_contradiction` | `consolidate()` per block pair | block_a + block_b → float 0.0–1.0 | High — false positives destroy valid knowledge |

All three calls:
- Are batched at consolidation time (not on the hot path)
- Need **structured, typed output** — a free-text response is not acceptable
- Use **temperature = 0** (deterministic; these are classification/scoring tasks)
- Need **small token counts** — inputs are short markdown blocks (~50–200 tokens each)
- Benefit from a **cheap, fast model** — a frontier model is unnecessary here

The one `EmbeddingService` method:

| Call | When | Input → Output | Notes |
|------|------|----------------|-------|
| `embed` | `consolidate()` per block, `recall()` per query | str → np.ndarray | Model must match across all blocks; stored in `embedding_model` column |

---

## Evaluating Gateway Approaches

### Approach A: Direct per-provider SDKs

Write one adapter class per provider: `OpenAILLMService`, `AnthropicLLMService`.
Each imports the official SDK for that provider.

```python
# adapters/openai_llm.py
from openai import AsyncOpenAI

class OpenAILLMService(LLMService):
    def __init__(self, model: str, api_key: str): ...
    async def score_self_alignment(self, block, self_context) -> float:
        response = await client.chat.completions.create(
            model=self.model, messages=[...], response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)["score"]
```

**Pros:** Official SDKs, minimal dependencies, no abstraction overhead.

**Cons:**
- One adapter per provider — N providers = N adapters = N maintenance burdens
- Each adapter reimplements the same retry logic, timeout handling, and JSON parsing
- Adding a new provider (Groq, Bedrock, local Ollama) means writing a new adapter
- Structured output extraction is duplicated across adapters

**When to use:** If you are certain you will only ever use one or two providers.

---

### Approach B: LiteLLM as unified backend

[LiteLLM](https://github.com/BerriAI/litellm) provides one API for 100+ LLM
providers. The model name encodes the provider — the calling code is identical
regardless of which provider you use.

```python
import litellm

# OpenAI
response = await litellm.acompletion(model="gpt-4o-mini", messages=[...])

# Anthropic
response = await litellm.acompletion(model="anthropic/claude-haiku-4-5-20251001", messages=[...])

# Local Ollama
response = await litellm.acompletion(model="ollama/llama3", messages=[...])

# Groq
response = await litellm.acompletion(model="groq/mixtral-8x7b-32768", messages=[...])
```

The calling code is identical. The model name is the only thing that changes.

LiteLLM also handles embeddings via the same convention:
```python
response = await litellm.aembedding(model="text-embedding-3-small", input=[text])
response = await litellm.aembedding(model="cohere/embed-english-v3.0", input=[text])
response = await litellm.aembedding(model="ollama/nomic-embed-text", input=[text])
```

**Pros:**
- One adapter for all providers — the gateway is genuinely thin
- Provider switching requires zero code change (model name in config only)
- Built-in retry logic, timeout handling, rate limit back-off
- Consistent response format across providers
- LiteLLM reads standard env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.)

**Cons:**
- LiteLLM is a moderately large dependency (~20MB installed)
- Occasional version lag when providers release new models
- Adds one abstraction layer over official SDKs

**When to use:** When provider flexibility is a requirement. This is the AMGS case.

---

### Approach C: Raw HTTP with httpx

Write all calls as direct HTTP requests against provider REST APIs.

**Pros:** No AI dependencies at all. Maximum control.

**Cons:** Rebuilds what LiteLLM already does well. Not worth it for a library.

---

### Decision: LiteLLM

LiteLLM is the right choice for AMGS. The gateway becomes:

```python
# adapters/litellm.py
import litellm
from amgs.ports import LLMService, EmbeddingService

class LiteLLMAdapter(LLMService):
    def __init__(self, model: str, **kwargs): ...
    async def score_self_alignment(self, block, self_context) -> float: ...
    async def infer_self_tags(self, block, self_context) -> list[str]: ...
    async def detect_contradiction(self, block_a, block_b) -> float: ...

class LiteLLMEmbeddingAdapter(EmbeddingService):
    def __init__(self, model: str, dimensions: int, **kwargs): ...
    async def embed(self, text: str) -> np.ndarray: ...
```

Two classes. All providers. The rest is config.

---

## Structured Outputs: The Critical Problem

The three LLMService calls must return typed data (`float`, `list[str]`).
Free-text responses are not acceptable — the system can't parse them reliably.

Three approaches to structured output:

### Option 1: JSON mode (provider-specific)

```python
response = await litellm.acompletion(
    model="gpt-4o-mini",
    messages=[{"role": "user", "content": prompt}],
    response_format={"type": "json_object"},
    temperature=0.0,
)
result = json.loads(response.choices[0].message.content)
score = float(result["score"])
```

**Problem:** JSON mode is OpenAI-specific. Anthropic uses a different mechanism.
Doesn't work with all LiteLLM providers. Requires manual parsing after extraction.

### Option 2: `instructor` library

[instructor](https://github.com/jxnl/instructor) wraps any LLM client and forces
Pydantic-validated structured output. It works with LiteLLM.

```python
import instructor
from litellm import AsyncOpenAI
from pydantic import BaseModel, Field

class AlignmentScore(BaseModel):
    score: float = Field(ge=0.0, le=1.0, description="Self-alignment score")

client = instructor.from_litellm(litellm.acompletion)

result: AlignmentScore = await client.chat.completions.create(
    model="gpt-4o-mini",
    response_model=AlignmentScore,
    messages=[{"role": "user", "content": prompt}],
    temperature=0.0,
)
return result.score   # guaranteed float in [0.0, 1.0]
```

instructor:
- Retries automatically when the LLM returns malformed output
- Validates via Pydantic — constraints are enforced (`ge=0.0, le=1.0`)
- Works with any LiteLLM-supported provider via tool use or JSON mode
- Returns the Pydantic model directly — no manual JSON parsing

**This is the right choice.** One dependency (`instructor`) solves structured
output across all providers.

### Option 3: Manual prompting + regex

Craft prompts that always produce parseable output. Parse with regex.

**Problem:** Brittle. LLMs occasionally fail to follow format instructions.
With hundreds of consolidations, occasional failures corrupt the graph.

---

### The Three Response Models

```python
# amgs/adapters/models.py — Pydantic models for structured LLM responses

from pydantic import BaseModel, Field

VALID_SELF_TAGS = {
    "self/constitutional", "self/constraint", "self/value",
    "self/style", "self/goal", "self/context",
}

class AlignmentScore(BaseModel):
    score: float = Field(
        ge=0.0, le=1.0,
        description="Self-alignment score: 0=unrelated, 1=core identity",
    )

class SelfTagInference(BaseModel):
    tags: list[str] = Field(
        default_factory=list,
        description="Applicable self/* tags from the defined taxonomy",
    )

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v):
        return [t for t in v if t in VALID_SELF_TAGS]

class ContradictionScore(BaseModel):
    score: float = Field(
        ge=0.0, le=1.0,
        description="Contradiction score: 0=compatible, 1=directly contradictory",
    )
```

Pydantic validates the data before the library ever uses it. Invalid scores
and unrecognised tag names are rejected automatically.

---

## Config Design

The config needs to be:
1. **Loadable** from a YAML file, environment variables, or Python code
2. **Flexible** — LLM and embedding can use different providers
3. **Safe** — API keys should come from env vars, not committed config files
4. **Complete** — all AMGS tunables in one place, not scattered

### The Config Schema

```python
# amgs/config.py

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings
import yaml, os

class LLMConfig(BaseModel):
    model: str = "gpt-4o-mini"          # LiteLLM model name encodes provider
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 30                    # seconds
    max_retries: int = 3
    # api_key intentionally excluded — use env vars (OPENAI_API_KEY etc.)
    # base_url for local/proxy endpoints:
    base_url: str | None = None          # e.g. "http://localhost:11434" for Ollama

class EmbeddingConfig(BaseModel):
    model: str = "text-embedding-3-small"
    dimensions: int = 1536              # MUST match what's stored in blocks table
    timeout: int = 30
    base_url: str | None = None

class MemoryConfig(BaseModel):
    # Lifecycle thresholds
    inbox_threshold: int = 10                   # consolidate when inbox reaches N
    curate_interval_hours: float = 40.0         # active hours between curates
    prune_threshold: float = 0.05               # decay_weight below this → archive
    search_window_hours: float = 200.0          # pre-filter time window
    vector_n_seeds_multiplier: int = 4          # N_seeds = top_k × this

    # Quality thresholds
    self_alignment_threshold: float = 0.70      # minimum to promote to SELF block
    contradiction_threshold: float = 0.80       # minimum to flag as contradiction
    near_dup_exact_threshold: float = 0.95      # reject (hash check already handles exact)
    near_dup_near_threshold: float = 0.90       # forget + create + inherit

    # Graph
    similarity_edge_threshold: float = 0.60    # minimum similarity to create edge
    edge_degree_cap: int = 10                   # max edges per block

    # Scoring
    top_k: int = 5                              # default blocks per frame

class AMGSConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)

    @classmethod
    def from_yaml(cls, path: str) -> "AMGSConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls.model_validate(data or {})

    @classmethod
    def from_env(cls) -> "AMGSConfig":
        """Load config from AMGS_ prefixed environment variables."""
        data = {
            "llm": {
                "model": os.getenv("AMGS_LLM_MODEL", "gpt-4o-mini"),
                "base_url": os.getenv("AMGS_LLM_BASE_URL"),
            },
            "embeddings": {
                "model": os.getenv("AMGS_EMBEDDING_MODEL", "text-embedding-3-small"),
                "dimensions": int(os.getenv("AMGS_EMBEDDING_DIMENSIONS", "1536")),
                "base_url": os.getenv("AMGS_EMBEDDING_BASE_URL"),
            },
        }
        return cls.model_validate(data)

    @classmethod
    def defaults(cls) -> "AMGSConfig":
        return cls()
```

### The Config File Format

```yaml
# amgs.yaml  ← committed to source control (no secrets)
llm:
  model: "gpt-4o-mini"
  max_tokens: 512
  temperature: 0.0

embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536

memory:
  inbox_threshold: 10
  curate_interval_hours: 40
  self_alignment_threshold: 0.70
  contradiction_threshold: 0.80
```

```bash
# .env  ← NOT committed to source control
OPENAI_API_KEY=sk-...
# or
ANTHROPIC_API_KEY=sk-ant-...
```

**The discipline:** Config files contain structure and thresholds.
API keys always come from environment variables. LiteLLM reads the
standard env var for each provider automatically — no config plumbing needed.

---

## Provider Examples

Switching providers is a one-line config change. No code changes.

```yaml
# Use OpenAI (default)
llm:
  model: "gpt-4o-mini"
# Set: OPENAI_API_KEY=sk-...

# Use Anthropic Claude Haiku
llm:
  model: "anthropic/claude-haiku-4-5-20251001"
# Set: ANTHROPIC_API_KEY=sk-ant-...

# Use Groq (fast, cheap, good for scoring calls)
llm:
  model: "groq/llama-3.1-8b-instant"
# Set: GROQ_API_KEY=...

# Use local Ollama (no API key, no cost)
llm:
  model: "ollama/llama3.2"
  base_url: "http://localhost:11434"
# No API key needed
```

```yaml
# OpenAI embeddings (default)
embeddings:
  model: "text-embedding-3-small"
  dimensions: 1536
# Set: OPENAI_API_KEY=sk-...

# Local Ollama embeddings (no cost)
embeddings:
  model: "ollama/nomic-embed-text"
  dimensions: 768
  base_url: "http://localhost:11434"

# Cohere embeddings
embeddings:
  model: "cohere/embed-english-v3.0"
  dimensions: 1024
# Set: COHERE_API_KEY=...
```

Note: `dimensions` must match what's already stored in the database. Changing
embedding models requires re-embedding all blocks. The `embedding_model` column
tracks which model was used for each block (per the schema from exploration 024).

---

## Prompt Templates

The quality of AMGS's self-alignment scores, tag inferences, and contradiction
detection depends entirely on prompt quality. Prompts should be configurable —
not buried in code.

```python
# amgs/prompts.py

SELF_ALIGNMENT_PROMPT = """\
You are evaluating whether a memory block expresses the identity of an agent.

## Agent Identity
{self_context}

## Memory Block
{block}

Rate how much this block expresses, reinforces, or reflects the agent's identity,
values, or self-concept on a scale from 0.0 to 1.0:
- 0.0: Unrelated — technical fact, external knowledge, no identity relevance
- 0.3: Adjacent — relevant to the agent's domain but not their identity
- 0.7: Identity-adjacent — reflects how the agent thinks or works
- 1.0: Core identity — directly states a value, constraint, or self-defining belief

Respond with JSON: {{"score": <float between 0.0 and 1.0>}}
"""

SELF_TAG_PROMPT = """\
You are classifying a memory block against an agent's identity taxonomy.

## Agent Identity
{self_context}

## Memory Block
{block}

## Available Tags
- self/constitutional: core invariants — never violated, fundamental to existence
- self/constraint: strong rules — rarely violated, firm preferences
- self/value: beliefs and principles that consistently guide behavior
- self/style: communication style, tone, and interaction preferences
- self/goal: active goals or objectives the agent is pursuing
- self/context: situational context about who the agent is or what they know

Which tags apply? A block may have 0, 1, or multiple tags.
Only assign a tag if you are confident it applies. Prefer no tags over guessing.

Respond with JSON: {{"tags": [<list of applicable tag strings>]}}
"""

CONTRADICTION_PROMPT = """\
You are detecting logical contradictions between two memory blocks.

## Block A
{block_a}

## Block B
{block_b}

Rate how contradictory these blocks are:
- 0.0: Compatible — can both be true simultaneously
- 0.3: Tension — different emphases or perspectives, not directly contradictory
- 0.7: Conflicting — one implies the other is wrong or outdated
- 1.0: Direct contradiction — both cannot be true at the same time

Focus on logical contradiction, not just difference of opinion or emphasis.
Technical corrections (Block B updates/supersedes Block A) score high (0.7+).

Respond with JSON: {{"score": <float between 0.0 and 1.0>}}
"""
```

Prompts are importable constants. Downstream users can override them by
passing custom prompt strings to the adapter at construction time. This is
useful for domain-specific agents where the classification criteria differ.

---

## The Adapter Implementation

```python
# amgs/adapters/litellm.py

import instructor
import litellm
import numpy as np
from amgs.ports import LLMService, EmbeddingService
from amgs.config import LLMConfig, EmbeddingConfig
from amgs.adapters.models import AlignmentScore, SelfTagInference, ContradictionScore
from amgs.prompts import (
    SELF_ALIGNMENT_PROMPT, SELF_TAG_PROMPT, CONTRADICTION_PROMPT
)

class LiteLLMAdapter(LLMService):
    """LLM service backed by any LiteLLM-supported provider.

    API keys are read from environment variables by LiteLLM automatically:
      OpenAI → OPENAI_API_KEY
      Anthropic → ANTHROPIC_API_KEY
      Groq → GROQ_API_KEY
      Ollama → no key needed (base_url required)
    """

    def __init__(
        self,
        config: LLMConfig,
        alignment_prompt: str = SELF_ALIGNMENT_PROMPT,
        tag_prompt: str = SELF_TAG_PROMPT,
        contradiction_prompt: str = CONTRADICTION_PROMPT,
    ):
        self.config = config
        self.alignment_prompt = alignment_prompt
        self.tag_prompt = tag_prompt
        self.contradiction_prompt = contradiction_prompt
        self.client = instructor.from_litellm(litellm.acompletion)

    def _call_kwargs(self) -> dict:
        kwargs = dict(
            model=self.config.model,
            temperature=self.config.temperature,
            max_tokens=self.config.max_tokens,
            timeout=self.config.timeout,
        )
        if self.config.base_url:
            kwargs["base_url"] = self.config.base_url
        return kwargs

    async def score_self_alignment(self, block: str, self_context: str) -> float:
        result: AlignmentScore = await self.client.chat.completions.create(
            response_model=AlignmentScore,
            messages=[{"role": "user", "content":
                self.alignment_prompt.format(
                    self_context=self_context, block=block
                )
            }],
            max_retries=self.config.max_retries,
            **self._call_kwargs(),
        )
        return result.score

    async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
        result: SelfTagInference = await self.client.chat.completions.create(
            response_model=SelfTagInference,
            messages=[{"role": "user", "content":
                self.tag_prompt.format(
                    self_context=self_context, block=block
                )
            }],
            max_retries=self.config.max_retries,
            **self._call_kwargs(),
        )
        return result.tags

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        result: ContradictionScore = await self.client.chat.completions.create(
            response_model=ContradictionScore,
            messages=[{"role": "user", "content":
                self.contradiction_prompt.format(
                    block_a=block_a, block_b=block_b
                )
            }],
            max_retries=self.config.max_retries,
            **self._call_kwargs(),
        )
        return result.score


class LiteLLMEmbeddingAdapter(EmbeddingService):
    """Embedding service backed by any LiteLLM-supported embedding provider."""

    def __init__(self, config: EmbeddingConfig):
        self.config = config

    async def embed(self, text: str) -> np.ndarray:
        kwargs = dict(
            model=self.config.model,
            input=[text],
            timeout=self.config.timeout,
        )
        if self.config.base_url:
            kwargs["api_base"] = self.config.base_url
        response = await litellm.aembedding(**kwargs)
        return np.array(response.data[0]["embedding"], dtype=np.float32)
```

---

## Factory: `MemorySystem.from_config()`

The user should never need to wire adapters manually for standard usage.
A factory method handles the wiring:

```python
# amgs/api.py

class MemorySystem:
    def __init__(
        self,
        db_path: str,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
        config: AMGSConfig | None = None,
    ): ...

    @classmethod
    def from_config(
        cls,
        db_path: str,
        config: AMGSConfig | str | dict | None = None,
    ) -> "MemorySystem":
        """Create a MemorySystem from config.

        config can be:
          - None → load from AMGS_CONFIG env var, or use defaults
          - str → path to a YAML file
          - dict → config values
          - AMGSConfig → direct config object
        """
        if config is None:
            config_path = os.getenv("AMGS_CONFIG")
            cfg = AMGSConfig.from_yaml(config_path) if config_path else AMGSConfig.defaults()
        elif isinstance(config, str):
            cfg = AMGSConfig.from_yaml(config)
        elif isinstance(config, dict):
            cfg = AMGSConfig.model_validate(config)
        else:
            cfg = config

        return cls(
            db_path=db_path,
            embedding_service=LiteLLMEmbeddingAdapter(cfg.embeddings),
            llm_service=LiteLLMAdapter(cfg.llm),
            config=cfg,
        )

    @classmethod
    def from_env(cls, db_path: str) -> "MemorySystem":
        """Create a MemorySystem from AMGS_ environment variables."""
        cfg = AMGSConfig.from_env()
        return cls.from_config(db_path, cfg)
```

**Usage patterns:**

```python
# Simplest: reads from AMGS_CONFIG env var, falls back to defaults
system = MemorySystem.from_config("~/memory.db")

# Explicit config file
system = MemorySystem.from_config("~/memory.db", "amgs.yaml")

# Dict (useful for testing or dynamic config)
system = MemorySystem.from_config("~/memory.db", {
    "llm": {"model": "ollama/llama3.2", "base_url": "http://localhost:11434"},
    "embeddings": {"model": "ollama/nomic-embed-text", "dimensions": 768,
                   "base_url": "http://localhost:11434"},
})

# Fully programmatic (power user or custom adapters)
system = MemorySystem(
    db_path="~/memory.db",
    embedding_service=MyCustomEmbedder(),
    llm_service=MyCustomLLM(),
)
```

---

## Mock Services for Testing

Testing with real LLM calls is slow and expensive. The protocol design
ensures that mocks are trivial:

```python
# tests/fixtures.py

from amgs.ports import LLMService, EmbeddingService
import numpy as np

class MockLLMService(LLMService):
    """Returns deterministic values for all LLM calls."""

    async def score_self_alignment(self, block, self_context) -> float:
        # Return a fixed score, or compute from block length, or use fixtures
        return 0.65

    async def infer_self_tags(self, block, self_context) -> list[str]:
        if "prefer" in block.lower() or "value" in block.lower():
            return ["self/value"]
        return []

    async def detect_contradiction(self, block_a, block_b) -> float:
        # Simple heuristic: if blocks share no words, low contradiction
        words_a = set(block_a.lower().split())
        words_b = set(block_b.lower().split())
        return 0.0 if not words_a & words_b else 0.3


class MockEmbeddingService(EmbeddingService):
    """Returns random but reproducible embeddings."""

    def __init__(self, dimensions: int = 1536, seed: int = 42):
        self.dimensions = dimensions
        self._rng = np.random.default_rng(seed)

    async def embed(self, text: str) -> np.ndarray:
        # Seed from text hash so same text → same embedding
        rng = np.random.default_rng(hash(text) % (2**32))
        vec = rng.random(self.dimensions).astype(np.float32)
        return vec / np.linalg.norm(vec)  # normalise

# Test setup:
system = MemorySystem(
    db_path=":memory:",
    embedding_service=MockEmbeddingService(),
    llm_service=MockLLMService(),
)
```

No network calls in tests. The mock embedding service produces consistent
vectors from text hashes — the same text always gets the same embedding,
so similarity tests are deterministic.

---

## Module Additions

The refined module layout from exploration 024 gains three new files:

```
amgs/
├── config.py                  # AMGSConfig, LLMConfig, EmbeddingConfig, MemoryConfig
├── prompts.py                 # SELF_ALIGNMENT_PROMPT, SELF_TAG_PROMPT, CONTRADICTION_PROMPT
├── adapters/
│   ├── litellm.py             # LiteLLMAdapter + LiteLLMEmbeddingAdapter
│   └── models.py              # AlignmentScore, SelfTagInference, ContradictionScore (Pydantic)
```

`adapters/models.py` was previously implied but never defined. The Pydantic
models live here, not in `types.py` — they are adapter-specific concerns,
not library data types.

---

## Dependencies

```toml
# pyproject.toml
[project]
dependencies = [
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "numpy>=1.26",
    "litellm>=1.40",
    "instructor>=1.3",
    "pyyaml>=6.0",
]
```

**Six production dependencies.** Each has a clear, non-overlapping role:

| Package | Role |
|---------|------|
| `sqlalchemy` | L1 database queries |
| `alembic` | L1 schema migrations |
| `pydantic` + `pydantic-settings` | Config validation and settings |
| `numpy` | Embedding vectors and cosine similarity |
| `litellm` | Unified LLM + embedding API |
| `instructor` | Structured output extraction |
| `pyyaml` | Config file parsing |

---

## Locked Design Decisions

| Decision | Rationale |
|----------|-----------|
| LiteLLM as the unified LLM + embedding backend | One dependency handles 100+ providers; provider switch = model name in config |
| `instructor` for structured outputs | Pydantic-validated responses; auto-retry on malformed output; provider-agnostic |
| API keys from env vars only; never in config files | Standard practice; prevents credential leaks; LiteLLM reads them automatically |
| `AMGSConfig` Pydantic model as single config source of truth | Validated at load time; discoverable schema; importable in tests |
| YAML config file for structure; env vars for secrets | Clear separation: what the app does (YAML) vs credentials (env) |
| `AMGS_CONFIG` env var as config file pointer | Allows container/CI override without code change |
| Prompts in `amgs/prompts.py` as named string constants | Visible, reviewable, overridable; not buried in adapter code |
| `LLMConfig.base_url` for local model support (Ollama) | Zero-cost local development and testing without API keys |
| `MemorySystem.from_config()` factory with flexible input type | Accepts path, dict, object, or None; AMGS_CONFIG env var as implicit fallback |
| Mock services use text-hash seeding for deterministic embeddings | Same text → same embedding → deterministic similarity tests |

---

## Open Questions

1. **Batching**: `consolidate()` processes N inbox blocks. Each block makes up
   to 3 LLM calls (alignment, tags, contradiction check). Should the adapter
   support batch calls? LiteLLM's batch API can reduce latency. For Phase 1
   (small inbox), serial calls are fine.

2. **Cost tracking**: LiteLLM returns token counts per call. Should AMGS log
   these to `system_config` or a separate `llm_costs` table? Useful for
   understanding running costs, especially during heavy consolidation.

3. **Model selection per call type**: A large model for contradiction detection
   (where false positives are expensive) and a small model for alignment scoring
   (where speed matters more) might be the right balance. Should the config
   support per-method model overrides?

4. **Prompt versioning**: If a prompt template is updated, historical scores
   (already stored in blocks) were computed with the old prompt. Does this
   matter? Probably not for Phase 1, but worth noting.

5. **Async boundary**: `consolidate()` and `curate()` are async (they call
   `LLMService` and `EmbeddingService`). This means the entire L4 API should
   be async. Should `frame()` be async too (since `embed(query)` is async)?
   Likely yes — consistently async is simpler than a mixed sync/async API.
