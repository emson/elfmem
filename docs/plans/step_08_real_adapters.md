# Step 8: Real LLM Adapters — Implementation Plan

## Overview

Build the real (non-mock) adapters for `LLMService` and `EmbeddingService`
using LiteLLM and instructor. These are thin wrappers — all business logic is
already verified with mocks from Step 4. The adapters convert elfmem's port
protocols into actual provider API calls.

This step also creates the Pydantic response models, prompt templates, and
the prompt override mechanism.

**Key design decisions (locked):**
- LiteLLM as unified LLM + embedding backend (100+ providers)
- instructor for structured outputs with Pydantic validation
- API keys from environment variables only — never in config files
- Per-call model overrides (alignment, tags, contradiction can use different models)
- Prompt templates as importable constants in `prompts.py` — overridable via config
- PromptsConfig supports inline, file-based, and subclassing overrides
- Tag filtering in adapter (not in Pydantic model) — adapter has the configured vocabulary
- temperature=0.0 for all LLM calls (deterministic scoring/classification)

---

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/elfmem/prompts.py` | Create | Prompt template constants + VALID_SELF_TAGS |
| `src/elfmem/adapters/models.py` | Create | Pydantic response models for structured LLM output |
| `src/elfmem/adapters/litellm.py` | Create | LiteLLMAdapter + LiteLLMEmbeddingAdapter |

---

## Module Design

### 1. `src/elfmem/prompts.py`

**Purpose:** Default prompt templates for the three LLM calls and the valid
self-tag vocabulary. These are importable constants — the adapter uses them
as defaults when no overrides are configured.

**Constants:**

```python
from __future__ import annotations

VALID_SELF_TAGS: frozenset[str] = frozenset({
    "self/constitutional",
    "self/constraint",
    "self/value",
    "self/style",
    "self/goal",
    "self/context",
})

SELF_ALIGNMENT_PROMPT: str = """\
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

SELF_TAG_PROMPT: str = """\
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

