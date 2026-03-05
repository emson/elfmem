# Plan: Token Usage Tracking

**Reference:** `docs/agent_friendly_principles.md`, `docs/plans/plan_agent_friendly_refactor.md`

---

## Problem Statement

When an agent runs, it has no visibility into what LLM/embedding calls were made, how many tokens were consumed, or what the cumulative cost profile looks like across sessions. This matters for:

- **Cost awareness**: agents (and their operators) need to know what memory maintenance costs
- **Debugging**: unusually high token counts indicate retry storms or unexpected consolidation patterns
- **Capacity planning**: understanding how token usage scales with block count and session frequency

---

## Before: Where Tokens Are Consumed Today

Reading the code reveals a precise call map:

### LLM calls (`LiteLLMAdapter`)

All LLM calls happen **only during `consolidate()`**, via three methods:

| Method | When called | Per-call cost |
|--------|------------|---------------|
| `score_self_alignment()` | Once per inbox block | ~200–500 input tokens, ~5–10 output tokens |
| `infer_self_tags()` | Once per inbox block | ~200–500 input tokens, ~20–50 output tokens |
| `detect_contradiction()` | For each inbox × active block pair above threshold | ~300–600 input tokens, ~5–10 output tokens |

**Important:** `instructor` retries on malformed structured output (default `max_retries=3`). A retry is a real API call at real cost. `create_with_completion()` captures only the **final** successful call's usage — retried-and-failed calls are silently undercounted. This is a known limitation to document, not fix.

### Embedding calls (`LiteLLMEmbeddingAdapter`)

| Location | When called |
|----------|-------------|
| `consolidate()` | Every active + inbox block embedded once (warming the cache, then dedup checks) |
| `frame("attention", query=...)` | Query text embedded once per uncached call |
| `frame("task", query=...)` | Query text embedded once |
| `recall(query=...)` | Query text embedded once |

Embedding tokens are typically 10–100× cheaper than LLM tokens (e.g., `text-embedding-3-small` at $0.00002/1K vs `claude-haiku` at $0.001/1K). They must be tracked **separately** — mixing them with LLM tokens produces meaningless cost estimates.

### What currently happens to token data

```python
# LiteLLMAdapter.score_self_alignment():
result: AlignmentScore = await self._client.chat.completions.create(
    response_model=AlignmentScore, ...
)
return result.score  # ← usage data silently discarded
```

```python
# LiteLLMEmbeddingAdapter.embed():
response = await litellm.aembedding(**kwargs)
# response.usage.prompt_tokens is available but never read
raw = response.data[0]["embedding"]
return vec  # ← token count discarded
```

Token data **exists** in the responses. It is simply never captured.

---

## Reasoning: What Granularity?

This is the central design question. The options, evaluated:

### Option A — Per-call granularity
Track every individual LLM/embedding call with its token count, stored as log entries.

**Pros:** Maximum detail; can reconstruct any aggregation; useful for spotting retry storms.
**Cons:** Storage overhead grows with block count × sessions. An agent managing 500 blocks over 100 sessions could generate thousands of records. Agents reading status don't need per-call data — they need summary signals.
**Verdict:** Too granular for the primary surface. Can be a future `history()` annotation.

### Option B — Per-operation granularity (per `consolidate()`, per `frame()`)
Attach token counts to each `OperationRecord` in history.

**Pros:** Answers "how expensive was this consolidation?"; visible in `history()`.
**Cons:** Doesn't aggregate to anything useful for ongoing cost awareness. Reading history to sum consolidation costs is awkward for agents.
**Verdict:** Good *secondary* surface — add to `OperationRecord.summary` for free; primary surface is still aggregated.

### Option C — Daily totals
Reset at midnight UTC; query "tokens today".

**Pros:** Familiar billing metric; maps to provider dashboards.
**Cons:** elfmem uses **session-aware time**, not wall-clock. The system is designed to survive long idle periods without accumulating "downtime" in its time model. A wall-clock daily window conflicts with this design. An agent running for 48 hours of continuous sessions that spans 3 calendar days produces confusing per-day numbers.
**Verdict:** Rejected. Wall-clock periods are incoherent with the session-aware model.

### Option D — Per-active-hour rate
Tokens-per-active-hour as the primary metric.

