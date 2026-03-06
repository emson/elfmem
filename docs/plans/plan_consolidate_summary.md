# Plan: Combined Consolidation with Summary

**Status:** Design v1 — implementation-ready

---

## 1. Design Intent

Consolidation currently makes **2 separate LLM calls per inbox block**:

1. `score_self_alignment(content, self_context)` → `float`
2. `infer_self_tags(content, self_context)` → `list[str]`

Both calls see the same content and self_context. Both are processed by the same
LLM. They share the same context window setup cost but return disjoint outputs.
This is wasteful and misses an opportunity: neither call produces a **summary** —
a normalised, distilled version of the raw content that embeds better and renders
more consistently.

**This plan replaces 2 LLM calls with 1 structured output call** that returns all
three outputs: `alignment_score`, `tags`, and `summary`.

### Benefits

| Dimension | Before | After |
|-----------|--------|-------|
| LLM calls per block | 2 | **1** |
| Cost per block | ~2× base | **~1× base** (single call) |
| Embedding quality | Raw user text (noisy, inconsistent) | **Summary (normalised, consistent)** |
| Rendering quality | Raw user text (variable) | **Summary (clean, structured)** |
| Provenance | Content stored | **Content preserved; summary added** |

### What stays the same

- `detect_contradiction()` remains a **separate** LLM call — it operates on
  block pairs, not single blocks. Fundamentally different operation.
- `EmbeddingService` protocol is unchanged.
- `ScoredBlock`, rendering, and format helpers require **zero changes** — summary
  resolution happens at retrieval time (see §3).
- No legacy data migration — user confirmed "no legacy knowledge, create fresh code."

---

## 2. Architecture: Before and After

### Before (2 calls)

```
inbox block
    │
    ├─→ llm.score_self_alignment(content, ctx) → float      ← call 1
    ├─→ llm.infer_self_tags(content, ctx)      → list[str]  ← call 2
    ├─→ embedding_svc.embed(content)            → ndarray
    │
    └─→ update_block_scoring(confidence, alignment, decay_lambda, embedding)
```

### After (1 call)

```
inbox block
    │
    ├─→ llm.process_block(content, ctx) → BlockAnalysis     ← single call
    │       ├── alignment_score: float
    │       ├── tags: list[str]
    │       └── summary: str
    │
    ├─→ embedding_svc.embed(summary)    → ndarray           ← embed summary, not content
    │
    └─→ update_block_scoring(confidence, alignment, decay_lambda, embedding, summary)
```

### Data flow at retrieval time

```
DB: blocks.content  = raw user input (provenance)
DB: blocks.summary  = LLM-generated distilled text

retrieval.py builds ScoredBlock:
    content = block["summary"] or block["content"]   ← summary preferred

rendering.py uses block.content → unchanged
format_recall_response uses block.content → unchanged
```

---

## 3. Key Design Decisions

### 3.1 Summary replaces content at retrieval — not at storage

**Decision:** Raw `content` is always preserved in the database. The `summary`
column stores the LLM-generated normalised version. At retrieval time,
`ScoredBlock.content` is populated with `summary or content`.

**Why:** Provenance. The user's original text is the audit trail. If the LLM
hallucinated or over-summarised, the raw content is always recoverable. The
summary is a computed view, like an index — derived from content, not a
replacement.

**Impact:** Zero changes to `ScoredBlock`, `rendering.py`, `_format_block()`,
or `format_recall_response()`. The summary-vs-content resolution is a one-line
change in `retrieval.py` line 213.

### 3.2 Embed the summary, not the raw content

**Decision:** `embedding_svc.embed(summary)` replaces `embedding_svc.embed(content)`.

**Why:** Raw user input varies wildly: some blocks are telegraphic notes, others
are verbose narratives, some mix formatting with facts. Summaries are normalised
to a consistent factual style — better for cosine similarity. When a user asks
"what are my preferences?", the summary "User prefers dark mode and monospace
fonts" embeds closer to that query than the raw input "oh btw I really like dark
mode, also please use monospace everywhere."

