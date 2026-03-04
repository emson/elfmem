# Title: Prompt Override Mechanism — Customising LLM Call Templates

## Status: complete

## Question

AMGS ships three prompt templates (`SELF_ALIGNMENT_PROMPT`, `SELF_TAG_PROMPT`,
`CONTRADICTION_PROMPT`) in `amgs/prompts.py`. These are sensible defaults for
general-purpose agents, but domain-specific deployments will need different
prompts. A medical assistant, a legal agent, or a code-only tool will each have
different calibration needs.

Currently, prompts are overridable only at `LiteLLMAdapter` construction time:

```python
LiteLLMAdapter(
    config=cfg.llm,
    alignment_prompt="...",   # keyword arg
    tag_prompt="...",
    contradiction_prompt="...",
)
```

But `MemorySystem.from_config()` — the primary entry point — ignores prompts
entirely:

```python
return cls(
    db_path=db_path,
    embedding_service=LiteLLMEmbeddingAdapter(cfg.embeddings),
    llm_service=LiteLLMAdapter(cfg.llm),   # ← no prompts
    config=cfg,
)
```

A user using `MemorySystem.from_config("~/memory.db", "amgs.yaml")` has no path
to override prompts. This is a gap.

How should prompt customisation be designed so that it works through the config
path, is type-safe, supports inline and file-based overrides, and has a clear
escape hatch for power users?

---

## The Three Levels of Override

Users have different needs. The design provides three levels, each more powerful
than the last:

| Level | Mechanism | Use Case |
|-------|-----------|----------|
| 1. Config inline | `prompts.self_alignment: "..."` in YAML | Minor wording adjustments, domain focus |
| 2. Config file path | `prompts.contradiction_file: "./my_prompt.txt"` | Long prompts, team-maintained |
| 3. Subclassing | `class MyLLM(LiteLLMAdapter)` | Different scoring logic, chain-of-thought, custom response models |

Levels 1 and 2 require no code changes — only config changes. Level 3 is the
escape hatch for users who need full programmatic control.

---

## PromptsConfig

A new `PromptsConfig` Pydantic model captures all prompt customisations:

```python
# amgs/config.py  (addition)

from pathlib import Path

class PromptsConfig(BaseModel):
    # Level 1: inline string override — None = use library default
    self_alignment: str | None = None
    self_tags: str | None = None
    contradiction: str | None = None

    # Level 2: file path override — None = not used
    # Resolved relative to the process working directory.
    # Inline takes priority over file if both are set.
    self_alignment_file: str | None = None
    self_tags_file: str | None = None
    contradiction_file: str | None = None

    # Tag vocabulary for infer_self_tags — None = use VALID_SELF_TAGS from prompts.py
    # Extend or replace the built-in tag set for domain-specific agents.
    valid_self_tags: list[str] | None = None

    def resolve_self_alignment(self) -> str:
        return self._resolve(
            self.self_alignment,
            self.self_alignment_file,
            _default_alignment,
        )

    def resolve_self_tags(self) -> str:
        return self._resolve(
            self.self_tags,
            self.self_tags_file,
            _default_tags,
        )

    def resolve_contradiction(self) -> str:
        return self._resolve(
            self.contradiction,
            self.contradiction_file,
            _default_contradiction,
        )

    def resolve_valid_tags(self) -> frozenset[str]:
        if self.valid_self_tags is not None:
            return frozenset(self.valid_self_tags)
        return _DEFAULT_VALID_SELF_TAGS

    @staticmethod
    def _resolve(inline: str | None, filepath: str | None, default_fn) -> str:
        if inline is not None:
            return inline
        if filepath is not None:
            return Path(filepath).read_text(encoding="utf-8")
        return default_fn()
```

The `_default_*` functions are thin wrappers around the constants in
`amgs/prompts.py`. They are callable (not strings) so that file I/O and
constant access both happen at adapter construction time, not at module import.

