# Title: Context Frames — API Design and Call Semantics

## Status: complete

## Question

Context frames are built from specific collections of blocks filtered and scored
around a particular context. How should the library expose them?

- Should a call return a rendered system prompt string — ready to inject into an LLM?
- Or raw blocks — ordered by score, caller assembles?
- Some frames (SELF) are used internally by the library. What does that mean for the API?
- What are the different frame types, and what makes each distinct?

Reason through the uses, the consumers, and the right API contract.

---

## What Is a Context Frame?

Before designing the API, define the concept precisely.

A **context frame** is a named retrieval configuration with four properties:

| Property | What it defines |
|----------|----------------|
| **Scoring weights** | How the five scoring components (Recency, Centrality, Confidence, Similarity, Reinforcement) are weighted for this frame |
| **Filter** | Which blocks are eligible candidates (all blocks, only self-tagged, only goal-tagged, etc.) |
| **Template** | How the selected blocks are rendered into a string for LLM consumption |
| **Token budget** | Hard upper bound on rendered output size |

Two frames with the same blocks in memory will return different results because their
scoring weights emphasise different signals. SELF emphasises confidence and reinforcement
(stable, well-established identity). ATTENTION emphasises similarity and recency
(what's most relevant to the current question right now).

---

## The Three Named Frames

### `self` frame

**Purpose:** Who am I? What are my values, constraints, and preferences?
Used as the identity layer in every LLM system prompt.

```
Filter:    blocks with any self/* tag
Scoring:   0.05×Recency + 0.25×Centrality + 0.30×Confidence + 0.10×Similarity + 0.30×Reinforcement
Query:     optional (see below)
Template:  instruction-style ("You prefer...", "You never...")
Budget:    600 tokens (exploration 006)
Special:   constitutional blocks guaranteed included, bypass scoring (exploration 007)
           cached per task-type with TTL + event invalidation (exploration 006)
```

When no query is provided: similarity component is zeroed; remaining weights
renormalized to sum to 1.0. This is "who am I?" — identity without a specific topic.

When a query IS provided: "What are my values around X?" — self-tagged blocks most
relevant to that specific topic surface. Useful for targeted self-reflection before
a decision.

### `attention` frame

**Purpose:** What do I know that's relevant to this question?
Used to augment LLM responses with relevant stored knowledge.

```
Filter:    all blocks (no tag restriction)
Scoring:   0.25×Recency + 0.15×Centrality + 0.15×Confidence + 0.35×Similarity + 0.10×Reinforcement
Query:     should be provided; without it, similarity = 0 and frame returns "most salient" blocks
Template:  markdown block list (## Title\nBody\n\n)
Budget:    2000 tokens (default; caller can override)
Special:   no caching — query-specific results must be fresh per call
```

The query is "should" rather than "must" because a queryless ATTENTION frame has a
meaningful interpretation: "what's most on my mind right now?" (high recency + high
reinforcement + high centrality). This surfaces recently-active knowledge without
a specific topic. Useful for session initialisation or unprompted synthesis.

### `task` frame

**Purpose:** What goals and relevant knowledge apply to this specific task?
Used as a task preamble before executing a specific action.

```
Filter:    blocks tagged self/goal (always) + all blocks scored by similarity (if query provided)
           → union of goal blocks + query-relevant knowledge blocks
Scoring:   0.20×Recency + 0.20×Centrality + 0.20×Confidence + 0.20×Similarity + 0.20×Reinforcement
Query:     optional (without query, returns only goal-tagged blocks)
Template:  structured sections ("## Active Goals\n\n## Relevant Context\n")
Budget:    800 tokens (default)
Special:   goal blocks are always included if present (similar to constitutional for SELF);
           non-goal blocks fill remaining budget by score
```

The equal scoring weights are intentional: tasks require balanced awareness — what's
recent, what's stable, what's relevant right now — not any single dominant signal.

---

## The Core API Design Question

Three types of consumers need context frames:

| Consumer | What they want | Example |
|----------|---------------|---------|
| **Application developer** | Plug-and-play: a string to inject | `system_prompt = memory.frame("self").text` |
| **Power user / integration** | Raw blocks for custom assembly | `blocks = memory.recall(frame="attention", query=q)` |
| **Library internals** | Raw blocks without side effects | consolidate() checking self-alignment |

A single function cannot serve all three cleanly. The API needs two layers.

### The Tension: Raw Blocks vs. Rendered String

**Case for raw blocks only:**
- Caller may want to filter, sort, or augment before rendering
- Caller may have their own template/prompt system
- Internal library calls need blocks, not text
- Extensible — new rendering formats don't require library changes

**Case for rendered string:**
- SELF frame IS a system prompt — you always want the string
- Rendering format is part of the frame identity; the library knows best
- External developers should not need to know scoring internals

**The resolution: two-layer API with a convenience function on top.**

---

## API Design

### Layer 1: `recall()` — retrieval only

```python
blocks = memory.recall(
    frame="self" | "attention" | "task",
    query=None,     # str | None
    top_k=5
) → List[ScoredBlock]
```

Returns raw `ScoredBlock` objects with scores. Always reinforces returned blocks.
Contradiction suppression runs inside recall() — the caller always receives a
contradiction-free list. No rendering.

**When to use:** When you want to inspect, filter, or augment blocks before
deciding what to do with them. Also useful as a building block for custom
prompt assembly.

### Layer 2: `render()` — presentation only

```python
text = memory.render(
    blocks: List[ScoredBlock],
    template="self" | "attention" | "task",
    token_budget=None   # int | None — uses frame default if None
) → str
```

Takes any block list and renders it to a string. No retrieval, no side effects,
no reinforcement. Applies the frame's template and truncates to token budget if
needed (dropping from lowest-scored blocks downward).

**When to use:** When you already have blocks (from recall() or elsewhere)
and want to format them for injection.

### Convenience: `frame()` — combined

```python
result = memory.frame(
    name="self" | "attention" | "task",
    query=None,         # str | None
    top_k=5,
    token_budget=None   # override frame default
) → FrameResult
```

The **primary interface for most callers**. Combines recall() + render() in one call.
Returns a `FrameResult` object:

```python
@dataclass
class FrameResult:
    text:        str               # rendered string, ready for LLM injection
    blocks:      List[ScoredBlock] # raw blocks for inspection or logging
    token_count: int               # actual tokens in text
    frame_name:  str               # "self" | "attention" | "task"
    query:       str | None        # query used (None if not applicable)
```

`result.text` is the primary artifact — the caller injects this into their prompt.
`result.blocks` is available for inspection, debugging, or custom post-processing.

**When to use:** The default. Use this unless you have a specific reason to
separate retrieval from rendering.

---

## Internal Use: Reinforcement-Free Calls

`recall()` and `frame()` always reinforce returned blocks. This is correct for
external retrieval — the act of retrieval IS the reinforcement event (exploration 008).

But library-internal calls must NOT reinforce. When `consolidate()` calls
`recall(frame="self")` to get the SELF context for self-alignment scoring,
that should not count as a reinforcement event. The agent isn't using those
blocks — the system is inspecting them.

**Solution: private `_recall()` method, identical to `recall()` but does not reinforce.**

```python
# Used inside consolidate(), curate(), and other library internals:
self_blocks = memory._recall(frame="self")   # no reinforcement
self_text = memory.render(self_blocks, template="self")
# → pass to LLM for self-alignment scoring of new block
```

This is the only way the library should call into its own retrieval. External
callers never need `_recall()` directly.

---

## Contradiction Suppression: Where It Happens

From exploration 014: if block A and block B are in the top-K but connected by
an `opposes` edge in the `contradictions` table, the lower-confidence block is
suppressed before the frame is returned.

**This happens inside `recall()` and `_recall()` — before blocks are returned.**

The consequence: by the time `frame()` or `render()` receives blocks, the list is
already contradiction-free. `render()` never needs to check for contradictions.
Neither does any caller.

```
recall() pipeline:
  1. Score all eligible blocks
  2. Take top-(K × 2) candidates (extra candidates for suppression headroom)
  3. Check contradictions table for any pairs in the candidate set
  4. For each contradicting pair: keep higher-confidence block, drop lower
  5. Return top-K from the surviving candidates
  6. Reinforce all returned blocks
```

The "extra candidates" in step 2 ensures that suppressing a contradicting block
doesn't leave the frame short. If we ask for top-5 and 2 blocks are suppressed,
we still return 5 blocks total.

---

## Caching: SELF Frame Only

The SELF frame is cached (exploration 006). The other frames are not.

**Why SELF is cached:**
- Constitutional blocks change only via the formal amendment process (rare)
- Variable self blocks change via curate() (infrequent)
- In a typical session, the identity doesn't change mid-session
- The same system prompt at the start of a session should persist until an
  invalidating event occurs

**Why ATTENTION and TASK are not cached:**
- Query-specific — every different query produces a different set of results
- Designed to be fresh — recency and current reinforcement state matter
- Caching a query-specific result would require keying the cache by query,
  which is effectively re-running the query to check the key

The `frame()` call for `self` automatically uses the cache. Cache invalidation
triggers remain as defined in exploration 006:
- New self-tagged block consolidated into MEMORY
- Self-tagged block pruned by curate()
- Self-tag added or confirmed
- Constitutional amendment
- `cache_ttl` expires (default: 60 minutes)
- Explicit `memory.invalidate_self_cache()` call

---

## Frame Composition: Library vs. Caller Responsibility

The library handles each frame independently. The library does NOT compose frames
into a complete system prompt.

**Why:** Application developers have different system prompt structures. Some put
identity at the top; some at the bottom. Some inject task context into the system
prompt; some inject it into the user message. The library should not impose one.

**The caller composes frames:**

```python
# Typical session start:
self_context = memory.frame("self")

system_prompt = f"""
{self_context.text}

Respond in the language of the user's message.
"""

# At each query:
question = "How do I handle concurrent database writes?"
knowledge = memory.frame("attention", query=question)

user_message_augmented = f"""
{question}

## Relevant context
{knowledge.text}
"""
```

The library provides the pieces. The application assembles them.

---

## Token Budget Enforcement

Token budget is enforced inside `frame()` (and therefore inside `render()` when
called from `frame()`). The enforcement strategy:

1. Score and rank all eligible blocks
2. Greedily select from highest-scored downward
3. Stop when adding the next block would exceed the budget
4. The excluded block is not in the result — it's just not there

This is the same greedy selection from exploration 006. "Greedy from top" means
the most important blocks are always included; less important blocks are the ones
cut when the budget is tight.

Token counting is approximate (words × 1.3 factor) for Phase 1. Precise token
counting via a tokenizer is Phase 2.

---

## Worked Example: Complete Session Trace

### Setup
```yaml
self_blocks: [S1 "direct communication", S2 "evidence-first epistemic", S3 "no hedging"]
knowledge_blocks: [K1 "asyncio patterns", K2 "database connection pools", K3 "Python type hints"]
goal_blocks: [G1 "refactor auth module"]
```

### Step 1: Session start — get identity

```python
self_result = memory.frame("self")
```

Internally:
```
1. Pre-include constitutional blocks (none in this example)
2. Score S1, S2, S3 using SELF weights (no query → similarity = 0)
   S1: 0.05×0.9 + 0.25×0.7 + 0.30×0.85 + 0.00×0 + 0.30×0.8 = 0.787
   S2: 0.05×0.8 + 0.25×0.6 + 0.30×0.90 + 0.00×0 + 0.30×0.7 = 0.727
   S3: 0.05×0.7 + 0.25×0.5 + 0.30×0.80 + 0.00×0 + 0.30×0.6 = 0.655
3. Check contradictions table → none
4. Reinforce S1, S2, S3 (reinforcement_count += 1, hours reset)
5. Render with "self" template:
```

```
→ self_result.text:

   You communicate directly and without hedging. You back claims with
   evidence and prefer epistemic precision over confident-sounding guesses.
   You state what you know and what you don't.

→ self_result.blocks: [ScoredBlock(S1, 0.787), ScoredBlock(S2, 0.727), ScoredBlock(S3, 0.655)]
→ self_result.token_count: 42
```

(This result is now cached for the session.)

### Step 2: User query — get relevant knowledge

```python
question = "How do I handle concurrent database writes safely?"
knowledge_result = memory.frame("attention", query=question)
```

Internally:
```
1. Embed query: query_vec = embed("How do I handle concurrent database writes safely?")
2. Score all blocks using ATTENTION weights:
   K1 "asyncio patterns":        sim=0.41 → 0.25×0.9 + 0.15×0.7 + 0.15×0.8 + 0.35×0.41 + 0.10×0.7 = 0.621
   K2 "database connection pools": sim=0.78 → 0.25×0.8 + 0.15×0.6 + 0.15×0.75 + 0.35×0.78 + 0.10×0.6 = 0.720
   K3 "Python type hints":        sim=0.12 → 0.25×0.7 + 0.15×0.5 + 0.15×0.7 + 0.35×0.12 + 0.10×0.5 = 0.417
   S1 "direct communication":     sim=0.05 → ... (scores low, similarity dominates negatively)
3. top-3: K2 (0.720), K1 (0.621), K3 (0.417)
4. Check contradictions → none
5. Reinforce K2, K1, K3
6. Render with "attention" template:
```

```
→ knowledge_result.text:

   ## Database connection pools

   Connection pools maintain a set of reusable connections to the database.
   Under concurrent writes, use a pool with appropriate max_size to prevent
   connection exhaustion. Never hold a connection open across await boundaries.

   ## Python asyncio patterns

   Asyncio uses an event loop to schedule coroutines. Use async def to define
   coroutines and await to yield control. Blocking calls must be wrapped with
   asyncio.run_in_executor to avoid stalling the loop.

→ knowledge_result.token_count: 84 (K3 dropped — low score AND below token threshold)
```

### Step 3: Task context check

```python
task_result = memory.frame("task", query="concurrent database writes")
```

Internally:
```
1. Always include goal blocks: G1 "refactor auth module" → included
2. Score non-goal blocks by TASK weights with query similarity
3. K2 scores high (0.78 similarity to query) → included
4. Return: G1 (goal, guaranteed) + K2 (top relevant knowledge)
5. Render with "task" template:
```

```
→ task_result.text:

   ## Active Goals
   - Refactor auth module to reduce complexity

   ## Relevant Context
   - Database connection pools: pool max_size prevents connection exhaustion
     under concurrent writes.
```

### Step 4: Compose for LLM call

```python
# Application assembles all pieces:
system_prompt = self_result.text
full_user_message = f"{question}\n\n## Relevant context\n{knowledge_result.text}"

# LLM call:
response = llm.complete(system=system_prompt, user=full_user_message)
```

The library provided three independent pieces. The application decided how to compose them.

### Step 5: Internal use — consolidate() checking self-alignment

```python
# Inside consolidate() — NOT via frame(), NOT via recall():
self_blocks = memory._recall(frame="self")        # no reinforcement
self_text = memory.render(self_blocks, "self")    # same render, no side effects

alignment = await llm.score(
    f"Self-alignment score (0.0-1.0): does this block reflect the agent described?\n"
    f"Agent:\n{self_text}\n\nBlock:\n{new_block.content}"
)
```

The same SELF content is used for self-alignment scoring during consolidation, but
`_recall()` ensures it doesn't inflate reinforcement counts. The consolidation pass
is not a retrieval event — it's a maintenance pass.

---

## ScoredBlock Structure

```python
@dataclass
class ScoredBlock:
    id:                  str
    content:             str       # title + body (rendered markdown)
    tags:                List[str]
    category:            str
    score:               float     # composite score for this frame
    score_components:    dict      # {"recency": 0.05, "centrality": 0.25, ...}
    confidence:          float
    reinforcement_count: int
    decay_weight:        float
```

The `score_components` breakdown is for inspection and debugging. The composite
`score` is what drives ordering.

---

## Implementation Schema

```python
class MemorySystem:

    # ─── Public Interface ─────────────────────────────────────────────────

    def frame(
        self,
        name: Literal["self", "attention", "task"],
        query: str | None = None,
        top_k: int = 5,
        token_budget: int | None = None
    ) -> FrameResult:
        """Primary interface. Retrieves + renders. Returns FrameResult."""
        blocks = self.recall(name, query, top_k)
        budget = token_budget or FRAME_DEFAULTS[name]["token_budget"]
        text = self.render(blocks, name, budget)
        return FrameResult(text=text, blocks=blocks, ...)

    def recall(
        self,
        frame: Literal["self", "attention", "task"],
        query: str | None = None,
        top_k: int = 5
    ) -> List[ScoredBlock]:
        """Raw retrieval. Always reinforces returned blocks."""
        return self._recall(frame, query, top_k, reinforce=True)

    def render(
        self,
        blocks: List[ScoredBlock],
        template: Literal["self", "attention", "task"],
        token_budget: int | None = None
    ) -> str:
        """Renders block list to string. No retrieval, no side effects."""
        ...

    # ─── Internal Interface ───────────────────────────────────────────────

    def _recall(
        self,
        frame: Literal["self", "attention", "task"],
        query: str | None = None,
        top_k: int = 5,
        reinforce: bool = False    # always False for internal calls
    ) -> List[ScoredBlock]:
        """Core retrieval engine. Handles scoring, filtering, contradiction suppression."""
        ...
```

---

## Design Decisions from this Exploration

| Decision | Rationale |
|----------|-----------|
| `frame()` is the primary public interface → returns `FrameResult` | Single call gives `.text` for injection and `.blocks` for inspection |
| `recall()` is the secondary public interface → returns raw blocks | Power users and custom assembly pipelines |
| `render()` is a stateless utility → takes blocks, returns string | No retrieval logic; composable with any block source |
| Three named frames: `self`, `attention`, `task` | Each represents a distinct consumer purpose and scoring strategy |
| Query is optional for all frames | SELF + query enables targeted self-reflection; queryless ATTENTION returns "most salient" |
| When no query: similarity component = 0, remaining weights renormalized | Avoids meaningless zero-similarity comparisons polluting scores |
| Contradiction suppression inside `recall()` — before return | Caller always receives a contradiction-free list; no caller-side scanning needed |
| Extra candidates (top-K×2) before contradiction suppression | Ensures frame is never short when blocks are suppressed |
| `_recall(reinforce=False)` for internal library calls | Internal calls (consolidate, curate) are not retrieval events |
| SELF frame cached; ATTENTION and TASK frames not cached | SELF identity is session-stable; query-specific results must be fresh |
| Frame composition is caller's responsibility, not library's | Application developers have different prompt structures; library should not impose one |
| Token budget enforced in `frame()` — greedy from top | Most important blocks always included; less important ones cut |
| TASK frame guarantees goal blocks like SELF guarantees constitutional blocks | Goals always present regardless of score when task frame is requested |
| `FrameResult.text` is primary artifact; `.blocks` is secondary | The string is what goes into the LLM; blocks are for tooling and debug |

---

## Open Questions

- [ ] Should `frame()` support a `task_type` parameter for SELF (exploration 007's
      RESPONSE/CONSOLIDATION/PRUNING modifiers)? Probably yes — the SELF frame
      already supports task-type modifiers from exploration 007.
      `memory.frame("self", task_type="consolidation")` is the natural extension.
- [ ] Should `recall()` accept custom scoring weights for user-defined frames?
      (Phase 2 — enables domain-specific retrieval tuning)
- [ ] What is the right behaviour for ATTENTION when no blocks score above a
      minimum threshold? Return empty? Return top-K regardless?
      (Return top-K regardless — caller can check scores and decide)
- [ ] Should `render()` support a custom template string for power users?
      (Probably yes in Phase 2 — enables LLM-synthesized formatting vs. static template)
- [ ] Should there be a `world` frame for general world-knowledge blocks that are
      not self-relevant and not query-specific? (Phase 2)

---

## Variations

- [ ] What if `frame("self", query="testing")` is called — how does the result differ
      from `frame("self")` with no query? Work through a concrete example with scoring.
- [ ] What if `frame("attention")` is called with no query? Work through what "most
      salient right now" means in practice with a concrete block set.
- [ ] What if the task frame has no goal blocks at all (empty self/goal category)?
      How does the frame degrade gracefully?
- [ ] Should `FrameResult` include a `suppressed_blocks` list (blocks dropped due to
      contradiction suppression) for logging and debugging?