**Pros:** Normalises for session length; consistent with how `total_active_hours` is already tracked.
**Cons:** Rate metrics are derived from totals — they don't replace them. Insufficient alone.
**Verdict:** A useful *derived* metric, not a primary one.

### Option E — Session + Lifetime (chosen)

Two aggregation windows:

1. **Session total**: accumulated since the last `begin_session()`. Resets on each `begin_session()`. In-memory only — fast, zero DB overhead.
2. **Lifetime total**: accumulated across all sessions. Persisted to `system_config` on each `end_session()`. Survives process restarts.

**Why this is right:**
- "Session" is already the primary unit in elfmem — it has a start/end, active-hour tracking, and a task_type. Token costs map naturally to "what did this session cost?"
- "Lifetime" answers "what has this agent spent in total?" — essential for long-running deployments.
- Both are fast to read: session from in-memory counter, lifetime from a single DB config key.
- Neither requires wall-clock concepts. Both are session-aware.
- The pair answers all practical questions agents have about cost without over-engineering.

**What we deliberately exclude:**
- Per-day totals (wall-clock incoherence)
- Moving averages or rolling windows (agent doesn't need trend analysis)
- Per-block-type breakdown (too granular for primary surface)
- Estimated cost in dollars (model pricing changes; better for the caller to compute)

---

## Architecture

### Core Insight: Keep the Protocol Clean

The `LLMService` and `EmbeddingService` protocols in `ports/services.py` must **not change**. If we added a `token_usage()` method to the protocols, we'd break every mock adapter and every custom adapter users write. The mock adapters must stay unchanged.

The solution: a **shared `TokenCounter`** instance owned by `MemorySystem`, injected into both adapters at construction time. Adapters record to it; `MemorySystem` reads from it. No protocol changes.

```
MemorySystem
├── _session_token_counter: TokenCounter   (in-memory, reset per session)
├── _llm: LiteLLMAdapter
│       └── _token_counter: TokenCounter   (same object ↑)
└── _embedding: LiteLLMEmbeddingAdapter
        └── _token_counter: TokenCounter   (same object ↑)
```

When adapters make calls, they record to the shared counter. When `status()` is called, `MemorySystem` reads from its owned counter directly — no protocol access needed.

When **mock adapters** are used (testing), no `TokenCounter` is created or passed. `status()` returns `TokenUsage()` (all zeros) gracefully.

### `create_with_completion` — the key instructor change

Currently, `LiteLLMAdapter` uses `instructor`'s `create()` which returns only the parsed model:

```python
# Current — usage discarded
result = await self._client.chat.completions.create(response_model=AlignmentScore, ...)
return result.score
```

`instructor` provides `create_with_completion()` which returns `(model, raw_completion)`:

```python
# After — usage captured
result, completion = await self._client.chat.completions.create_with_completion(
    response_model=AlignmentScore, ...
)
if self._token_counter is not None and completion.usage is not None:
    self._token_counter.record_llm(
        input_tokens=completion.usage.prompt_tokens or 0,
        output_tokens=completion.usage.completion_tokens or 0,
    )
return result.score
```

The `or 0` guards handle providers that return `None` for usage fields (Ollama, some proxies).

---

## Data Model

### `TokenUsage` — immutable snapshot dataclass

```python
@dataclass(frozen=True)
class TokenUsage:
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    embedding_tokens: int = 0
    llm_calls: int = 0
    embedding_calls: int = 0

    @property
    def llm_total_tokens(self) -> int:
        return self.llm_input_tokens + self.llm_output_tokens

    @property
    def total_tokens(self) -> int:
        return self.llm_total_tokens + self.embedding_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage: ...   # combine two windows
    def __str__(self) -> str: ...                              # agent-readable one-liner
    def to_dict(self) -> dict[str, int]: ...
```

Frozen because it represents a point-in-time snapshot, not a live counter. `__add__` allows combining session + lifetime for display or persistence.

Example `__str__` output:
```
LLM: 4,820 tokens (in: 4,560, out: 260, 9 calls) | Embed: 1,230 tokens (14 calls)
LLM: 0 tokens (no calls this session) | Embed: 340 tokens (6 calls)
```

### `TokenCounter` — mutable in-process accumulator

```python
class TokenCounter:
    """Mutable accumulator. Owned by MemorySystem, injected into adapters."""
    def record_llm(self, input_tokens: int, output_tokens: int) -> None: ...
    def record_embedding(self, tokens: int) -> None: ...
    def snapshot(self) -> TokenUsage: ...   # read without resetting
    def reset(self) -> TokenUsage: ...      # snapshot + zero all fields
```

Not a dataclass — needs mutation. Lives in `src/elfmem/token_counter.py`.

### `SystemStatus` — add two new fields

```python
@dataclass
class SystemStatus:
    # ... existing fields ...
    session_tokens: TokenUsage   # this session only (in-memory)
    lifetime_tokens: TokenUsage  # all time (from DB)
```

`__str__` updated to include a token line:
```
Session: active (1.2h) | Inbox: 8/10 | Active: 42 blocks | Health: good
Tokens (session): LLM: 4,820 (9 calls) | Embed: 1,230 (14 calls)
Suggestion: Memory healthy. No action required.
```

### Persistence — single JSON key in `system_config`

Lifetime totals are stored as a single JSON value under `"lifetime_token_usage"`:
```json
{"llm_input_tokens": 42300, "llm_output_tokens": 2100, "embedding_tokens": 18400, "llm_calls": 87, "embedding_calls": 340}
```

Single key → single atomic upsert. Easy to extend with new fields. Consistent with existing `system_config` pattern.

**When persisted:** On every `end_session()`. Session counter is read, added to the current lifetime total fetched from DB, and the combined total is written back.

**Crash window:** If the process crashes after `consolidate()` but before `end_session()`, that session's tokens are lost. For an observability/debugging system (not a billing system), this is acceptable. We document the limitation clearly.

---

## Control Flow Changes

### `MemorySystem.__init__` (new field)
```
+ self._session_token_counter: TokenCounter | None = None
  (set to a new TokenCounter if LiteLLM adapters are used)
```

### `MemorySystem.from_config()` (modified)
```
counter = TokenCounter()
llm_svc = LiteLLMAdapter(..., token_counter=counter)
embedding_svc = LiteLLMEmbeddingAdapter(..., token_counter=counter)
return cls(..., token_counter=counter)
```

### `MemorySystem.__init__` (accepts optional counter)
```
self._session_token_counter = token_counter
```

### `MemorySystem.begin_session()` (modified)
```
If _session_token_counter is not None:
    discard any previous session state
    _session_token_counter.reset()  ← start fresh for new session
```

### `MemorySystem.end_session()` (modified)
```
If _session_token_counter is not None:
    session_usage = _session_token_counter.reset()
    Inside the existing DB transaction:
        raw = await get_config(conn, "lifetime_token_usage")
        lifetime = TokenUsage(**json.loads(raw)) if raw else TokenUsage()
        updated = lifetime + session_usage
        await set_config(conn, "lifetime_token_usage", json.dumps(updated.to_dict()))
```

### `MemorySystem.status()` (modified)
```
session_tokens = _session_token_counter.snapshot() if counter else TokenUsage()
Inside existing DB connect():
    + raw = await get_config(conn, "lifetime_token_usage")
    + lifetime_tokens = TokenUsage(**json.loads(raw)) if raw else TokenUsage()
Return SystemStatus(..., session_tokens=session_tokens, lifetime_tokens=lifetime_tokens)
```

### `LiteLLMAdapter` (all three call sites)
```
Change: create()  →  create_with_completion()
After each call:
    if self._token_counter is not None and completion.usage is not None:
        self._token_counter.record_llm(
            input_tokens=completion.usage.prompt_tokens or 0,
            output_tokens=completion.usage.completion_tokens or 0,
        )
```

### `LiteLLMEmbeddingAdapter.embed()` (modified)
```
After litellm.aembedding() call:
    if self._token_counter is not None and response.usage is not None:
        self._token_counter.record_embedding(response.usage.prompt_tokens or 0)
```

---

## Edge Cases and Mitigations

| Edge Case | Risk | Mitigation |
|-----------|------|------------|
| Provider returns `None` for `usage` | Counter gets wrong value | `or 0` on every field access |
| `usage.prompt_tokens` is `None` but not the object | Same | `or 0` guard |
| `instructor` retries before success | Under-counts tokens | Documented limitation; retries are infrequent by design |
| Process crash before `end_session()` | Session tokens not persisted | Documented limitation; acceptable for observability use case |
| Two `MemorySystem` instances on same DB | Both add sessions to lifetime | Correct behaviour — they're separate instances contributing to the same system |
| `begin_session()` called when session active (idempotent) | Token counter should NOT reset | Guard: only reset counter when a new session is actually started (i.e., `_session_id` was None) |
| Mock adapters in use | No counter passed | Counter is `None`; `status()` returns `TokenUsage()` (all zeros) gracefully |
| Overflow for very long-lived systems | `int` overflow | Python `int` is arbitrary precision; not a concern |
| `lifetime_token_usage` key is corrupted JSON | `json.loads()` raises | Wrap in try/except; return `TokenUsage()` zeros with a logged warning |
| Ollama/local models with no usage data | All token fields are 0 | `or 0` guard; result is "0 tokens" which is technically inaccurate but safe |
| `from_config()` not used (direct construction) | No counter created | Counter defaults to `None`; all counts show as zero; not a regression |

### The Retry Undercounting Problem (Detailed)

`instructor` with `max_retries=3` will retry LLM calls if the response fails Pydantic validation. Each retry is a real API call. `create_with_completion()` returns the **final successful** call's usage only. If 2 retries occurred before success:
- Actual API cost: 3 calls worth of tokens
- Counted: 1 call worth of tokens

**Why we accept this:** Retries should be rare (they indicate prompt or model issues). The discrepancy is bounded (max 2 extra calls per successful call). This is a debugging tool — approximate counts are still useful. Exact billing should use the provider's dashboard.

**Future option:** Use `instructor`'s `usage_control` context or patch the retry hook to count all attempts. Deferred.

---

## File Locations

```
src/elfmem/
├── token_counter.py          NEW   — TokenCounter mutable accumulator class only
│                                     (imports TokenUsage from types.py)
├── types.py                  MOD   — add TokenUsage frozen dataclass (public API
│                                     type: exported from __init__.py);
│                                     add session_tokens, lifetime_tokens to SystemStatus
│                                     (with default_factory=TokenUsage so existing code
│                                     that constructs SystemStatus still compiles)
├── adapters/
│   └── litellm.py            MOD   — accept token_counter: TokenCounter | None = None
│                                     kwarg; switch to create_with_completion(); record
│                                     tokens after each call; guard usage is not None
├── api.py                    MOD   — create TokenCounter in from_config();
│                                     pass to adapters and constructor; reset on
│                                     begin_session() (only when truly starting new);
│                                     persist on end_session() via _parse_token_usage();
│                                     read in status(); import json
└── __init__.py               MOD   — export TokenUsage

tests/
├── test_token_tracking.py    NEW   — unit: TokenUsage, TokenCounter;
│                                     integration: status() token fields, persistence,
│                                     session lifecycle; adapter: mock create_with_completion
├── test_agent_api.py         MOD   — update test_status_to_dict_has_all_keys expected
│                                     key set (add "session_tokens", "lifetime_tokens")
└── test_result_types.py      MOD   — update TestSystemStatus.test_to_dict_has_all_fields
                                      expected key set (add same two keys)
```

**Unchanged:** `ports/services.py`, `adapters/mock.py`, `operations/` layer, `context/`, `memory/`, `scoring.py`, `session.py`, `config.py`, `guide.py`

### Architectural note: `TokenUsage` lives in `types.py`

`TokenUsage` is a public API type (returned inside `SystemStatus`, exported from
`__init__.py`, used in test assertions). It belongs in `types.py` alongside all
other result types — NOT in `token_counter.py`. `TokenCounter` is an internal
accumulator that imports `TokenUsage` from `types.py`.

### `SystemStatus.__str__` becomes 3-line

After the change, `str(status)` renders:
```
Session: active (1.2h) | Inbox: 8/10 | Active: 42 blocks | Health: good
Tokens this session: LLM: 4,820 tokens (9 calls) | Embed: 1,230 tokens (14 calls)
Suggestion: Memory healthy. No action required.
```
`status.summary` remains the compact single-line version (no tokens). This
preserves the `summary` pattern used by all result types.

### `end_session()` token persistence — transaction scope

The counter is reset (capturing the snapshot) BEFORE the DB transaction to
ensure atomicity:
```
session_usage = counter.reset()          # capture + zero — no DB needed
async with engine.begin() as conn:
    duration = await _end_session(...)   # DB write
    if session_usage is not None:        # DB write (atomic with session end)
        ... persist lifetime + session_usage ...
```

---

## Guide Update

The `status` entry in `guide.py` should be updated to mention token fields:

```
RETURNS: SystemStatus with: session_active, inbox_count, health, suggestion,
session_tokens (TokenUsage for this session), lifetime_tokens (TokenUsage all time).
```

---

## Test Plan

**`tests/test_token_tracking.py`** — new file

Unit tests (no DB, no adapters):
- `test_token_usage_llm_total_is_sum_of_input_output`
- `test_token_usage_total_tokens_includes_embedding`
- `test_token_usage_str_zero_no_calls`
- `test_token_usage_str_nonzero_formatted_with_commas`
- `test_token_usage_add_combines_fields`
- `test_token_usage_to_dict_round_trips`
- `test_token_counter_record_llm_accumulates`
- `test_token_counter_record_embedding_accumulates`
- `test_token_counter_snapshot_does_not_reset`
- `test_token_counter_reset_returns_snapshot_and_zeros`
- `test_token_counter_multiple_records_accumulate`

Integration tests (with `test_engine` + `MemorySystem`):
- `test_status_session_tokens_zero_before_consolidate`
- `test_status_lifetime_tokens_zero_on_fresh_db`
- `test_status_session_tokens_populated_after_mock_counter_records`
  _(inject a pre-populated counter to verify status() reads it)_
- `test_end_session_persists_lifetime_tokens_to_db`
- `test_lifetime_tokens_accumulate_across_sessions`
- `test_begin_session_resets_session_counter`
- `test_status_returns_token_usage_when_no_counter` _(mock adapter path)_
- `test_token_usage_idempotent_begin_session_does_not_reset`

Adapter unit tests (no DB):
- `test_litellm_adapter_records_llm_tokens_when_counter_provided`
  _(mock `create_with_completion` to return fake usage)_
- `test_litellm_adapter_skips_recording_when_no_counter`
- `test_litellm_adapter_handles_none_usage_gracefully`
- `test_litellm_embedding_adapter_records_embedding_tokens`
- `test_litellm_embedding_adapter_handles_none_usage`

---

## Execution Phases

| Phase | Scope | Risk |
|-------|-------|------|
| 1 — `TokenUsage` + `TokenCounter` | New `token_counter.py`; add `TokenUsage` to `types.py`; update `__init__.py` | Zero |
| 2 — Adapter changes | `litellm.py`: add `token_counter` param, switch to `create_with_completion()` | Low — additive, counter is optional |
| 3 — `MemorySystem` wiring | `api.py`: create counter in `from_config()`, pass to adapters, reset on `begin_session()`, persist on `end_session()`, read in `status()` | Low |
| 4 — `SystemStatus` update | `types.py`: add `session_tokens` and `lifetime_tokens` fields to `SystemStatus`; update `__str__` | Low — additive fields |
| 5 — Guide update | `guide.py`: update `status` entry RETURNS description | Trivial |

Phases 1–5 are a single cohesive change. No phase gate is needed. All existing tests continue to pass because:
- Mock adapters have no `token_counter` param → unchanged
- `SystemStatus` gets new fields with zero defaults → `__str__` gains a token line but existing assertions on other fields are unaffected
- No protocol changes

---

## What Is Explicitly Out of Scope

- **Estimated cost in USD**: Model pricing changes frequently; this is the operator's responsibility, not the library's. The library reports tokens; the caller computes cost.
- **Per-call-type breakdown in primary surface**: How many tokens went to alignment vs. contradiction vs. tags is useful for debugging but too granular for `status()`. This can be a future `token_usage(breakdown=True)` extension.
- **Daily/weekly rolling windows**: Incompatible with the session-aware time model.
- **Exact retry counting**: Requires patching instructor internals. Benefit does not justify complexity.
- **Streaming support**: elfmem does not use streaming completions; not applicable.