**Fallback:** If the LLM returns an empty summary (shouldn't happen with a well-
crafted prompt, but defensive), embed the raw content.

### 3.3 Clean protocol break — remove old methods, add new one

**Decision:** Remove `score_self_alignment()` and `infer_self_tags()` from the
`LLMService` protocol. Add `process_block()` returning `BlockAnalysis`.

**Why:** No legacy adapters exist. No external consumers of the protocol. A
clean break avoids maintaining dead methods. `detect_contradiction()` stays.

### 3.4 Single structured output with instructor

**Decision:** The new `process_block()` uses instructor with a `BlockAnalysis`
Pydantic model that returns all three fields in one structured response.

**Why:** instructor validates the output shape. One API call, one round-trip,
one token count. The LLM sees all the context once and produces coherent
alignment/tags/summary together — tags and summary inform each other.

### 3.5 Summary prompt instructs factual distillation

**Decision:** The combined prompt instructs the LLM to produce a 1–2 sentence
factual summary that:
- Preserves all specific details (names, numbers, preferences)
- Removes filler, formatting, and conversational tone
- Writes in third person ("User prefers..." not "I prefer...")
- Keeps domain-specific terms intact

**Why:** Summaries must be retrieval-optimised. A summary that generalises away
specifics ("User has preferences") is worse than the raw input. The prompt
must explicitly require detail preservation.

### 3.6 Config changes mirror existing patterns

**Decision:** Replace `alignment_model`/`tags_model` with `process_block_model`.
Replace `self_alignment`/`self_tags` prompt overrides with `process_block`
prompt override. Keep `contradiction_model` and `contradiction` prompt unchanged.

**Why:** Follows the existing per-call override pattern. Users who override the
prompt template get the same `{self_context}` and `{block}` variables. Users who
override the model get a single `process_block_model` instead of two separate ones.

---

## 4. File Changes

### 4.1 `src/elfmem/adapters/models.py` — Add BlockAnalysis

Add a new Pydantic model. Remove `AlignmentScore` and `SelfTagInference`.

```python
class BlockAnalysis(BaseModel):
    """Structured response from combined block analysis.

    Returned by process_block() during consolidation.
    """

    alignment_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-alignment score: 0=unrelated, 1=core identity",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Self/* tags inferred by the LLM.",
    )
    summary: str = Field(
        description=(
            "Factual 1-2 sentence distillation of the block content. "
            "Preserves all specific details. Third person."
        ),
    )
```

**Remove:** `AlignmentScore`, `SelfTagInference` (no longer used).

**Keep:** `ContradictionScore` (still used by `detect_contradiction`).

---

### 4.2 `src/elfmem/prompts.py` — Add BLOCK_ANALYSIS_PROMPT

Add a new combined prompt. Remove `SELF_ALIGNMENT_PROMPT` and `SELF_TAG_PROMPT`.

```python
BLOCK_ANALYSIS_PROMPT: str = """\
You are analysing a memory block for an agent's adaptive memory system.

## Agent Identity
{self_context}

## Memory Block
{block}

Analyse this block and return:

1. **alignment_score** (0.0–1.0): How much does this block express or reinforce
   the agent's identity, values, or self-concept?
   - 0.0: Unrelated — technical fact, external knowledge, no identity relevance
   - 0.3: Adjacent — relevant to the agent's domain but not their identity
   - 0.7: Identity-adjacent — reflects how the agent thinks or works
   - 1.0: Core identity — directly states a value, constraint, or self-defining belief

2. **tags**: Which self/* tags apply? Assign 0, 1, or multiple from:
   - self/constitutional: core invariants — never violated, fundamental to existence
   - self/constraint: strong rules — rarely violated, firm preferences
   - self/value: beliefs and principles that consistently guide behavior
   - self/style: communication style, tone, and interaction preferences
   - self/goal: active goals or objectives the agent is pursuing
   - self/context: situational context about who the agent is or what they know
   Only assign a tag if you are confident it applies. Prefer no tags over guessing.

3. **summary**: A factual 1–2 sentence distillation of the block content.
   Rules:
   - Preserve ALL specific details (names, numbers, preferences, constraints)
   - Remove filler words, formatting artifacts, and conversational tone
   - Write in third person ("User prefers..." not "I prefer...")
   - Keep domain-specific terms intact
   - If the content is already concise and factual, return it as-is

Respond with JSON: {{"alignment_score": <float>, "tags": [<strings>], "summary": "<string>"}}
"""
```

