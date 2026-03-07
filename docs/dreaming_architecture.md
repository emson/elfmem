# The Dreaming Architecture — Learn Fast, Consolidate Deep

## The Discovery

**Learn is already fast.** The current `learn()` implementation (in `operations/learn.py`) makes zero LLM calls and zero embedding calls. It's a pure database insert — content hash, UUID, store in INBOX. Milliseconds.

**The slowness is consolidation happening inline.** `SmartMemory.remember()` (in `smart.py:70-84`) auto-triggers `consolidate()` when the inbox fills up. The Nth learn call blocks while ALL pending items are processed through the expensive consolidation pipeline.

**Consolidation is O(n × m).** For each inbox block, contradiction detection calls the LLM against EVERY active block. With 22 inbox items and 100 active blocks, that's 2,200+ LLM calls — plus 166 embedding calls and 22 process_block LLM calls.

This is the architectural mismatch: **a fast operation (learn) is coupled to a slow operation (consolidate) in the same call path.**

---

## The Biological Metaphor

The solution mirrors how biological memory works:

### Waking (Learn)
- Rapid encoding of experiences into hippocampus
- Surface-level processing — just capture it
- High volume, low cost
- No deep analysis, no integration

### Dreaming (Consolidate)
- Transfer from hippocampus to cortex
- Deep processing: understand meaning (embeddings), find connections (edges), detect conflicts (contradictions)
- Pattern extraction: determine what matters (alignment scoring, decay tiers)
- Memory pruning: remove duplicates, archive superseded

### Deep Sleep (Curate)
- Synaptic homeostasis: balance weights
- Memory pruning: archive decayed blocks
- Identity reinforcement: boost constitutional blocks

**The key insight: You don't dream while you're running. You dream when you rest.**

---

## Current Architecture (The Problem)

```
SmartMemory.remember(content, tags)
  │
  ├─ learn(content, tags)              ← FAST (milliseconds)
  │   ├─ compute_content_hash()
  │   ├─ check duplicate
  │   └─ insert into INBOX
  │
  └─ IF pending >= threshold:          ← BLOCKS on Nth call!
     └─ consolidate()                  ← SLOW (seconds to minutes)
         ├─ embed ALL active blocks    ← O(m) embedding calls
         ├─ embed ALL inbox blocks     ← O(n) embedding calls
         ├─ FOR EACH inbox block:
         │   ├─ near-dup check         ← O(m) similarity comparisons
         │   ├─ llm.process_block()    ← LLM call (alignment + tags + summary)
         │   ├─ embed summary          ← embedding call
         │   ├─ contradiction check    ← O(m) LLM calls per inbox block!
         │   └─ promote to active
         └─ edge creation              ← O(n × m) similarity comparisons
```

**Cost for 22 inbox blocks, 100 active blocks:**
- 122 embedding calls (active warmup + inbox warmup)
- 22 process_block LLM calls
- 22 summary embedding calls
- ~2,200 contradiction LLM calls
- ~2,200 edge similarity comparisons
- **Total: ~2,366 API calls**

---

## Proposed Architecture (The Solution)

### Principle: Decouple Learn from Consolidation