```python
# amgs/config.py (helpers)
from amgs.prompts import (
    SELF_ALIGNMENT_PROMPT, SELF_TAG_PROMPT, CONTRADICTION_PROMPT, VALID_SELF_TAGS
)

def _default_alignment() -> str:    return SELF_ALIGNMENT_PROMPT
def _default_tags() -> str:         return SELF_TAG_PROMPT
def _default_contradiction() -> str: return CONTRADICTION_PROMPT

_DEFAULT_VALID_SELF_TAGS = frozenset(VALID_SELF_TAGS)
```

---

## Updated AMGSConfig

`PromptsConfig` is added as a fourth top-level section:

```python
class AMGSConfig(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    prompts: PromptsConfig = Field(default_factory=PromptsConfig)  # new
```

No other change to `AMGSConfig` — the new section is entirely optional.
A config file without a `prompts` section is identical to one with
`prompts: {}`.

---

## Updated LiteLLMAdapter

`LiteLLMAdapter.__init__()` now accepts `PromptsConfig` instead of three
separate keyword arguments. This cleans up the constructor and makes the
prompts/tags relationship explicit:

```python
# amgs/adapters/litellm.py

class LiteLLMAdapter(LLMService):
    def __init__(
        self,
        config: LLMConfig,
        prompts: PromptsConfig | None = None,
    ):
        self.config = config
        p = prompts or PromptsConfig()

        # Resolve at construction time — file I/O happens once, at startup
        self.alignment_prompt = p.resolve_self_alignment()
        self.tag_prompt = p.resolve_self_tags()
        self.contradiction_prompt = p.resolve_contradiction()
        self.valid_self_tags = p.resolve_valid_tags()

        self.client = instructor.from_litellm(litellm.acompletion)
```

The `infer_self_tags` method now filters against the instance's `valid_self_tags`
rather than a module-level constant:

```python
    async def infer_self_tags(self, block: str, self_context: str) -> list[str]:
        result: SelfTagInference = await self.client.chat.completions.create(
            response_model=SelfTagInference,
            messages=[{"role": "user", "content":
                self.tag_prompt.format(self_context=self_context, block=block)
            }],
            max_retries=self.config.max_retries,
            **self._call_kwargs(self.config.tags_model),
        )
        # Filter against instance's valid tags (configurable, not module-level constant)
        return [t for t in result.tags if t in self.valid_self_tags]
```

---

## Updated SelfTagInference

The `@field_validator` that used the module-level `VALID_SELF_TAGS` is removed
from `SelfTagInference`. Filtering is now the adapter's responsibility — it has
access to the configured tag vocabulary.

```python
# amgs/adapters/models.py

class SelfTagInference(BaseModel):
    tags: list[str] = Field(
        default_factory=list,
        description="Self/* tags inferred by the LLM. Filtered by the adapter.",
    )
    # No validator here — adapter filters against PromptsConfig.valid_self_tags
```

This is the right boundary: the Pydantic model captures the LLM's raw output.
The adapter applies business rules (valid tag vocabulary) before returning.

---

## Updated MemorySystem.from_config()

The factory now passes `cfg.prompts` to `LiteLLMAdapter`:

```python
return cls(
    db_path=db_path,
    embedding_service=LiteLLMEmbeddingAdapter(cfg.embeddings),
    llm_service=LiteLLMAdapter(cfg.llm, cfg.prompts),   # prompts now wired
    config=cfg,
)
```

One line change. All prompt customisation in `amgs.yaml` is now respected.

---

## Per-Call-Type Model Overrides

Open question from exploration 025: should contradiction detection use a larger
model than alignment scoring? Contradiction false-positives are expensive
(valid knowledge gets suppressed). Alignment scoring can tolerate slight
imprecision.

This is resolved by adding optional per-call model overrides to `LLMConfig`:

```python
class LLMConfig(BaseModel):
    model: str = "gpt-4o-mini"          # default for all calls
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 30
    max_retries: int = 3
    base_url: str | None = None

    # Per-call overrides — None = use model above
    alignment_model: str | None = None   # typically cheaper/faster
    tags_model: str | None = None        # typically cheaper/faster
    contradiction_model: str | None = None  # typically more precise
```