CONTRADICTION_PROMPT: str = """\
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

**Key implementation notes:**
- Use double braces `{{` / `}}` in prompts for literal JSON braces (Python format escaping)
- `VALID_SELF_TAGS` is a `frozenset` — immutable, hashable, importable
- Prompts use `{self_context}`, `{block}`, `{block_a}`, `{block_b}` as template variables
- These are string constants, not Jinja2 templates — Phase 1 uses `str.format()`

---

### 2. `src/elfmem/adapters/models.py`

**Purpose:** Pydantic models for structured LLM responses. Used by instructor
to validate and extract data from LLM output.

**Imports:**
```python
from __future__ import annotations

from pydantic import BaseModel, Field
```

**Models:**

```python
class AlignmentScore(BaseModel):
    """Structured response for self-alignment scoring."""
    score: float = Field(
        ge=0.0, le=1.0,
        description="Self-alignment score: 0=unrelated, 1=core identity",
    )


class SelfTagInference(BaseModel):
    """Structured response for self-tag inference.

    Tags are the raw LLM output. Filtering against the valid tag vocabulary
    is the adapter's responsibility, not this model's.
    """
    tags: list[str] = Field(
        default_factory=list,
        description="Self/* tags inferred by the LLM.",
    )


class ContradictionScore(BaseModel):
    """Structured response for contradiction detection."""
    score: float = Field(
        ge=0.0, le=1.0,
        description="Contradiction score: 0=compatible, 1=directly contradictory",
    )
```

**Key implementation notes:**
- `AlignmentScore` and `ContradictionScore` use `ge=0.0, le=1.0` constraints —
  Pydantic rejects out-of-range values before the adapter sees them
- `SelfTagInference` does NOT validate tags against the vocabulary — the adapter
  filters tags using the configured `valid_self_tags` set (which may be customised)
- These models are adapter-specific concerns, not core domain types — they live
  in `adapters/`, not in `types.py`

---

### 3. `src/elfmem/adapters/litellm.py`

**Purpose:** Real LLM and embedding service implementations backed by LiteLLM.
Two classes: `LiteLLMAdapter` (LLMService) and `LiteLLMEmbeddingAdapter` (EmbeddingService).

**Imports:**
```python
from __future__ import annotations

import instructor
import litellm
import numpy as np

from elfmem.adapters.models import AlignmentScore, ContradictionScore, SelfTagInference
from elfmem.ports.services import EmbeddingService, LLMService
from elfmem.prompts import (
    CONTRADICTION_PROMPT,
    SELF_ALIGNMENT_PROMPT,
    SELF_TAG_PROMPT,
    VALID_SELF_TAGS,
)
```

**Class: `LiteLLMAdapter`**

```python
class LiteLLMAdapter:
    """LLM service backed by any LiteLLM-supported provider.

    API keys are read from environment variables by LiteLLM automatically:
      OpenAI → OPENAI_API_KEY
      Anthropic → ANTHROPIC_API_KEY
      Groq → GROQ_API_KEY
      Ollama → no key needed (base_url required)

    Args:
        model: LiteLLM model name (e.g. "gpt-4o-mini", "anthropic/claude-haiku-4-5-20251001").
        temperature: Sampling temperature. Default 0.0 (deterministic).
        max_tokens: Maximum response tokens. Default 512.
        timeout: Request timeout in seconds. Default 30.
        max_retries: instructor retry count on malformed output. Default 3.
        base_url: Optional base URL for local/proxy endpoints (e.g. Ollama).
        alignment_model: Optional per-call model override for alignment scoring.
        tags_model: Optional per-call model override for tag inference.
        contradiction_model: Optional per-call model override for contradiction detection.
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
```

**Key implementation notes for `LiteLLMAdapter`:**

- Store all config as instance attributes
- Create instructor client: `self._client = instructor.from_litellm(litellm.acompletion)`
- Resolve prompts at construction time (default to constants from `prompts.py`)
- Resolve valid tags at construction time (default to `VALID_SELF_TAGS`)
- `_call_kwargs(model_override)` returns dict with model, temperature, max_tokens, timeout, base_url
- Each method formats the prompt with template variables, calls instructor, returns result

**Method signatures:**

```python
async def score_self_alignment(self, block: str, self_context: str) -> float:
    """Score how much a block reflects the agent's identity.

    Formats the alignment prompt, calls the LLM via instructor,
    returns the validated score.
    """


async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
    """Infer self/* tags for a block.

    Formats the tag prompt, calls the LLM via instructor,
    filters returned tags against the valid vocabulary,
    returns the filtered tag list.
    """


async def detect_contradiction(self, block_a: str, block_b: str) -> float:
    """Score how contradictory two blocks are.

    Formats the contradiction prompt, calls the LLM via instructor,
    returns the validated score.
    """
```

**Helper method:**

```python
def _call_kwargs(self, model_override: str | None = None) -> dict:
    """Build kwargs dict for instructor/litellm call.

    Uses model_override if provided, otherwise the default model.
    Includes temperature, max_tokens, timeout, and base_url.
    """