```
┌─────────────────────────────────────────────────────────────┐
│                    WAKING (Learn)                           │
│                                                             │
│  Agent encounters knowledge worth keeping                   │
│  ↓                                                         │
│  learn(content, tags)                                      │
│  ├─ Hash content                                           │
│  ├─ Check exact duplicate in INBOX                         │
│  ├─ Insert into INBOX                                      │
│  └─ Return immediately                                     │
│                                                             │
│  Time: < 50ms                                              │
│  API calls: 0                                              │
│  Blocks: Never. Always returns fast.                       │
│                                                             │
│  Status field tells agent if consolidation is advisable:   │
│  "inbox_count: 8/10 — consider dreaming soon"              │
└─────────────────────────────────────────────────────────────┘

         ↓ (at natural transition point)

┌─────────────────────────────────────────────────────────────┐
│                    DREAMING (Consolidate)                   │
│                                                             │
│  Triggered by:                                             │
│  • Agent explicitly calls dream()                          │
│  • Natural transition (task complete, domain switch)        │
│  • Inbox threshold reached (agent is advised, not forced)   │
│  • Autonomous policy (if running autonomously)             │
│                                                             │
│  dream()                                                   │
│  ├─ Batch embed all pending items                          │
│  ├─ Deduplication                                          │
│  ├─ LLM analysis (alignment + tags + summary)             │
│  ├─ Contradiction detection (pre-filtered by similarity)   │
│  ├─ Edge creation                                          │
│  └─ Promote to active                                      │
│                                                             │
│  Time: 5-60 seconds (depends on inbox size)                │
│  This is intentional. Deep processing takes time.          │
└─────────────────────────────────────────────────────────────┘

         ↓ (on schedule or explicit request)

┌─────────────────────────────────────────────────────────────┐
│                 DEEP SLEEP (Curate)                         │
│                                                             │
│  Triggered by:                                             │
│  • After consolidation (if curate_interval elapsed)        │
│  • Explicit request                                        │
│                                                             │
│  curate()                                                  │
│  ├─ Archive decayed blocks                                 │
│  ├─ Prune weak edges                                       │
│  ├─ Reinforce top-N patterns                               │
│  └─ Meta-health assessment                                 │
│                                                             │
│  Time: 1-5 seconds (DB operations only)                    │
└─────────────────────────────────────────────────────────────┘
```

---

## The SELF-Driven Consolidation Policy

### When Should an Autonomous Agent Dream?

Using the 10 constitutional blocks to reason about optimal timing:

**Block 7 (Rhythmic Learning):** "Sustain excellence through rhythm — push, then recover, then push again."

→ Consolidation IS the recovery phase. It should happen naturally, not forcefully. The rhythm is: learn fast → dream → learn fast → dream.

**Block 2 (Minimum Force):** "Apply minimum force that solves the problem. When unsure, do less."

→ Don't consolidate after every learn (too much overhead). Don't wait too long (INBOX overwhelms). Find the natural balance point.

**Block 10 (Reflection at Transitions):** "At natural transitions, pause and reflect."

→ Consolidation should happen at natural transition points — task completion, domain switch, session end. NOT on arbitrary timers.

**Block 5 (Epistemic Humility):** "Design reversible moves when knowledge is thin."

→ Start with conservative triggers. Observe system behavior. Adjust based on evidence. The policy itself should be adaptive.

**Block 1 (Curiosity):** "I learn through action, evolve through reflection."

→ Learning (action) and consolidation (reflection) are complementary. Neither should dominate.

### The Policy

```python
class ConsolidationPolicy:
    """SELF-driven consolidation timing.

    Determines when dreaming (consolidation) should occur,
    guided by constitutional principles:
    - Block 7: Rhythmic pacing (not too frequent, not too rare)
    - Block 2: Minimum force (only when genuinely needed)
    - Block 10: Natural transitions (pause at boundaries)
    - Block 5: Adaptive (adjust based on observed behavior)
    """

    def __init__(self, config: MemoryConfig):
        self.inbox_threshold = config.inbox_threshold
        self.max_hours_without = 8.0       # Block 7: rhythmic fallback
        self.urgency_ratio = 0.8           # Suggest at 80% of threshold

    def advise(self, state: SystemState) -> ConsolidationAdvice:
        """Return advice on whether to consolidate now.

        Never forces consolidation. Returns advice that the agent
        or user can act on (or ignore).
        """
        # Nothing to consolidate
        if state.inbox_count == 0:
            return ConsolidationAdvice(should=False)

        # Block 7: Inbox full — strong signal to dream
        if state.inbox_count >= self.inbox_threshold:
            return ConsolidationAdvice(
                should=True,
                urgency="high",
                trigger="inbox_full",
                reason=(
                    f"Inbox full ({state.inbox_count}/{self.inbox_threshold}). "
                    "Dream now to integrate pending knowledge."
                ),
            )

        # Block 10: Natural transition — ideal moment
        if state.at_transition:
            return ConsolidationAdvice(
                should=True,
                urgency="normal",
                trigger="transition",
                reason="Natural transition point. Good time to dream.",
            )

        # Block 7: Approaching threshold — gentle nudge
        if state.inbox_count >= self.inbox_threshold * self.urgency_ratio:
            return ConsolidationAdvice(
                should=True,
                urgency="low",
                trigger="approaching",
                reason=(
                    f"Inbox filling ({state.inbox_count}/{self.inbox_threshold}). "
                    "Consider dreaming at next natural pause."
                ),
            )

        # Block 2: Time-based fallback — safety net
        if state.hours_since_consolidation > self.max_hours_without:
            return ConsolidationAdvice(
                should=True,
                urgency="low",
                trigger="scheduled",
                reason=(
                    f"Been {state.hours_since_consolidation:.1f}h since last dream. "
                    "Time for a consolidation cycle."
                ),
            )

        # No consolidation needed
        return ConsolidationAdvice(should=False)
```

