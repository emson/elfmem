# Playground: Lifecycle — learn → consolidate → curate → recall

## Status: Draft

## Subsystem Specification

The lifecycle is the four-operation flow that all knowledge goes through.
Operations are **vertical slices** at L4 — each orchestrates across all layers.

```
learn()        → adds block to inbox (fast, no embeddings, instant dedup by hash)
consolidate()  → promotes inbox blocks to active (embeddings, LLM scoring, dedup, graph)
curate()       → maintenance pass (decay prune, contradiction resolution, edge maintenance)
recall()       → retrieval with reinforcement side-effect (runs through frame pipeline)
```

### State Machine

```
inbox → active → archived
              ↗ (promoted at consolidate)
  archived:
    archive_reason: "decayed" | "superseded" | "forgotten"
```

### Trigger Conditions

| Operation | Auto-trigger condition |
|-----------|----------------------|
| `consolidate()` | inbox count ≥ `inbox_threshold` (default: 10), OR `end_session()` |
| `curate()` | elapsed active hours ≥ `curate_interval_hours` (default: 40), OR `begin_session()` |
| `learn()` | explicit call by agent |
| `recall()` | explicit call (via `frame()`) |

---

## Parameters

```yaml
inbox_threshold: 10               # consolidate when inbox reaches this
curate_interval_hours: 40.0       # active hours between curates
prune_threshold: 0.05             # decay_weight below this → archived
self_alignment_threshold: 0.70    # minimum to promote to SELF block
contradiction_threshold: 0.80     # minimum to flag as contradiction
near_dup_exact_threshold: 0.95    # reject (hash check handles exact)
near_dup_near_threshold:  0.90    # forget + create + inherit
similarity_edge_threshold: 0.60   # minimum similarity to create edge
edge_degree_cap: 10               # max edges per block
```

---

## Test Suite

### TC-L-001: learn() Adds Block to Inbox (Not Active)

**Purpose:** `learn()` must be fast — no embeddings, no LLM calls. Block lands
in inbox with `status=inbox`.

**Given:** Empty memory system

**When:**
```python
system.learn("I prefer async patterns in Python when possible.")
```

**Then:**
- Inbox count: 1
- Active count: 0
- Block has `status=inbox`
- Block has content hash (computed synchronously)
- No embedding stored yet

**Expected:** Block in inbox, no embedding, no active block
**Status:** NOT YET RUN

---

### TC-L-002: Exact Duplicate Rejected at learn()

**Purpose:** Content-hash dedup at learn() rejects exact duplicates instantly,
before inbox even receives them.

**Given:** Block "I prefer async patterns in Python when possible." already in inbox

**When:**
```python
system.learn("I prefer async patterns in Python when possible.")  # exact same content
```

**Then:**
- Inbox count: still 1 (no new block added)
- No error raised — silent rejection

**Expected:** Silent rejection; inbox unchanged
**Status:** NOT YET RUN

---

### TC-L-003: consolidate() Triggered When Inbox Threshold Reached

**Purpose:** After `inbox_threshold` learn() calls, consolidate() runs automatically.

**Given:** `inbox_threshold=10`

**When:** 10 distinct blocks are added via `learn()`

**Then:**
- `consolidate()` triggered automatically on the 10th learn()
- OR, depending on implementation: consolidate is queued and runs at `end_session()`
- After consolidate: inbox empty, blocks promoted to active (minus duplicates/rejects)

**Expected:** Inbox empties; blocks promoted to active
**Status:** NOT YET RUN

---

### TC-L-004: consolidate() Processes Self-Alignment