**Remove:** `SELF_ALIGNMENT_PROMPT`, `SELF_TAG_PROMPT`.

**Keep:** `CONTRADICTION_PROMPT`, `VALID_SELF_TAGS`.

---

### 4.3 `src/elfmem/ports/services.py` — Replace 2 methods with 1

```python
from elfmem.adapters.models import BlockAnalysis

@runtime_checkable
class LLMService(Protocol):
    """LLM operations required by the elfmem memory system."""

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Analyse a block: score alignment, infer tags, generate summary.

        Combined operation replacing score_self_alignment + infer_self_tags.
        Returns a BlockAnalysis with alignment_score, tags, and summary.
        """
        ...

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        """Return a float in [0.0, 1.0] indicating the contradiction strength
        between two blocks. >= threshold means active contradiction."""
        ...
```

**Remove:** `score_self_alignment()`, `infer_self_tags()`.

**Note:** `BlockAnalysis` import is from `adapters.models`. This is the one place
where a port references an adapter type. This is acceptable because `BlockAnalysis`
is a pure data container (Pydantic model, no logic). If this coupling becomes
uncomfortable, `BlockAnalysis` could move to `types.py` — but for now, keeping it
with the other instructor models is simpler.

---

### 4.4 `src/elfmem/adapters/litellm.py` — Implement process_block

Replace `score_self_alignment()` and `infer_self_tags()` with a single `process_block()`.

```python
from elfmem.adapters.models import BlockAnalysis, ContradictionScore
from elfmem.prompts import (
    BLOCK_ANALYSIS_PROMPT,
    CONTRADICTION_PROMPT,
    VALID_SELF_TAGS,
)


class LiteLLMAdapter:
    def __init__(
        self,
        *,
        model: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 512,
        timeout: int = 30,
        max_retries: int = 3,
        base_url: str | None = None,
        process_block_model: str | None = None,        # was: alignment_model + tags_model
        contradiction_model: str | None = None,
        process_block_prompt: str | None = None,        # was: alignment_prompt + tag_prompt
        contradiction_prompt: str | None = None,
        valid_self_tags: frozenset[str] | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        # ... same pattern as existing, but:
        self._process_block_model = process_block_model   # replaces _alignment_model + _tags_model
        self._process_block_prompt = (
            process_block_prompt if process_block_prompt is not None
            else BLOCK_ANALYSIS_PROMPT
        )
        # ... rest as before ...

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Analyse a block: alignment score + tags + summary in one call."""
        prompt = self._process_block_prompt.format(
            self_context=self_context, block=block
        )
        result, completion = await self._client.chat.completions.create_with_completion(
            messages=[{"role": "user", "content": prompt}],
            response_model=BlockAnalysis,
            max_retries=self._max_retries,
            **self._call_kwargs(self._process_block_model),
        )
        self._record_llm_usage(completion)
        # Filter tags to valid vocabulary
        result.tags = [tag for tag in result.tags if tag in self._valid_self_tags]
        return result

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        # unchanged
        ...
```

**Remove:** `score_self_alignment()`, `infer_self_tags()`.

**Remove imports:** `AlignmentScore`, `SelfTagInference`, `SELF_ALIGNMENT_PROMPT`,
`SELF_TAG_PROMPT`.

---

### 4.5 `src/elfmem/adapters/mock.py` — Implement process_block

Replace `score_self_alignment()` and `infer_self_tags()` with `process_block()`.