### Autonomous vs Interactive Mode

```
INTERACTIVE MODE (default):
  Agent learns → learn result includes advisory:
    {"status": "created", "inbox": "8/10",
     "suggestion": "Consider dreaming soon"}
  Agent decides when to call dream()
  User can also call elfmem_dream() manually

AUTONOMOUS MODE:
  Agent learns → policy checks automatically
  If policy.advise().should == True:
    Consolidation runs without asking
  Natural rhythm: learn rapidly → dream when full → resume
```

---

## Code Changes Required

### 1. SmartMemory: Decouple learn from consolidate

**Current** (`smart.py:70-84`):
```python
async def remember(self, content, tags=None, category="knowledge"):
    await self._system.begin_session()
    result = await self._system.learn(content, tags=tags, category=category)
    if result.status == "created":
        self._pending += 1
    if self._pending >= self._threshold:
        await self._system.consolidate()  # ← BLOCKS!
        self._pending = 0
    return result
```

**Proposed**:
```python
async def remember(self, content, tags=None, category="knowledge"):
    """Fast-path learn. Never blocks for consolidation."""
    await self._system.begin_session()
    result = await self._system.learn(content, tags=tags, category=category)
    if result.status == "created":
        self._pending += 1
    return result

@property
def should_dream(self) -> bool:
    """True when consolidation is advisable."""
    return self._pending >= self._threshold

async def dream(self) -> ConsolidateResult | None:
    """Consolidate pending knowledge. The 'dreaming' phase.

    Call at natural transition points or when should_dream is True.
    Returns None if nothing to consolidate.
    """
    if self._pending == 0:
        return None
    result = await self._system.consolidate()
    self._pending = 0
    return result
```

### 2. MCP: Add dream tool, make remember advisory

**elfmem_remember** returns advisory when inbox is filling:
```python
@mcp.tool()
async def elfmem_remember(content, tags=None):
    """Store knowledge instantly. Returns in milliseconds.

    Content is captured in the inbox. Call elfmem_dream() at natural
    transition points to integrate pending items into long-term memory.
    """
    mem = _mem()
    result = await mem.remember(content, tags=tags)
    response = result.to_dict()

    if mem.should_dream:
        response["dream_suggested"] = True
        response["suggestion"] = (
            "Inbox full. Call elfmem_dream() to consolidate "
            "pending knowledge into long-term memory."
        )

    return response
```

**New: elfmem_dream** — explicit consolidation:
```python
@mcp.tool()
async def elfmem_dream() -> dict:
    """Consolidate pending knowledge into long-term memory.

    This is the 'dreaming' phase — deep processing of items learned
    since the last consolidation. Embeds content, detects duplicates,
    builds knowledge graph connections, and promotes to active memory.

    Takes longer than learn (seconds, not milliseconds). This is by
    design: deep integration requires deep processing.

    Call at natural transition points: task completion, domain switch,
    session end. Or whenever elfmem_remember suggests it.
    """
    result = await _mem().dream()
    if result is None:
        return {"status": "nothing_to_consolidate", "inbox_count": 0}
    return result.to_dict()
```