```

---

**Class: `LiteLLMEmbeddingAdapter`**

```python
class LiteLLMEmbeddingAdapter:
    """Embedding service backed by any LiteLLM-supported embedding provider.

    Args:
        model: LiteLLM embedding model name (e.g. "text-embedding-3-small").
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
```

**Method signature:**

```python
async def embed(self, text: str) -> np.ndarray:
    """Embed text via the configured LiteLLM provider.

    Calls litellm.aembedding(), extracts the embedding vector,
    converts to float32 numpy array, normalises to unit vector.

    Returns:
        Normalised float32 ndarray of shape (dimensions,).
    """
```

**Key implementation notes for `LiteLLMEmbeddingAdapter`:**
- Call `litellm.aembedding(model=..., input=[text], timeout=..., api_base=...)`
- Extract `response.data[0]["embedding"]`
- Convert to `np.array(..., dtype=np.float32)`
- L2-normalise: `vec / np.linalg.norm(vec)`
- The `dimensions` parameter is for documentation/validation — the actual
  dimensions come from the model. If the returned vector has different dimensions,
  that's a configuration error

---

## Key Invariants

1. **Protocol compliance** — `isinstance(LiteLLMAdapter(), LLMService)` is True;
   same for `LiteLLMEmbeddingAdapter` / `EmbeddingService`
2. **Scores in [0.0, 1.0]** — Pydantic `ge=0.0, le=1.0` enforced by AlignmentScore
   and ContradictionScore
3. **Tag filtering** — `infer_self_tags` returns only tags in the configured
   `valid_self_tags` set; invalid tags from LLM are silently dropped
4. **temperature=0.0** — deterministic responses for scoring/classification
5. **No secrets in code** — API keys come from env vars via LiteLLM's built-in
   mechanism; adapter never reads `os.environ` directly for keys
6. **instructor retry** — malformed LLM output retried up to `max_retries` times;
   raises `InstructorRetryException` on persistent failure
7. **Unit-normalised embeddings** — all vectors L2-normalised before return
8. **Prompt resolution at construction** — file I/O and default resolution happen
   once in `__init__`, not on every call

## Security Considerations

1. **No API keys in source** — all credentials via environment variables
2. **Pydantic validation** — LLM outputs validated before use; no injection possible
3. **prompt template variables** — use Python `str.format()` with known keys only;
   no user-controlled template injection
4. **No credential logging** — adapter never logs API keys or response payloads

## Edge Cases

1. **Empty self_context** — prompts work with empty identity context; alignment
   scores will be low (which is correct)
2. **Empty block** — hash of empty string is valid; LLM may return low scores
3. **LLM returns no tags** — `SelfTagInference(tags=[])` is valid; empty list returned
4. **LLM returns invalid tags** — filtered out by adapter; empty list returned
5. **LLM timeout** — raises `litellm.Timeout` after configured seconds
6. **Invalid API key** — raises `litellm.AuthenticationError` on first call
7. **Provider not available** — raises `litellm.APIConnectionError`
8. **Embedding dimensions mismatch** — vector returned has different dimensions
   than configured; should log warning (Phase 1: no hard validation)

## Dependencies

- `litellm` (already in pyproject.toml) — unified LLM + embedding API
- `instructor` (already in pyproject.toml) — structured output extraction
- `numpy` (already in pyproject.toml) — embedding vectors
- `pydantic` (already in pyproject.toml) — response model validation
- `elfmem.ports.services` (Step 1) — Protocol definitions
- `elfmem.prompts` (this step) — prompt templates

## Done Criteria

1. `from elfmem.adapters.litellm import LiteLLMAdapter, LiteLLMEmbeddingAdapter` importable
2. `from elfmem.adapters.models import AlignmentScore, SelfTagInference, ContradictionScore` importable
3. `from elfmem.prompts import SELF_ALIGNMENT_PROMPT, SELF_TAG_PROMPT, CONTRADICTION_PROMPT, VALID_SELF_TAGS` importable
4. `isinstance(LiteLLMAdapter(), LLMService)` is True
5. `isinstance(LiteLLMEmbeddingAdapter(), EmbeddingService)` is True
6. `AlignmentScore(score=0.5)` validates; `AlignmentScore(score=1.5)` raises
7. `ContradictionScore(score=-0.1)` raises validation error
8. `SelfTagInference(tags=["self/value", "invalid_tag"])` does NOT raise
   (filtering is adapter's job)
9. Integration test (requires real API key): `LiteLLMAdapter().score_self_alignment("test", "ctx")`
   returns a float in [0.0, 1.0]
10. Integration test: `LiteLLMEmbeddingAdapter().embed("test")` returns a float32
    ndarray of expected dimensions with L2 norm ≈ 1.0
11. Prompt template variables resolve correctly: `SELF_ALIGNMENT_PROMPT.format(self_context="...", block="...")` succeeds
12. Per-call model override: `LiteLLMAdapter(model="gpt-4o-mini", contradiction_model="gpt-4o")` — contradiction uses gpt-4o
13. Custom prompt: `LiteLLMAdapter(alignment_prompt="custom...")` — uses custom prompt
14. `mypy --strict` passes on all new files
15. `ruff check` clean
