# Plan: Dreaming Architecture — Learn Fast, Dream Deep

## Summary

Decouple fast learning (pure DB insert) from deep consolidation (LLM-powered "dreaming"). The `remember()` method becomes instant. A new `dream()` method handles consolidation when the agent is ready.

## Naming Decision

**Operation name: `dream`**

Rationale:
- Consistent with elfmem vocabulary: `remember`, `recall`, `dream` — all cognitive verbs
- Biological metaphor is accurate: brains consolidate memories during sleep/dreaming
- Short (5 chars), memorable, unambiguous
- Agent-friendly: `should_dream` / `dream()` reads naturally as a decision point
- Full surface: `elfmem_dream` (MCP), `elfmem dream` (CLI), `mem.dream()` (Python)

## Discovery

- `learn()` in `operations/learn.py` is already fast: pure DB insert, 0 API calls, milliseconds
- `consolidate()` in `operations/consolidate.py` is expensive: O(n*m) LLM calls for contradiction detection
- `SmartMemory.remember()` couples learn to consolidate via auto-trigger at `_threshold`
- This coupling makes `remember()` unpredictably slow (fast 9 times, then blocks on 10th)

## Architecture: Three Rhythms

```
HEARTBEAT (milliseconds)     BREATHING (seconds)         SLEEP (minutes)
remember() → DB insert       dream() → embed + align     curate() → prune + archive
Every interaction             When inbox is full          Scheduled maintenance
0 API calls                  LLM calls per inbox block    Full graph maintenance
```

## Implementation Steps

### Step 1: Decouple SmartMemory.remember()
**File:** `src/elfmem/smart.py`
**Change:** Remove auto-consolidation from `remember()`. It becomes pure fast-path.

```python
async def remember(self, content, tags=None, category="knowledge"):
    """Fast-path learn. Never blocks for consolidation."""
    await self._system.begin_session()
    result = await self._system.learn(content, tags=tags, category=category)
    if result.status == "created":
        self._pending += 1
    return result
```

### Step 2: Add dream() method
**File:** `src/elfmem/smart.py`
**Change:** New method + `should_dream` property.

```python
@property
def should_dream(self) -> bool:
    """Check if consolidation is needed."""
    return self._pending >= self._threshold

async def dream(self) -> ConsolidateResult | None:
    """Deep consolidation — embed, align, detect contradictions.
    Call when should_dream is True, or at session end."""
    if self._pending == 0:
        return None
    result = await self._system.consolidate()
    self._pending = 0
    return result
```

### Step 3: Add elfmem_dream MCP tool
**File:** `src/elfmem/mcp.py`
**Change:** New tool + update `elfmem_remember` to include `should_dream` advisory.

```python
@mcp.tool()
async def elfmem_dream(ctx: Context) -> str:
    """Consolidate recently learned knowledge (embed, align, detect contradictions).
    Call when elfmem_remember indicates should_dream=True, or at session end."""
    mem = _get_memory(ctx)
    result = await mem.dream()
    if result is None:
        return "Nothing to consolidate."
    return str(result)
```

Update `elfmem_remember` response to include:
```python
if mem.should_dream:
    response += f"\n\nAdvisory: {mem._pending} blocks pending. Consider calling elfmem_dream."
```

### Step 4: Add dream CLI command
**File:** `src/elfmem/cli.py`
**Change:** New `dream` command.

```python
@app.command()
def dream(db_path: str = ..., config: str = ...):
    """Consolidate pending knowledge — embed, align, detect contradictions."""
    async def _run():
        async with SmartMemory.managed(db_path, config=config) as mem:
            result = await mem.dream()
            ...
    asyncio.run(_run())
```

### Step 5: Optimize contradiction detection (pre-filter)
**File:** `src/elfmem/operations/consolidate.py`
**Change:** Pre-filter by embedding similarity before LLM contradiction check.

```python
# Before: O(n*m) LLM calls
for inbox_block in inbox:
    for active_block in active:
        await llm_check_contradiction(inbox_block, active_block)

# After: Only check similar pairs
SIMILARITY_THRESHOLD = 0.40
for inbox_block in inbox:
    similar = [b for b in active if cosine(inbox_block.embedding, b.embedding) > SIMILARITY_THRESHOLD]
    for active_block in similar:
        await llm_check_contradiction(inbox_block, active_block)
# ~95% fewer LLM calls
```

### Step 6: Batch embeddings
**File:** `src/elfmem/litellm.py`
**Change:** Add `embed_batch()` for bulk embedding.

```python
async def embed_batch(self, texts: list[str]) -> list[list[float]]:
    """Embed multiple texts in one API call."""
    response = await litellm.aembedding(model=self._model, input=texts)
    return [item["embedding"] for item in response.data]
```

### Step 7: ConsolidationPolicy (SELF-driven timing)
**File:** `src/elfmem/smart.py`
**Change:** Policy class for autonomous dreaming decisions.

Constitutional principles that govern timing:
- **Block 7 (Rhythm):** Don't dream mid-task. Dream at natural pauses.
- **Block 10 (Reflection):** Transitions are consolidation opportunities.
- **Block 2 (Minimum force):** Only dream when there's enough to justify the cost.

```python
@dataclass
class ConsolidationPolicy:
    inbox_threshold: int = 10          # Block 2: minimum force
    max_pending: int = 50              # Block 7: don't accumulate too much
    dream_on_session_end: bool = True  # Block 10: transitions
    auto_dream: bool = False           # Autonomous mode (no confirmation)
```

### Step 8: Update session() context manager
**File:** `src/elfmem/smart.py`
**Change:** Dream on exit if pending blocks exist.

```python
@classmethod
@asynccontextmanager
async def managed(cls, db_path, *, config=None, threshold=10):
    mem = cls(...)
    try:
        yield mem
    finally:
        if mem.should_dream:
            await mem.dream()  # Consolidate on exit
        await mem._system.end_session()
```

## Testing Strategy

- Unit test `remember()` never triggers consolidation
- Unit test `should_dream` threshold logic
- Unit test `dream()` delegates to `consolidate()` and resets counter
- Integration test: remember N blocks → should_dream=True → dream() → blocks consolidated
- Integration test: pre-filter reduces LLM calls vs baseline
- Integration test: `managed()` context manager dreams on exit

## Migration

- `SmartMemory.remember()` becomes non-breaking (faster, same return type)
- Agents using MCP get `should_dream` advisory in remember response
- Existing `consolidate()` in `MemorySystem` unchanged (dream() wraps it)
- CLI adds `dream` command alongside existing `curate`

## Risk Assessment

- **Low risk:** remember() change is purely subtractive (removes blocking call)
- **Low risk:** dream() wraps existing consolidate() — no new logic initially
- **Medium risk:** Pre-filter threshold (0.40) needs tuning — too high misses contradictions, too low doesn't help
- **Mitigation:** Make threshold configurable, start conservative (0.35)

## Priority Order

1. Steps 1-2 (decouple + dream method) — highest impact, lowest risk
2. Step 3 (MCP tool) — agents need the interface
3. Step 8 (session context manager) — safety net
4. Step 4 (CLI) — parity
5. Steps 5-6 (optimization) — performance improvement
6. Step 7 (policy) — autonomous operation