### 3. CLI: Add dream command

```python
@app.command()
def dream(
    db: str | None = None,
    config: str | None = None,
    json_output: bool = False,
) -> None:
    """Consolidate pending knowledge — the 'dreaming' phase.

    Processes all inbox items: embeds, deduplicates, builds connections,
    promotes to active memory. Call at natural pauses in your workflow.
    """
    result = _run(_dream(_resolve_db(db), _resolve_config(config)))
    _json(result.to_dict()) if json_output else typer.echo(str(result))
```

### 4. Session context manager: dream on exit (not inline)

**Current** (`api.py:337-347`):
```python
async with system.session():
    # ... operations ...
# On exit: consolidates if inbox >= threshold
```

**Proposed** — same behavior, but consolidation happens at session boundary (exit), not mid-session:
```python
async with system.session():
    # ... operations ...
    # learn() NEVER blocks for consolidation
# On exit: dreams if inbox has pending items (natural transition)
```

---

## Consolidation Optimisation

### Problem: O(n × m) Contradiction Detection

The current consolidation calls `llm.detect_contradiction()` for every `(inbox_block, active_block)` pair. With 22 inbox and 100 active, that's 2,200 LLM calls.

### Solution: Pre-filter by Embedding Similarity

Only check contradiction for blocks that are semantically similar (high cosine similarity but potentially conflicting). Most block pairs are unrelated and can never contradict.

```python
# Current: O(n × m) LLM calls
for inbox_block in inbox:
    for active_block in active_blocks:
        score = await llm.detect_contradiction(inbox_block, active_block)

# Proposed: O(n × k) LLM calls where k << m
CONTRADICTION_SIMILARITY_FLOOR = 0.40  # Only check if similar enough

for inbox_block in inbox:
    # Pre-filter: only check semantically similar blocks
    candidates = [
        (a_block, sim) for a_block, a_vec in active_vecs
        if (sim := cosine_similarity(inbox_vec, a_vec)) >= CONTRADICTION_SIMILARITY_FLOOR
    ]
    # Now only k candidates instead of m
    for a_block, sim in candidates:
        score = await llm.detect_contradiction(inbox_block, a_block)
```

**Expected reduction:** From 2,200 to ~50-100 LLM calls (95% reduction).

### Solution: Batch Embeddings

Embed all texts in a single batch API call instead of sequential calls:

```python
# Current: N sequential API calls
for block in inbox:
    vec = await embedding_svc.embed(block["content"])

# Proposed: 1-2 batch API calls
texts = [b["content"] for b in inbox]
vecs = await embedding_svc.embed_batch(texts)  # Single API call
```

**Expected reduction:** From 122 sequential calls to 2-3 batch calls.

---

## The Harmony Model

The system achieves balance through three rhythms:

### Heartbeat (Learn)
```
Frequency:  Every few seconds (whenever agent discovers something)
Cost:       Milliseconds (pure database)
Volume:     High (many small items)
Processing: None (raw capture only)
```

### Breathing (Dream)
```
Frequency:  Every 10-50 items, or at natural pauses
Cost:       5-60 seconds (LLM + embedding calls)
Volume:     Batch (all pending items at once)
Processing: Deep (embed, dedup, connect, score, promote)
```

### Sleep (Curate)
```
Frequency:  Every 40 active hours, or on request
Cost:       1-5 seconds (database operations)
Volume:     Full scan (all active blocks)
Processing: Maintenance (archive, prune, reinforce)
```

### The Natural Rhythm

```
Learn learn learn learn learn learn learn learn learn learn
                                                          │
                                              inbox full ─┘
                                                          │
                                                     DREAM ←── natural pause
                                                          │
Learn learn learn learn learn learn learn learn learn learn
                                                          │
                                              inbox full ─┘
                                                          │
                                                     DREAM
                                                     CURATE ←── interval elapsed
                                                          │
Learn learn learn learn learn learn learn learn learn learn
```