`LiteLLMAdapter._call_kwargs()` becomes:

```python
def _call_kwargs(self, model_override: str | None = None) -> dict:
    kwargs = dict(
        model=model_override or self.config.model,
        temperature=self.config.temperature,
        max_tokens=self.config.max_tokens,
        timeout=self.config.timeout,
    )
    if self.config.base_url:
        kwargs["base_url"] = self.config.base_url
    return kwargs
```

Each LLM method passes its specific override:

```python
async def score_self_alignment(self, block, self_context) -> float:
    ...
    **self._call_kwargs(self.config.alignment_model),   # cheap model

async def infer_self_tags(self, block, self_context) -> list[str]:
    ...
    **self._call_kwargs(self.config.tags_model),         # cheap model

async def detect_contradiction(self, block_a, block_b) -> float:
    ...
    **self._call_kwargs(self.config.contradiction_model),  # precise model
```

Config example:

```yaml
llm:
  model: "gpt-4o-mini"              # default for alignment + tags
  contradiction_model: "gpt-4o"     # higher precision for contradiction check
  # alignment_model and tags_model omitted → use default
```

All three calls can use different models, different providers, or the same.
The base model is the fallback when no override is set.

---

## Template Variable Reference

Custom prompts MUST include the template variables their call type uses.
Missing variables will raise a `KeyError` at call time — not at config load time.

| Prompt | Required Variables | Optional Variables |
|--------|-------------------|-------------------|
| `self_alignment` | `{self_context}`, `{block}` | none |
| `self_tags` | `{self_context}`, `{block}` | none |
| `contradiction` | `{block_a}`, `{block_b}` | none |