```python
from elfmem.adapters.models import BlockAnalysis


class MockLLMService:
    def __init__(
        self,
        *,
        default_alignment: float = 0.5,
        alignment_overrides: dict[str, float] | None = None,
        default_tags: list[str] | None = None,
        tag_overrides: dict[str, list[str]] | None = None,
        default_summary_prefix: str = "Summary: ",     # NEW
        summary_overrides: dict[str, str] | None = None, # NEW
        default_contradiction: float = 0.1,
        contradiction_overrides: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._default_alignment = default_alignment
        self._alignment_overrides = alignment_overrides or {}
        self._default_tags = default_tags or []
        self._tag_overrides = tag_overrides or {}
        self._default_summary_prefix = default_summary_prefix
        self._summary_overrides = summary_overrides or {}
        self._default_contradiction = default_contradiction
        self._contradiction_overrides = contradiction_overrides or {}
        self.process_block_calls: int = 0       # replaces alignment_calls + tag_calls
        self.contradiction_calls: int = 0

    async def process_block(self, block: str, self_context: str) -> BlockAnalysis:
        """Return deterministic BlockAnalysis. Checks overrides, then defaults."""
        self.process_block_calls += 1
        block_lower = block.lower()

        # Alignment
        alignment = self._default_alignment
        for substring, score in self._alignment_overrides.items():
            if substring.lower() in block_lower:
                alignment = score
                break

        # Tags
        tags = list(self._default_tags)
        for substring, override_tags in self._tag_overrides.items():
            if substring.lower() in block_lower:
                tags = override_tags
                break

        # Summary
        summary = f"{self._default_summary_prefix}{block}"
        for substring, override_summary in self._summary_overrides.items():
            if substring.lower() in block_lower:
                summary = override_summary
                break

        return BlockAnalysis(
            alignment_score=alignment,
            tags=tags,
            summary=summary,
        )

    async def detect_contradiction(self, block_a: str, block_b: str) -> float:
        # unchanged
        ...
```

**Remove:** `score_self_alignment()`, `infer_self_tags()`, `alignment_calls`,
`tag_calls`.

**Mock summary strategy:** By default, prepends `"Summary: "` to the raw content.
This makes tests deterministic while being distinguishable from raw content.
Override via `summary_overrides` dict (substring → exact summary). The deterministic
default also means existing tests that don't care about summaries work unchanged
after adjusting for the new API.

---

### 4.6 `src/elfmem/db/models.py` — Add summary column

Add one column to the `blocks` table:

```python
blocks = Table(
    "blocks",
    metadata,
    # ... existing columns ...
    Column("embedding_model", Text),
    Column("token_count", Integer),
    Column("summary", Text),             # ← NEW: LLM-generated distilled text
    Column("last_session_id", Text),
    Column("outcome_evidence", Float, nullable=False, default=0.0),
)
```