**The agent is never interrupted.** Learning flows continuously. Dreaming happens at the agent's chosen pace. The system breathes.

---

## Configuration

```yaml
memory:
  inbox_threshold: 10           # Dream when inbox reaches this

consolidation:
  autonomous: false             # If true, dream without asking
  ask_before: true              # If true, suggest rather than auto-trigger
  max_hours_without: 8.0        # Safety net: dream at least every 8 hours
  urgency_ratio: 0.8            # Suggest at 80% of inbox threshold
  contradiction_similarity_floor: 0.40  # Only check contradiction if similar

  # Batch processing
  embed_batch_size: 20          # Embed up to 20 texts per API call
```

---

## What Changes for the Agent

### Before (Current)
```
Agent: elfmem_remember("API timeout under load")
  → ... waits 2-3 seconds (may trigger consolidation) ...
  → {"status": "created", "block_id": "abc123"}

Agent: elfmem_remember("Connection pooling helps")
  → ... waits 30+ seconds (consolidation triggered!) ...
  → {"status": "created", "block_id": "def456"}
```

### After (Proposed)
```
Agent: elfmem_remember("API timeout under load")
  → instant
  → {"status": "created", "block_id": "abc123"}

Agent: elfmem_remember("Connection pooling helps")
  → instant
  → {"status": "created", "block_id": "def456"}

... 8 more learns, all instant ...

Agent: elfmem_remember("Max connections = 100")
  → instant
  → {"status": "created", "block_id": "ghi789",
     "dream_suggested": true,
     "suggestion": "Inbox full. Call elfmem_dream() to consolidate."}

Agent: (at natural pause in task)
  elfmem_dream()
  → ... 15-30 seconds of deep processing ...
  → {"processed": 10, "promoted": 9, "deduplicated": 1, "edges_created": 7}
```

**The agent stays in flow.** It learns rapidly. When it reaches a natural pause, it dreams. Knowledge integrates deeply. Then it resumes.

---

## Summary

### What Changes
1. **SmartMemory.remember()** — Remove inline consolidation. Never blocks.
2. **SmartMemory.dream()** — New method. Explicit consolidation.
3. **elfmem_remember MCP tool** — Returns advisory when inbox filling.
4. **elfmem_dream MCP tool** — New tool. Agent calls at natural pauses.
5. **elfmem dream CLI command** — New command for manual consolidation.
6. **ConsolidationPolicy** — SELF-driven timing for autonomous mode.
7. **Contradiction detection** — Pre-filter by similarity (95% fewer LLM calls).
8. **Embedding** — Batch API calls (5x fewer network round-trips).

### What Stays the Same
1. **learn()** — Already fast. No changes needed.
2. **consolidate()** — Same pipeline, just called differently.
3. **curate()** — Same. Triggered after consolidation as before.
4. **recall()/frame()** — Same. Only sees ACTIVE blocks (consolidated).
5. **INBOX items** — Not retrievable until dreamed. This is correct.

### The Philosophical Shift
- **Before:** "Learn and maybe consolidate" (coupled, unpredictable timing)
- **After:** "Learn fast, dream deep" (decoupled, agent-controlled timing)

The system breathes. Learn is the inhale — rapid, light, continuous. Dream is the exhale — deep, integrative, at the agent's natural rhythm. Neither dominates. Both serve the whole.

---

## Implementation Order

1. **Decouple SmartMemory** — Remove auto-consolidation from remember()
2. **Add dream() method** — To SmartMemory
3. **Add elfmem_dream MCP tool** — And update elfmem_remember to be advisory
4. **Add dream CLI command** — For manual use
5. **Pre-filter contradictions** — Similarity floor for O(n×k) instead of O(n×m)
6. **Batch embeddings** — embed_batch() for bulk processing
7. **ConsolidationPolicy** — SELF-driven autonomous timing
8. **Update session() context manager** — Dream on exit if pending

Steps 1-4 are the core architectural change. Steps 5-6 are performance optimisations. Step 7 is the autonomous intelligence. Step 8 maintains backward compatibility for library users.