**Variable semantics:**
- `{self_context}` — The rendered SELF frame text (the agent's current identity context). Always a string; may be multi-block markdown.
- `{block}` — The block content being evaluated. Markdown string without front matter.
- `{block_a}`, `{block_b}` — Two blocks being compared for contradiction. Markdown strings.

**Output requirements:**

Custom prompts must produce output that instructor can parse into the response model:
- `self_alignment` → `AlignmentScore`: requires `{"score": <float 0.0–1.0>}`
- `self_tags` → `SelfTagInference`: requires `{"tags": [<list of strings>]}`
- `contradiction` → `ContradictionScore`: requires `{"score": <float 0.0–1.0>}`

The response model format is fixed; the prompt text is free.

---

## YAML Config Examples

### Level 1: Inline Prompt Override

Override one prompt for a domain-specific agent. Other prompts remain default.

```yaml
# amgs.yaml

llm:
  model: "anthropic/claude-haiku-4-5-20251001"

prompts:
  # Only override self_alignment; other prompts use library defaults
  self_alignment: |
    You are evaluating whether a memory block is relevant to a medical AI assistant's identity.

    ## Assistant Identity
    {self_context}

    ## Memory Block
    {block}

    Score how much this block reflects the assistant's medical knowledge domain,
    clinical reasoning approach, or patient interaction style:
    - 0.0: General knowledge unrelated to medicine or this assistant
    - 0.3: Medical knowledge, but general (not identity-defining)
    - 0.7: Reflects how this assistant reasons clinically
    - 1.0: Core to this assistant's medical identity or constraints

    Respond: {"score": <float between 0.0 and 1.0>}
```

### Level 2: File-Based Prompt Override

Useful for long prompts, version-controlled separately, or maintained by a team.

```yaml
# amgs.yaml

llm:
  model: "gpt-4o-mini"

prompts:
  # Long prompts maintained as separate files
  self_alignment_file: "./prompts/medical_alignment.txt"
  contradiction_file: "./prompts/strict_contradiction.txt"
  # self_tags not specified → use library default
```

File content is plain text with the same `{variable}` syntax:

```
# ./prompts/medical_alignment.txt

You are evaluating alignment for a medical assistant agent.
...
{self_context}
...
{block}
...
{"score": <float>}
```

### Level 3: Custom Tag Vocabulary

Add domain-specific tags while retaining the built-in set.

```yaml
prompts:
  # The built-in tags PLUS domain extensions
  valid_self_tags:
    - "self/constitutional"
    - "self/constraint"
    - "self/value"
    - "self/style"
    - "self/goal"
    - "self/context"
    - "self/domain/oncology"     # custom: domain expertise
    - "self/domain/cardiology"   # custom: domain expertise
    - "self/regulatory/hipaa"    # custom: compliance constraints

  self_tags: |
    You are classifying a memory block against a medical AI's identity taxonomy.

    ## Agent Identity
    {self_context}

    ## Memory Block
    {block}

    ## Available Tags
    - self/constitutional: core invariants — never violated
    - self/constraint: firm rules — strong preferences
    - self/value: beliefs and principles
    - self/style: communication style
    - self/goal: active objectives
    - self/context: situational context
    - self/domain/oncology: expertise in oncology
    - self/domain/cardiology: expertise in cardiology
    - self/regulatory/hipaa: HIPAA compliance constraints

    Respond: {"tags": [<applicable tags>]}
```

The `valid_self_tags` list replaces (not augments) the default set. Include
all standard tags you want to retain plus any custom ones.

### Per-Call-Type Models

```yaml
llm:
  model: "gpt-4o-mini"          # fast/cheap for alignment and tags
  contradiction_model: "gpt-4o" # precise for contradiction (false positives are costly)
```

---

## Level 3: Subclassing LiteLLMAdapter

For maximum control — custom response models, chain-of-thought, multi-turn
prompting, entirely different scoring approaches:

```python
from amgs.adapters.litellm import LiteLLMAdapter
from amgs.adapters.models import AlignmentScore

class MedicalLLMAdapter(LiteLLMAdapter):
    """Custom adapter for a medical AI assistant."""

    async def score_self_alignment(self, block: str, self_context: str) -> float:
        # Chain-of-thought: first ask for reasoning, then the score
        reasoning_prompt = f"""
        Is this memory block about medical knowledge, clinical reasoning, or
        patient interaction? Think step-by-step.

        Block: {block}
        """
        reasoning_response = await self.client.chat.completions.create(
            response_model=None,   # free text for reasoning step
            messages=[{"role": "user", "content": reasoning_prompt}],
            **self._call_kwargs(),
        )
        reasoning = reasoning_response.choices[0].message.content

        score_prompt = f"""
        Context: {self_context}
        Block: {block}
        Reasoning: {reasoning}

        Given the above reasoning, rate alignment 0.0-1.0.
        JSON: {{"score": <float>}}
        """
        result: AlignmentScore = await self.client.chat.completions.create(
            response_model=AlignmentScore,
            messages=[{"role": "user", "content": score_prompt}],
            **self._call_kwargs(),
        )
        return result.score
```

Then wire it manually:

```python
from amgs.config import AMGSConfig

cfg = AMGSConfig.from_yaml("amgs.yaml")

system = MemorySystem(
    db_path="~/memory.db",
    embedding_service=LiteLLMEmbeddingAdapter(cfg.embeddings),
    llm_service=MedicalLLMAdapter(cfg.llm, cfg.prompts),
    config=cfg,
)
```

The subclassing approach overrides specific methods only. Non-overridden methods
(`infer_self_tags`, `detect_contradiction`) continue to use `LiteLLMAdapter`'s
implementation with the configured prompts.

---

## Prompt Validation

Custom prompts cannot be validated at YAML load time — the adapter doesn't know
the `self_context` or `block` values until a call is made. Validation happens
at call time via Python's string `format()`.

Two failure modes:

1. **Missing variable** (`KeyError`): Prompt uses `{context}` instead of
   `{self_context}`. Raises `KeyError` on first call.

2. **Missing response fields** (instructor retry): Prompt doesn't ask for the
   required JSON fields. instructor retries up to `max_retries` times, then
   raises `InstructorRetryException`.

Both fail loudly, on first use, with clear error messages. Silent corruption
is not possible — the adapter either returns a validated float/list or raises.

An optional validation method can be added to `PromptsConfig` for early
detection in tests or startup checks:

```python
class PromptsConfig(BaseModel):
    ...

    def validate_templates(self) -> None:
        """Raise ValueError if any resolved prompt is missing required variables."""
        _check("self_alignment", self.resolve_self_alignment(),
               required=("{self_context}", "{block}"))
        _check("self_tags", self.resolve_self_tags(),
               required=("{self_context}", "{block}"))
        _check("contradiction", self.resolve_contradiction(),
               required=("{block_a}", "{block_b}"))

def _check(name: str, prompt: str, required: tuple) -> None:
    for var in required:
        if var not in prompt:
            raise ValueError(
                f"Prompt '{name}' is missing required variable {var!r}. "
                f"All prompts must include: {required}"
            )
```

Calling `cfg.prompts.validate_templates()` at startup catches misconfigured
prompts before any LLM calls are made. This is optional but recommended in
production deployments.

---

## Module Changes

The prompt override design touches four files:

```
amgs/
├── config.py         ← add PromptsConfig, add to AMGSConfig, add per-call model fields to LLMConfig
├── prompts.py        ← no change (VALID_SELF_TAGS exported for import by config.py)
├── adapters/
│   ├── litellm.py    ← LiteLLMAdapter.__init__ accepts PromptsConfig; _call_kwargs takes model_override
│   └── models.py     ← remove @field_validator from SelfTagInference.tags
```

`prompts.py` exposes `VALID_SELF_TAGS` as a public constant (currently implied
but not exported). It becomes an explicit export so `config.py` can reference
`_DEFAULT_VALID_SELF_TAGS`.

---

## Locked Design Decisions

| Decision | Rationale |
|----------|-----------|
| `PromptsConfig` in `AMGSConfig` — prompts are config-level, not code-level | Prompt changes are deployment concerns, not development concerns |
| Inline override takes priority over file override | Inline is explicit; files are referenced; explicit beats implicit |
| File resolution at adapter construction time, not at YAML parse time | One file read per adapter instance; I/O not triggered at config import |
| `valid_self_tags` list replaces (not augments) default tags | Prevents silent tag accumulation; explicit opt-in for all tags in vocabulary |
| Tag filtering moves from `SelfTagInference` validator to adapter | Adapter has the configured vocabulary; Pydantic model captures raw LLM output |
| Per-call model overrides on `LLMConfig` (not `PromptsConfig`) | Model selection is an LLM config concern; prompts and models are orthogonal |
| Subclassing `LiteLLMAdapter` is the Level 3 escape hatch | Full method override; no framework-level hooks needed |
| `validate_templates()` is opt-in, not enforced at load time | Config loading is synchronous; I/O in validation is acceptable; startup check is optional |
| No hot-reload of prompts — resolved once at startup | Prompt changes require process restart; simplest, safest mental model |

---

## Open Questions

1. **Prompt in database?** Should custom prompt templates be stored in the
   `system_config` table rather than files? This would make them inspectable
   and versionable through the DB. Probably not for Phase 1 — config files are
   sufficient, and DB prompts create a different management surface.

2. **Prompt versioning**: If a prompt is updated, historical alignment scores
   (already stored in blocks) were computed with the old prompt. Should a prompt
   hash be stored alongside the score? Useful for detecting score drift, but
   adds schema complexity.

3. **Per-session prompt override**: Could an agent dynamically select a
   different alignment prompt at `begin_session()` time? For example, a
   medical session vs. an administrative session. This is a Level 4 extension
   not needed in Phase 1.

4. **Template engine**: The current `str.format()` approach has no loops,
   conditionals, or partials. If prompts grow in complexity, Jinja2 templates
   would be cleaner. Phase 1: `str.format()` is sufficient. Phase 2+: consider
   `jinja2` if prompts need conditional sections.