Position: after `token_count`, before `last_session_id`. Nullable (NULL for
inbox blocks that haven't been consolidated yet).

---

### 4.7 `src/elfmem/db/queries.py` — Update update_block_scoring

Add `summary` parameter to `update_block_scoring()`:

```python
async def update_block_scoring(
    conn: AsyncConnection,
    block_id: str,
    *,
    confidence: float | None = None,
    self_alignment: float | None = None,
    decay_lambda: float | None = None,
    embedding: np.ndarray | None = None,
    embedding_model: str | None = None,
    token_count: int | None = None,
    summary: str | None = None,            # ← NEW
) -> None:
    """Update scoring-related fields after consolidation (partial update)."""
    values: dict[str, object] = {}
    # ... existing field checks ...
    if summary is not None:
        values["summary"] = summary
    if values:
        await conn.execute(
            update(blocks).where(blocks.c.id == block_id).values(**values)
        )
```

---

### 4.8 `src/elfmem/operations/consolidate.py` — Use process_block

The core change. Replace the two LLM calls with one `process_block()` call and
embed the summary instead of raw content.

**Current code (lines 118–138):**

```python
# Score alignment + infer tags
alignment = await llm.score_self_alignment(content, "")
inferred_tags = await llm.infer_self_tags(content, "")
if inferred_tags:
    await add_tags(conn, block_id, inferred_tags)

all_tags = await get_tags(conn, block_id)
tier = determine_decay_tier(all_tags, category)
lam = decay_lambda_for_tier(tier)
confidence = alignment if alignment >= self_alignment_threshold else 0.50
token_count = max(1, len(content) // 4)

await update_block_scoring(
    conn, block_id,
    confidence=confidence,
    self_alignment=alignment,
    decay_lambda=lam,
    embedding=vec,
    embedding_model="mock",
    token_count=token_count,
)
```

**New code:**

```python
# Combined analysis: alignment + tags + summary in one LLM call
analysis = await llm.process_block(content, "")
if analysis.tags:
    await add_tags(conn, block_id, analysis.tags)

# Embed the summary (normalised text produces better vectors)
summary_text = analysis.summary or content
norm_summary = summary_text.strip().lower()
vec = await embedding_svc.embed(norm_summary)

all_tags = await get_tags(conn, block_id)
tier = determine_decay_tier(all_tags, category)
lam = decay_lambda_for_tier(tier)
confidence = (
    analysis.alignment_score
    if analysis.alignment_score >= self_alignment_threshold
    else 0.50
)
token_count = max(1, len(summary_text) // 4)

await update_block_scoring(
    conn, block_id,
    confidence=confidence,
    self_alignment=analysis.alignment_score,
    decay_lambda=lam,
    embedding=vec,
    embedding_model="mock",
    token_count=token_count,
    summary=analysis.summary,
)
```

**Key changes:**
1. Single `process_block()` call replaces two separate calls.
2. Embedding uses `analysis.summary or content` (fallback if empty).
3. `summary` is persisted via `update_block_scoring()`.
4. Token count based on summary length (it's what gets rendered).

**Phase 0 (warm-up) change:** The current pre-embedding pass embeds
`content.strip().lower()` for cache warming. Since consolidation now embeds the
**summary** (which doesn't exist until after the LLM call), Phase 0 becomes
unnecessary for inbox blocks. However, Phase 0 also embeds active blocks for the
near-duplicate check — that still uses content. The change:

- Phase 0 still embeds active blocks (unchanged — they already have embeddings,
  this is for mock cache warming).
- Phase 0 no longer pre-embeds inbox blocks (embeddings now happen after the LLM
  call produces the summary).
- Near-dup check for inbox blocks embeds raw content for comparison against
  active block embeddings. After promotion, the block's stored embedding is the
  summary embedding.

**Near-duplicate check detail:** The dedup check compares the new block's raw
content embedding against active blocks' stored embeddings (which are summary
embeddings). This is a content-vs-summary comparison. For exact/near duplicates,
the content and summary are similar enough that cosine similarity still works.
If the raw content is "I really prefer dark mode", and an active block's summary
embedding is for "User prefers dark mode", the cosine similarity will be high
(same semantic content). This actually works better than content-vs-content
because summaries normalise away noise.

---

### 4.9 `src/elfmem/memory/retrieval.py` — Use summary for ScoredBlock content

One-line change at line 213:

**Current:**
```python
content=block.get("content", ""),
```

**New:**
```python
content=block.get("summary") or block.get("content", ""),
```

This means ScoredBlock.content shows the summary when available. Rendering,
format helpers, and agent tool responses all use this field — zero additional
changes needed.

---

### 4.10 `src/elfmem/config.py` — Update config model

**LLMConfig changes:**

```python
class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.0
    max_tokens: int = 512
    timeout: int = 30
    max_retries: int = 3
    base_url: str | None = None

    # Per-call model overrides — None = use model above
    process_block_model: str | None = None   # was: alignment_model + tags_model
    contradiction_model: str | None = None
```

**Remove:** `alignment_model`, `tags_model`.

**PromptsConfig changes:**

```python
class PromptsConfig(BaseModel):
    # Level 1: Inline overrides
    process_block: str | None = None          # was: self_alignment + self_tags
    contradiction: str | None = None

    # Level 2: File path overrides
    process_block_file: str | None = None     # was: self_alignment_file + self_tags_file
    contradiction_file: str | None = None

    # Tag vocabulary override
    valid_self_tags: list[str] | None = None

    def resolve_process_block(self) -> str:
        """Resolve the process_block prompt: inline > file > default."""
        return self._resolve(self.process_block, self.process_block_file, "process_block")

    def resolve_contradiction(self) -> str:
        # unchanged
        ...

    def resolve_valid_tags(self) -> frozenset[str]:
        # unchanged
        ...

    def validate_templates(self) -> None:
        """Raise ValueError if any resolved prompt is missing required variables."""
        _check_vars(self.resolve_process_block(), ["self_context", "block"], "process_block")
        _check_vars(self.resolve_contradiction(), ["block_a", "block_b"], "contradiction")
```

**Remove:** `self_alignment`, `self_tags`, `self_alignment_file`, `self_tags_file`,
`resolve_self_alignment()`, `resolve_self_tags()`.

**_resolve() defaults update:** The static defaults map changes:

```python
@staticmethod
def _resolve(inline, filepath, prompt_name):
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
```

**api.py `from_config()` change:**

```python
llm_svc = LiteLLMAdapter(
    model=cfg.llm.model,
    temperature=cfg.llm.temperature,
    max_tokens=cfg.llm.max_tokens,
    timeout=cfg.llm.timeout,
    max_retries=cfg.llm.max_retries,
    base_url=cfg.llm.base_url,
    process_block_model=cfg.llm.process_block_model,    # was: alignment_model + tags_model
    contradiction_model=cfg.llm.contradiction_model,
    process_block_prompt=cfg.prompts.resolve_process_block(),  # was: alignment + tag prompts
    contradiction_prompt=cfg.prompts.resolve_contradiction(),
    valid_self_tags=cfg.prompts.resolve_valid_tags(),
    token_counter=counter,
)
```

---

### 4.11 Files unchanged

These files require **zero modifications**:

| File | Why unchanged |
|------|--------------|
| `types.py` | ScoredBlock.content receives summary at retrieval time |
| `context/rendering.py` | Uses block.content which is now summary |
| `smart.py` | Delegates to MemorySystem; no consolidation logic |
| `mcp.py` | Delegates to SmartMemory |
| `cli.py` | Delegates to SmartMemory |
| `memory/blocks.py` | Content hashing uses raw content (dedup), unchanged |
| `memory/dedup.py` | Operates on embeddings, unchanged |
| `memory/graph.py` | Edge operations, unchanged |
| `scoring.py` | Score computation, unchanged |
| `session.py` | Session management, unchanged |
| `exceptions.py` | Error types, unchanged |
| `guide.py` | Documentation, updated separately if needed |
| `db/engine.py` | Engine creation, unchanged |

---

## 5. Testing

### 5.1 Existing test updates

Tests that reference `score_self_alignment`, `infer_self_tags`, `alignment_calls`,
`tag_calls` must be updated to use `process_block` and `process_block_calls`.

**`tests/conftest.py`:** `mock_llm()` fixture uses `make_mock_llm()` which calls
`MockLLMService()` — unchanged signature for basic usage. Tests that check
`.alignment_calls` or `.tag_calls` must switch to `.process_block_calls`.

**`tests/test_mock_adapters.py`:** Tests for `score_self_alignment` and
`infer_self_tags` become tests for `process_block`. Coverage is equivalent:
alignment overrides, tag overrides, default values, call counting, plus new
summary tests.

**`tests/test_lifecycle.py`:** Tests that assert consolidation behaviour
(promoted, deduplicated, edges_created) are unchanged — they test outcomes,
not LLM call internals. Tests that check `mock_llm.alignment_calls` must
switch to `mock_llm.process_block_calls`.

**`tests/test_retrieval.py`:** Tests that check `block.content` will now see
the summary text instead of raw content. Update expected values accordingly.

### 5.2 New tests for process_block

**`tests/test_mock_adapters.py` additions:**

```python
class TestProcessBlock:
    async def test_returns_block_analysis(self, mock_llm):
        result = await mock_llm.process_block("test content", "")
        assert isinstance(result, BlockAnalysis)

    async def test_default_alignment_score(self, mock_llm):
        result = await mock_llm.process_block("test content", "")
        assert result.alignment_score == 0.5  # default

    async def test_alignment_override(self):
        llm = make_mock_llm(alignment_overrides={"identity": 0.95})
        result = await llm.process_block("identity statement", "")
        assert result.alignment_score == 0.95

    async def test_default_tags_empty(self, mock_llm):
        result = await mock_llm.process_block("test", "")
        assert result.tags == []

    async def test_tag_override(self):
        llm = make_mock_llm(tag_overrides={"value": ["self/value"]})
        result = await llm.process_block("core value", "")
        assert result.tags == ["self/value"]

    async def test_default_summary_prefixed(self, mock_llm):
        result = await mock_llm.process_block("user likes dark mode", "")
        assert result.summary == "Summary: user likes dark mode"

    async def test_summary_override(self):
        llm = make_mock_llm(summary_overrides={"dark mode": "User prefers dark mode."})
        result = await llm.process_block("I really like dark mode", "")
        assert result.summary == "User prefers dark mode."

    async def test_increments_call_counter(self, mock_llm):
        await mock_llm.process_block("a", "")
        await mock_llm.process_block("b", "")
        assert mock_llm.process_block_calls == 2
```

### 5.3 Summary in consolidation tests

**`tests/test_lifecycle.py` additions:**

```python
class TestConsolidateSummary:
    async def test_consolidated_block_has_summary(self, db_conn, mock_llm, mock_embedding):
        """After consolidation, active block has a summary field."""
        await insert_block(db_conn, block_id="abc", content="raw input", ...)
        await consolidate(db_conn, llm=mock_llm, embedding_svc=mock_embedding, ...)
        block = await get_block(db_conn, "abc")
        assert block["summary"] is not None
        assert len(block["summary"]) > 0

    async def test_summary_is_used_for_embedding(self, db_conn, mock_llm, mock_embedding):
        """Embedding is computed from summary, not raw content."""
        await insert_block(db_conn, block_id="abc", content="raw input", ...)
        await consolidate(db_conn, llm=mock_llm, embedding_svc=mock_embedding, ...)
        # MockLLMService generates "Summary: raw input" — embedding should be
        # for that text, not for "raw input"
        block = await get_block(db_conn, "abc")
        assert block["embedding"] is not None
        # Verify the embedding matches the summary text, not the raw content
        expected_vec = await mock_embedding.embed("summary: raw input")
        stored_vec = bytes_to_embedding(block["embedding"])
        from elfmem.memory.dedup import cosine_similarity
        assert cosine_similarity(expected_vec, stored_vec) > 0.99
```

### 5.4 Summary in retrieval tests

**`tests/test_retrieval.py` additions:**

```python
class TestSummaryRetrieval:
    async def test_scored_block_content_uses_summary(self, ...):
        """ScoredBlock.content shows summary when available."""
        # Set up block with both content and summary
        await insert_block(db_conn, ...)
        await update_block_scoring(db_conn, block_id, summary="Distilled summary.", ...)
        blocks = await hybrid_retrieve(db_conn, ...)
        assert blocks[0].content == "Distilled summary."

    async def test_scored_block_falls_back_to_content(self, ...):
        """ScoredBlock.content falls back to raw content when no summary."""
        # Set up block with content but no summary
        await insert_block(db_conn, ...)
        await update_block_scoring(db_conn, block_id, ...)  # no summary
        blocks = await hybrid_retrieve(db_conn, ...)
        assert blocks[0].content == "raw content"
```

---

## 6. Implementation Checklist

Per coding_principles.md, verify each changed file:

**`adapters/models.py`**
- [ ] `BlockAnalysis` has complete Field descriptions
- [ ] Pydantic validation (ge/le on alignment_score)
- [ ] `AlignmentScore` and `SelfTagInference` removed

**`prompts.py`**
- [ ] `BLOCK_ANALYSIS_PROMPT` has `{self_context}` and `{block}` placeholders
- [ ] Prompt instructs detail preservation in summary
- [ ] Prompt specifies third-person voice
- [ ] `SELF_ALIGNMENT_PROMPT` and `SELF_TAG_PROMPT` removed

**`ports/services.py`**
- [ ] `process_block()` returns `BlockAnalysis`
- [ ] `detect_contradiction()` unchanged
- [ ] `score_self_alignment` and `infer_self_tags` removed
- [ ] `@runtime_checkable` preserved

**`adapters/litellm.py`**
- [ ] `process_block()` ≤50 lines
- [ ] Tag filtering against `_valid_self_tags`
- [ ] Token recording via `_record_llm_usage()`
- [ ] Per-call model override via `_process_block_model`
- [ ] Old methods and imports removed

**`adapters/mock.py`**
- [ ] `process_block()` returns `BlockAnalysis`
- [ ] Default summary: `"Summary: {content}"`
- [ ] Summary overrides via `summary_overrides` dict
- [ ] `process_block_calls` counter
- [ ] Old methods and counters removed

**`db/models.py`**
- [ ] `summary` column added (Text, nullable)
- [ ] Position: after `token_count`

**`db/queries.py`**
- [ ] `summary` parameter added to `update_block_scoring()`
- [ ] Only updates if not None (existing pattern)

**`operations/consolidate.py`**
- [ ] Single `process_block()` call per block
- [ ] Embedding uses `analysis.summary or content`
- [ ] Summary persisted via `update_block_scoring()`
- [ ] Phase 0 no longer pre-embeds inbox blocks
- [ ] All functions ≤50 lines

**`memory/retrieval.py`**
- [ ] `content = block.get("summary") or block.get("content", "")` at line 213

**`config.py`**
- [ ] `process_block_model` replaces `alignment_model` + `tags_model`
- [ ] `process_block` prompt replaces `self_alignment` + `self_tags`
- [ ] `resolve_process_block()` method
- [ ] `validate_templates()` updated
- [ ] Old fields and methods removed

**`api.py`**
- [ ] `from_config()` passes `process_block_model` + `process_block_prompt`
- [ ] Old prompt resolution removed

**All changed files**
- [ ] `uv run pytest tests/ -x` — all tests pass, no regressions
- [ ] `uv run mypy src/` — clean
- [ ] `uv run ruff check src/` — clean

---

## 7. Implementation Order

```
Step 1  Schema + data layer
        - db/models.py: add summary column
        - db/queries.py: add summary to update_block_scoring
        Verify: uv run pytest tests/test_storage.py -v

Step 2  New model + prompt
        - adapters/models.py: add BlockAnalysis, remove AlignmentScore + SelfTagInference
        - prompts.py: add BLOCK_ANALYSIS_PROMPT, remove old prompts
        Verify: python -c "from elfmem.adapters.models import BlockAnalysis"

Step 3  Protocol + adapters
        - ports/services.py: replace 2 methods with process_block
        - adapters/mock.py: implement process_block, remove old methods
        - adapters/litellm.py: implement process_block, remove old methods
        Verify: uv run pytest tests/test_mock_adapters.py -v

Step 4  Config
        - config.py: update LLMConfig + PromptsConfig
        - api.py: update from_config() wiring
        Verify: uv run pytest tests/test_config.py -v (if exists)

Step 5  Consolidation operation
        - operations/consolidate.py: use process_block + embed summary
        Verify: uv run pytest tests/test_lifecycle.py -v

Step 6  Retrieval
        - memory/retrieval.py: summary-or-content resolution
        Verify: uv run pytest tests/test_retrieval.py -v

Step 7  Full regression + smoke test
        Verify: uv run pytest tests/ -x (all tests pass)
        Verify: ELFMEM_DB=/tmp/smoke.db elfmem remember "User prefers dark mode"
        Verify: ELFMEM_DB=/tmp/smoke.db elfmem recall "preferences" --json
        Verify: ELFMEM_DB=/tmp/smoke.db elfmem status --json
```