**Purpose:** consolidate() calls LLM to score alignment; high-scoring blocks get self/* tags.

**Given:**
```yaml
inbox_block:
  content: "I always explain my reasoning before giving recommendations."
self_context: "[agent's current SELF frame text]"
# MockLLMService returns: alignment_score=0.85
```

**When:** `consolidate()` runs on this block

**Then:**
- Block receives `self_alignment=0.85`
- Since 0.85 ≥ `self_alignment_threshold=0.70`, LLM tag inference runs
- MockLLMService returns `["self/style"]`
- Block tagged `self/style`
- Block's effective λ = 0.001 (durable tier, from `self/style` tag)
- Block promoted to `status=active`

**Expected:** Block active with `self/style` tag and durable decay
**Status:** NOT YET RUN

---

### TC-L-005: Near-Duplicate at consolidate() — Forget + Create + Inherit

**Purpose:** New block (0.90–0.95 similarity to existing block) triggers the
near-duplicate resolution: old block archived as superseded, new block inherits
reinforcement history.

**Given:**
```yaml
existing_active_block:
  id: "abc123"
  content: "Use celery for background tasks in Django."
  reinforcement_count: 8
  confidence: 0.75
  status: active

new_inbox_block:
  content: "Use Celery 5+ with redis broker for background tasks in Django."
  # similarity to abc123 = 0.92 (near-duplicate range: 0.90–0.95)
```

**When:** `consolidate()` processes new inbox block

**Then:**
- Old block `abc123`: `status=archived`, `archive_reason=superseded`
- New block: `status=active`, `reinforcement_count=8` (inherited), `confidence=0.75` (inherited)
- Edge from old block to new block created (for graph continuity)

**Expected:** Old archived, new active with inherited metadata
**Status:** NOT YET RUN

---

### TC-L-006: Very High Similarity Block Rejected Silently

**Purpose:** Blocks above `near_dup_exact_threshold=0.95` are rejected silently —
they don't add enough new information to justify creating a new block.

**Given:**
```yaml
existing_active_block:
  content: "Use async/await in Python for I/O-bound tasks."
  status: active

inbox_block:
  content: "Use async/await in Python for I/O-bound task handling."
  # similarity = 0.97 (above 0.95 threshold)
```

**When:** `consolidate()` processes inbox block

**Then:**
- Inbox block rejected; not promoted to active
- Existing block unchanged
- No error raised

**Expected:** Silent rejection; no new block created
**Status:** NOT YET RUN

---

### TC-L-007: curate() Archives Decayed Blocks

**Purpose:** `curate()` archives blocks with `recency < prune_threshold`.

**Given:**
```yaml
block_A:
  status: active
  decay_tier: standard     # λ=0.010
  last_reinforced_at: 0    # very old
  total_active_hours: 400  # recency = e^(-4.0) = 0.018 < 0.05

block_B:
  status: active
  decay_tier: standard
  last_reinforced_at: 380  # recently reinforced
  total_active_hours: 400  # recency = e^(-0.2) = 0.819 — healthy
```

**When:** `curate()` runs at `total_active_hours=400`

**Then:**
- Block A: `status=archived`, `archive_reason=decayed`
- Block B: unchanged, still active

**Expected:** A archived, B untouched
**Status:** NOT YET RUN

---

### TC-L-008: curate() Reinforces Top-Scoring Active Blocks

**Purpose:** `curate()` reinforces the top N blocks by composite score to prevent
useful-but-unretrieved blocks from decaying between explicit recalls.

**Given:** 10 active blocks; 3 with high composite scores have not been explicitly
recalled in the current curate interval.

**When:** `curate()` runs with `curate_reinforce_top_n=3`

**Then:**
- Top 3 blocks by score have `last_reinforced_at` updated to `total_active_hours`
- Their `recency` resets toward 1.0

**Expected:** Top 3 blocks reinforced; others unchanged
**Status:** NOT YET RUN

---

### TC-L-009: recall() via frame() Reinforces Returned Blocks

**Purpose:** `frame()` is the primary retrieval interface; it must reinforce returned
blocks as a side effect (retrieval is a memory event).

**Given:**
```yaml
block_X:
  status: active
  last_reinforced_at: 100
  total_active_hours: 150  # hours_since = 50

query: "async patterns in Python"
```

**When:** `system.frame("attention", query="async patterns in Python")` returns `block_X`

**Then:**
- `block_X.last_reinforced_at` updated to 150 (current `total_active_hours`)
- `block_X.reinforcement_count` incremented by 1
- Returned `FrameResult` contains block X in `.blocks`

**Expected:** Block reinforced; `last_reinforced_at=150`; `reinforcement_count` +1
**Status:** NOT YET RUN

---

### TC-L-010: frame() Does Not Reinforce Blocks Not Returned

**Purpose:** Reinforcement is selective — only blocks actually returned in the
top-K get reinforced, not all candidates evaluated.

**Given:** 20 active blocks; query returns top-5

**When:** `system.frame("attention", query="...") → top-5 blocks`

**Then:**
- 5 returned blocks: `reinforcement_count` incremented
- 15 non-returned blocks: `reinforcement_count` unchanged

**Expected:** Exactly 5 blocks reinforced
**Status:** NOT YET RUN

---

### TC-L-011: begin_session() Triggers curate() When Due

**Purpose:** `begin_session()` runs `curate()` if `elapsed_active_hours ≥ curate_interval_hours`.

**Given:**
```yaml
last_curate_at: 0           # curate ran at hour 0
total_active_hours: 45      # 45 active hours have elapsed
curate_interval_hours: 40   # interval is 40h → curate is due
```

**When:** `system.begin_session()`

**Then:** `curate()` runs automatically before the session begins

**Expected:** curate() called; no manual trigger needed
**Status:** NOT YET RUN

---

### TC-L-012: end_session() Triggers consolidate() Regardless of Inbox Size

**Purpose:** `end_session()` always consolidates inbox contents, even if below `inbox_threshold`.
The agent shouldn't leave unprocessed knowledge when a session ends.

**Given:**
```yaml
inbox_count: 3       # below inbox_threshold=10
```

**When:** `system.end_session()`

**Then:** `consolidate()` runs on the 3 inbox blocks

**Expected:** All 3 inbox blocks processed; inbox empty after end_session
**Status:** NOT YET RUN

---

### TC-L-013: learn() Returns Block ID for Downstream Use

**Purpose:** `learn()` should return the block ID immediately so the agent can
reference it (e.g., for explicit tagging or linking).

**Given:** Fresh memory system

**When:**
```python
block_id = await system.learn("New insight about caching strategies.")
```

**Then:** `block_id` is a 16-character hex string (content-hash based)

**Expected:** Returns valid block ID string
**Status:** NOT YET RUN

---

### TC-L-014: consolidate() Does Not Run on Empty Inbox

**Purpose:** If inbox is empty, `consolidate()` is a no-op. No LLM calls, no embeddings.

**Given:** `inbox_count = 0`

**When:** `system.consolidate()` called explicitly

**Then:**
- No LLM calls made (MockLLMService records 0 calls)
- No embedding calls made
- No database writes

**Expected:** Silent no-op; zero external calls
**Status:** NOT YET RUN

---

## Parameter Tuning

### PT-1: inbox_threshold (currently 10)

**Question:** Is 10 the right threshold before auto-consolidation?

**Tradeoff:**
- Too low (5): Frequent consolidation, more LLM calls, but faster knowledge promotion
- Too high (20): Inbox stays unprocessed longer; potential stale context in retrieval

**Scenario:** Run 3 agents with thresholds 5, 10, 20 over 100 learn() calls. Compare:
- Total LLM calls (cost)
- Average time from learn() to active (latency)
- Retrieval quality (does fresh knowledge appear in frames?)

**Recommendation:** Default 10 is reasonable. Lower to 5 for high-volume agents;
raise to 20 for cost-sensitive deployments.

---

### PT-2: curate_interval_hours (currently 40)

**Question:** Is 40 active hours the right maintenance interval?

**Scenario:** At 4h/session, 40h = 10 sessions ≈ weekly maintenance for a daily-use agent.

**Tradeoff:**
- Too frequent: Premature pruning of blocks that would have been reinforced soon
- Too infrequent: Stale blocks accumulate, degrading retrieval signal

**Recommendation:** Keep 40h as default. For agents with irregular usage, a lower
interval (20h) prevents accumulation.

---

### PT-3: near_dup_near_threshold (currently 0.90)

**Question:** Is 0.90 the right boundary for near-duplicate resolution?

**Scenario:**
- At 0.90: Most updates to existing knowledge trigger the forget+create+inherit path
- At 0.85: More aggressive; minor paraphrasing of the same concept triggers resolution
- At 0.92: More permissive; only very close paraphrases trigger resolution

**Key concern:** If set too low (0.85), a block about "async" and one about "await"
(both Python concurrency) might incorrectly trigger near-duplicate resolution.

**Test:** Sample 20 block pairs at various similarities (0.85–0.95). Does the
resolution correctly identify updates vs. distinct concepts?

---

### PT-4: self_alignment_threshold (currently 0.70)

**Question:** Is 0.70 the right threshold for SELF-tag promotion?

**Scenario:** At 0.70:
- Blocks with moderate identity relevance get self/* tags
- Risk: too permissive; many non-identity blocks accumulate self/* tags

At 0.75 (stricter):
- Only clearly identity-relevant blocks get tagged
- Risk: some valid identity blocks missed

**Recommendation:** 0.70 as default. If SELF frame becomes noisy with tangentially-
relevant blocks, raise to 0.75.

---

## Open Assertions

1. `learn()` always returns a block ID (never None)
2. `learn()` never blocks on I/O (no embeddings, no LLM calls)
3. Exact duplicates are rejected before inbox insertion (O(1) hash check)
4. `consolidate()` on N blocks makes at most N×3 LLM calls (alignment + tags + contradiction)
5. A block's `status` only transitions forward: inbox → active → archived (never backward)
6. `archive_reason` is always set when `status=archived`
7. `recall()` / `frame()` never return `status=inbox` blocks

---

## Python Test Sketch

```python
# elfmem/tests/test_lifecycle.py

import pytest
from elfmem import MemorySystem
from tests.fixtures import MockLLMService, MockEmbeddingService

@pytest.fixture
def system():
    return MemorySystem(
        db_path=":memory:",
        llm_service=MockLLMService(),
        embedding_service=MockEmbeddingService(),
    )

async def test_learn_adds_to_inbox_not_active(system):
    block_id = await system.learn("I prefer async patterns.")
    assert system.inbox_count() == 1
    assert system.active_count() == 0
    assert block_id is not None

async def test_exact_duplicate_rejected(system):
    await system.learn("I prefer async patterns.")
    await system.learn("I prefer async patterns.")  # exact duplicate
    assert system.inbox_count() == 1  # still 1, not 2

async def test_consolidate_promotes_to_active(system):
    await system.learn("I prefer async patterns.")
    await system.consolidate()
    assert system.inbox_count() == 0
    assert system.active_count() == 1

async def test_end_session_consolidates_inbox(system):
    await system.learn("Knowledge 1")
    await system.learn("Knowledge 2")
    await system.learn("Knowledge 3")
    # inbox has 3 items, below threshold of 10
    await system.end_session()
    assert system.inbox_count() == 0
    assert system.active_count() == 3

async def test_recall_reinforces_returned_blocks(system):
    await system.learn("async await Python")
    await system.consolidate()
    block_before = system.get_active_blocks()[0]
    count_before = block_before.reinforcement_count

    await system.frame("attention", query="async patterns")
    block_after = system.get_block(block_before.id)

    assert block_after.reinforcement_count == count_before + 1
```
