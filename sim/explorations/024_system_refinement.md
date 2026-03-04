# Title: System Refinement — Audit, Unify, Simplify

## Status: complete

## Question

Twenty-three explorations have built AMGS from first principles. Each exploration
answered one question well. But the design accumulated organically — later
explorations sometimes contradict earlier ones, concepts duplicate across layers,
and special cases proliferate where general mechanisms would suffice.

Before implementation: audit the entire design for inconsistencies, redundancies,
and inelegance. Then refactor into the most robust, flexible, and elegant form.

---

## Part 1: Audit

### A1 — Scoring Formula Inconsistency

The composite scoring formula has **five** components in explorations 002, 003,
008, 015, 021:

```
score = w₁·similarity + w₂·confidence + w₃·recency + w₄·centrality + w₅·reinforcement
```

But exploration 023's worked example uses **four** components:

```
score = 0.35·sim + 0.25·conf + 0.20·decay_factor + 0.20·centrality
```

Reinforcement is missing. The weights don't match any established frame profile.
This is an error introduced in 023. The canonical formula has always been five
components.

**Resolution:** Five components. Always. The formula is:

```
score = w_sim·similarity + w_conf·confidence + w_rec·recency + w_cen·centrality + w_rein·reinforcement

where:
  similarity    = cosine(query_vec, block_vec)           [0.0 if no query]
  confidence    = block.confidence                        [0.0–1.0]
  recency       = exp(-λ × hours_since_reinforcement)    [0.0–1.0]
  centrality    = weighted_degree / max_degree            [0.0–1.0]
  reinforcement = log(1 + count) / log(1 + max_count)    [0.0–1.0]
```

All five signals are normalised to [0.0, 1.0]. Weights per frame sum to 1.0.

---

### A2 — Two Scoring Modules in Two Layers

Exploration 022 places scoring in both L2 and L3:
- `memory/scoring.py` — "score(block, weights) → float (pure function)"
- `context/scoring.py` — "Applies frame weights to ScoredBlock candidates"

These are the same operation. L2's pipeline already applies composite scoring in
Stage 4 (021). L3 shouldn't re-score — it should receive already-scored candidates
and assemble them.

**Resolution:** One scoring function. It lives in neither L2 nor L3 — it's a shared
utility. Both layers import it. No `context/scoring.py`.

---

### A3 — Constitutional Blocks Are a Special Case

Exploration 007 establishes constitutional blocks as guaranteed inclusion that
bypass scoring, with a pre-allocated budget (100 tokens). Exploration 011 replaces
`is_constitutional` with the `self/constitutional` tag. Exploration 016 introduces
`guarantee_tags` as a generic frame feature.

These three mechanisms overlap. The constitutional guarantee is just an instance
of `guarantee_tags=["self/constitutional"]` on the SELF frame. No special-case
code is needed.

**Resolution:** `guarantee_tags` is the sole guarantee mechanism. Constitutional
blocks are guaranteed via frame configuration, not via special-case logic in the
scoring pipeline.

---

### A4 — SELF Frame Caching Is Special-Cased

Exploration 015 hardcodes "SELF frame is cached; ATTENTION and TASK are not."
But if caching were a frame property, this falls out naturally.

**Resolution:** Every `FrameDefinition` has an optional `cache: CachePolicy | None`.
SELF sets `cache=CachePolicy(ttl_seconds=3600, ...)`. ATTENTION sets `cache=None`.
No special-case code.

---

### A5 — `_recall(reinforce=False)` Is a Leaky Abstraction

Exploration 015 introduces a private `_recall()` for internal library calls that
shouldn't reinforce blocks. This conflates two concerns: *finding blocks* (pure
retrieval) and *recording usage* (side effect).

**Resolution:** Retrieval is side-effect-free. Reinforcement is a separate step.
L4 decides whether to reinforce. No `_recall()` needed.

```python
# L2: pure retrieval — no side effects
candidates = pipeline.retrieve(query, filters, weights, conn, embedding_svc)

# L3: frame assembly — no side effects
result = frames.assemble(candidates, frame_def, contradictions)

# L4: side effects — only for external calls
if external_call:
    storage.reinforce(result.block_ids, conn)
```

Internal calls (consolidate checking self-alignment) simply skip the reinforce step.

---

### A6 — `hours_since_reinforcement` Is Stored and Bulk-Updated

The current design stores `hours_since_reinforcement` per block and bulk-updates
it at session boundaries. This creates stale data between updates and requires
a write-heavy maintenance pattern.

Session-aware decay (exploration 005) means the clock only ticks during active
sessions. This can be implemented with a global counter.

**Resolution:** Store `last_reinforced_at` as cumulative active hours. Compute
`hours_since_reinforcement` at query time.

```python
# System tracks one global counter
system_config.total_active_hours: float

# At session start:
session.start_active_hours = system.total_active_hours

# During session:
def current_active_hours(self) -> float:
    elapsed = (now() - self.current_session.started_at).total_seconds() / 3600
    return self.total_active_hours + elapsed

# At reinforcement:
block.last_reinforced_at = system.current_active_hours()

# At query time:
hours_since = system.current_active_hours() - block.last_reinforced_at
decay_weight = exp(-block.decay_lambda * hours_since)

# At session end:
duration = (session.ended_at - session.started_at).total_seconds() / 3600
system.total_active_hours += duration
```

Eliminates:
- The `hours_since_reinforcement` column (derived, not stored)
- Bulk UPDATE at session boundaries
- Stale-data risk between updates

The pre-filter `WHERE hours_since < search_window` becomes:
```sql
WHERE last_reinforced_at > (:current_active_hours - :search_window_hours)
```
Still index-friendly (single column comparison).

---

### A7 — Block Status Transitions Are Scattered

Block statuses appear across explorations 008, 009, 010 with terms like
"pending", "active", "superseded", "forgotten", "pruned". No single state
machine is defined.

**Resolution:** Three states with an archive reason.

```
inbox → active → archived

ArchiveReason:
  DECAYED       — pruned by curate() (decay_weight < threshold)
  SUPERSEDED    — replaced by near-duplicate (dedup in consolidate)
  FORGOTTEN     — explicit forget() by user
```

The retrieval pipeline only sees `status = 'active'`. Archive reasons are
metadata for audit, not for retrieval logic.

---

### A8 — Six Self-Tag Decay Rates Is Over-Specified

Exploration 011 defines six `self/*` categories with distinct λ values:
`constitutional` (0.00001), `constraint` (0.001), `value` (0.001),
`style` (0.01), `context` (0.03), `goal` (0.01).

Plus knowledge (0.01) and observations (0.03–0.1). That's 8+ decay rates.

**Resolution:** Four decay tiers.

| Tier | λ | Survives (approx) | Used for |
|------|---|-------------------|----------|
| permanent | 0.00001 | ~7,000 active hours | `self/constitutional` only |
| durable | 0.001 | ~700 active hours | Self blocks (`self/value`, `self/constraint`, `self/goal`) |
| standard | 0.01 | ~70 active hours | Knowledge, `self/style`, `self/context` |
| ephemeral | 0.05 | ~14 active hours | Observations, transient context |

The tag taxonomy remains (it controls frame filtering), but decay assignment
uses only four tiers. Tier is determined by the block's *highest-priority*
self tag, or by category if no self tags.

```python
def decay_tier(tags: list[str], category: str) -> float:
    if "self/constitutional" in tags:
        return 0.00001
    if any(t in tags for t in ["self/value", "self/constraint", "self/goal"]):
        return 0.001
    if category == "observation":
        return 0.05
    return 0.01  # standard: knowledge, self/style, self/context
```

---

### A9 — Dedup Thresholds Diverge

Exploration 008: `similarity > 0.92 → reject`.
Exploration 009: `> 0.95 exact (reject), 0.90–0.95 near (forget + create)`.

009 supersedes 008.

**Resolution:** Use 009's three-band model:
- `> 0.95`: exact duplicate → reject (don't even add to inbox)
- `0.90–0.95`: near duplicate → forget old, create new, inherit metadata
- `< 0.90`: distinct → keep both, create edge if ≥ 0.60

---

### A10 — Content Hash ID Simplifies Dedup

Exploration 010 decided block IDs are `sha256(normalized_content)[:16]`.
This means exact-duplicate detection is O(1) — just check if the ID already
exists. No embedding needed.

This should be applied at `learn()` time, not just consolidate(). If the hash
matches an active block, `learn()` can reject instantly. The "shallow dedup"
in exploration 008 (exact title match) is superseded by hash-based dedup.

**Resolution:** `learn()` computes content hash immediately. If the hash exists
in active blocks, reject silently. This is the fast-path dedup — zero cost.

---

## Part 2: Design Principles

The refactored design follows five principles:

**P1 — No special cases.** General mechanisms handle specific needs.
Constitutional guarantees, SELF caching, and queryless scoring all fall out
of configurable frame properties. No `if frame == "self"` in the pipeline.

**P2 — Computation over storage.** Values that can be derived from stored data
should be computed at query time, not stored and bulk-updated. `decay_weight`
and `hours_since_reinforcement` are computed, not stored.

**P3 — Side effects at the boundary.** Retrieval is pure. Scoring is pure.
Frame assembly is pure. Reinforcement and state mutations happen in L4 only.
This makes every layer below L4 trivially testable.

**P4 — One concept, one place.** Scoring is one function. Frame definitions are
one schema. Block state transitions are one state machine. No concept is split
across files without a clear reason.

**P5 — Configuration over code.** Differences between frames are data
(weights, filters, guarantees), not code branches. A new frame is a new row
in the frames table, not a new module.

---

## Part 3: The Refined Design

### 3.1 — Unified Scoring Function

One function, used everywhere:

```python
@dataclass(frozen=True)
class ScoringWeights:
    similarity: float
    confidence: float
    recency: float
    centrality: float
    reinforcement: float

    def renormalized_without_similarity(self) -> "ScoringWeights":
        """For queryless retrieval: redistribute similarity weight."""
        remaining = self.confidence + self.recency + self.centrality + self.reinforcement
        if remaining == 0:
            return ScoringWeights(0, 0.25, 0.25, 0.25, 0.25)
        factor = 1.0 / remaining
        return ScoringWeights(
            similarity=0.0,
            confidence=self.confidence * factor,
            recency=self.recency * factor,
            centrality=self.centrality * factor,
            reinforcement=self.reinforcement * factor,
        )

def score(
    similarity: float,
    confidence: float,
    recency: float,         # = decay_weight = exp(-λ × h)
    centrality: float,      # normalised weighted degree
    reinforcement: float,   # = log(1+count) / log(1+max_count)
    weights: ScoringWeights,
) -> float:
    return (
        weights.similarity    * similarity +
        weights.confidence    * confidence +
        weights.recency       * recency +
        weights.centrality    * centrality +
        weights.reinforcement * reinforcement
    )
```

The pipeline decides whether to renormalize:

```python
effective_weights = (
    frame.weights.renormalized_without_similarity()
    if query is None
    else frame.weights
)
```

No conditional inside the scoring function. No awareness of frames or queries.

---

### 3.2 — Frame Model as Pure Configuration

```python
@dataclass(frozen=True)
class FrameDefinition:
    name: str
    weights: ScoringWeights
    filters: FrameFilters
    guarantees: list[str]           # tag patterns for guaranteed blocks
    template: str                   # rendering template name or string
    token_budget: int
    cache: CachePolicy | None       # None = no caching
    source: Literal["builtin", "user"]

@dataclass(frozen=True)
class FrameFilters:
    tag_patterns: list[str] | None = None    # glob, OR-combined
    categories: list[str] | None = None
    search_window_hours: float = 200.0

@dataclass(frozen=True)
class CachePolicy:
    ttl_seconds: int = 3600
    invalidate_on: list[str] = field(
        default_factory=lambda: ["block_change"]
    )
```

**The three built-in frames — as data, not code:**

```python
SELF = FrameDefinition(
    name="self",
    weights=ScoringWeights(0.05, 0.30, 0.05, 0.25, 0.30),
    # ↑ similarity low — identity is not query-driven
    # confidence + reinforcement high — stable, established blocks win
    filters=FrameFilters(tag_patterns=["self/*"]),
    guarantees=["self/constitutional"],
    template="self",
    token_budget=600,
    cache=CachePolicy(
        ttl_seconds=3600,
        invalidate_on=["self_block_change", "curate_complete"],
    ),
    source="builtin",
)

ATTENTION = FrameDefinition(
    name="attention",
    weights=ScoringWeights(0.35, 0.15, 0.15, 0.15, 0.10),
    # ↑ similarity dominant — relevance to query is the primary signal
    # balanced across other signals
    # ↑ 0.10 reinforcement kept — differs from 023 which dropped it
    filters=FrameFilters(),  # no tag filter — all blocks eligible
    guarantees=[],
    template="attention",
    token_budget=2000,
    cache=None,
    source="builtin",
)

TASK = FrameDefinition(
    name="task",
    weights=ScoringWeights(0.20, 0.20, 0.20, 0.20, 0.20),
    # ↑ equal weights — tasks need balanced awareness
    filters=FrameFilters(),
    guarantees=["self/goal"],
    template="task",
    token_budget=800,
    cache=None,
    source="builtin",
)
```

**What this eliminates:**
- `context/self_frame.py` — SELF is a configured frame, not a special module
- Constitutional guarantee logic — handled by `guarantees` field
- Hardcoded cache decisions — handled by `cache` field
- Any `if frame.name == "self"` conditional in the pipeline

**Custom frame creation:**

```python
system.register_frame(FrameDefinition(
    name="code_review",
    weights=ScoringWeights(0.50, 0.20, 0.05, 0.10, 0.15),
    filters=FrameFilters(tag_patterns=["knowledge/technical", "self/style"]),
    guarantees=["self/constraint"],
    template="attention",     # reuse existing template
    token_budget=1500,
    cache=None,
    source="user",
))

# Use identically to built-in frames:
result = system.frame("code_review", query="error handling patterns")
```

---

### 3.3 — Computed Decay via Active Hours Clock

**Schema change:**

```sql
-- blocks table
-- REMOVE: hours_since_reinforcement REAL
-- ADD:
last_reinforced_at REAL NOT NULL DEFAULT 0.0   -- cumulative active hours

-- system_config
-- ADD:
total_active_hours REAL NOT NULL DEFAULT 0.0
```

**At query time, every "hours since reinforcement" is computed:**

```python
def hours_since(block_last_reinforced_at: float, current_active_hours: float) -> float:
    return current_active_hours - block_last_reinforced_at
```

**Pre-filter** uses the same computation:

```sql
SELECT ... FROM blocks
WHERE status = 'active'
AND last_reinforced_at > :cutoff   -- cutoff = current_active_hours - search_window
```

**What this eliminates:**
- Bulk `UPDATE blocks SET hours_since_reinforcement = ...` at session boundaries
- Stale `hours_since_reinforcement` values between updates
- The entire concept of "updating decay" — decay is a function, not stored state

**What this preserves:**
- Session-aware decay (the clock only advances during active sessions)
- Identical mathematical behavior (same exp formula, same results)
- Index-friendly pre-filtering

---

### 3.4 — Block State Machine

One clear state machine, replacing scattered transitions across explorations:

```
                     ┌─────────────────────────────────────────┐
                     │                                         │
  learn()            │  consolidate()            curate() /    │
  ────────▶ [inbox] ─┼──────────────▶ [active] ──forget()───▶ [archived]
                     │                    ▲                     │
                     │                    │ re-learn (new hash) │
                     │                    └─────────────────────┘
                     │
                     └──▶ rejected (hash exists in active, or content invalid)
```

**Three states:**

| State | Meaning | Retrieved? | Decays? |
|-------|---------|-----------|---------|
| `inbox` | Awaiting consolidation | No | No |
| `active` | In memory, retrievable | Yes | Yes |
| `archived` | Removed, preserved for audit | No | No |

**Archive reasons** (metadata on archived blocks):

| Reason | Trigger |
|--------|---------|
| `decayed` | `curate()`: decay_weight < 0.05 |
| `superseded` | `consolidate()`: near-duplicate replaced this block |
| `forgotten` | `forget()`: user/agent explicitly removed |

**Transition rules:**
- `inbox → active`: only via `consolidate()`
- `inbox → rejected`: content hash already exists in active blocks
- `active → archived`: via `curate()` (decay), `consolidate()` (superseded), or `forget()`
- `archived → active`: not possible (immutability); re-learn creates a new block

**Edge CASCADE:** When a block is archived, its edges are removed (ON DELETE
CASCADE on the edges table foreign keys). Tags are removed similarly.

---

### 3.5 — Retrieval Without Side Effects

The retrieval pipeline is refactored into three pure stages plus one side-effect
stage. The side-effect stage is called by L4 only when appropriate.

```
┌──────────────────────────────────────────────────────────────┐
│  PURE (no side effects, no state mutation)                    │
│                                                               │
│  Stage 1: Pre-filter (SQL)                                    │
│      → candidate pool (blocks with scoring fields)            │
│                                                               │
│  Stage 2: Vector similarity + Graph expansion                 │
│      → scored candidates with all five signal values          │
│      → (similarity=0 for expansion blocks and queryless)      │
│                                                               │
│  Stage 3: Frame assembly                                      │
│      → apply guarantees (guaranteed blocks always included)   │
│      → suppress contradictions                                │
│      → enforce token budget                                   │
│      → render to text                                         │
│      → return FrameResult                                     │
│                                                               │
├──────────────────────────────────────────────────────────────┤
│  SIDE EFFECTS (L4 only)                                       │
│                                                               │
│  Stage 4: Reinforce                                           │
│      → update last_reinforced_at for returned blocks          │
│      → increment reinforcement_count                          │
│      → reinforce co-retrieval edges (+0.1 weight)             │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**Impact on internal calls:**

```python
# External: frame() — retrieves AND reinforces
def frame(self, name, query=None, top_k=5, token_budget=None):
    result = self._assemble(name, query, top_k, token_budget)
    self._reinforce(result.block_ids)  # side effect
    return result

# Internal: consolidate checking self-alignment — retrieves only
def _get_self_context(self):
    return self._assemble("self", query=None, top_k=10, token_budget=600)
    # no reinforcement — just reading
```

No `_recall()` vs `recall()`. No `reinforce=False` parameter. One assembly
path, and L4 decides whether to add the side effect.

---

### 3.6 — Refined Module Layout

```
amgs/
├── __init__.py
├── api.py                      # L4 — MemorySystem (4 essential + power-user methods)
├── session.py                  # L4 — session lifecycle, active hours tracking
├── types.py                    # All data types: ScoredBlock, FrameResult, FrameDefinition, etc.
├── scoring.py                  # THE scoring function — shared utility, no layer ownership
│
├── ports.py                    # EmbeddingService + LLMService protocols (one file)
├── adapters/                   # Concrete implementations
│   ├── openai.py
│   └── anthropic.py
│
├── operations/                 # L4 — vertical slices
│   ├── learn.py
│   ├── consolidate.py
│   ├── curate.py
│   └── recall.py               # orchestrates pure retrieval + side effects
│
├── context/                    # L3
│   ├── frames.py               # frame registry, builtin definitions, custom CRUD, caching
│   ├── rendering.py            # blocks → string (templates)
│   └── contradiction.py        # suppression logic
│
├── memory/                     # L2
│   ├── blocks.py               # state transitions (inbox → active → archived)
│   ├── graph.py                # edges, centrality, 1-hop expansion
│   ├── pipeline.py             # hybrid retrieval (pre-filter → vector → expand → score)
│   ├── tagging.py              # self-tag inference and promotion
│   └── dedup.py                # near-duplicate detection, forget+create+inherit
│
├── db/                         # L1
│   ├── engine.py               # SQLAlchemy engine, pragma listener
│   ├── models.py               # table definitions (schema source of truth)
│   ├── queries/                # named query functions
│   │   ├── blocks.py
│   │   ├── edges.py
│   │   ├── frames.py
│   │   └── inbox.py
│   └── migrations/
│       ├── env.py
│       └── versions/
│           └── 001_initial.py
│
└── files.py                    # L1 — block .md file read/write
```

**What changed from exploration 022:**

| Change | Reason |
|--------|--------|
| `context/scoring.py` removed | Scoring is one function in `amgs/scoring.py` |
| `context/self_frame.py` removed | SELF is a configured frame in `context/frames.py` |
| `memory/scoring.py` removed | Scoring promoted to shared `amgs/scoring.py` |
| `memory/decay.py` removed | Decay is computed in `scoring.py` at query time |
| `types.py` added | All data types centralised; imported by every layer |
| `ports/` directory → `ports.py` file | Two protocols don't need a directory |
| `db/queries/recall.py` removed | Recall queries are in `blocks.py` and `edges.py` |

**File count:** 22 files (was 25). Three removed, one added.

---

### 3.7 — Minimal API Surface

**Four essential methods** (covers 90% of usage):

```python
class MemorySystem:
    # Session
    def begin_session(self, task_type: str = "general") -> Session: ...
    def end_session(self) -> None: ...

    # Per-turn
    def frame(self, name: str, query: str | None = None,
              top_k: int = 5, token_budget: int | None = None) -> FrameResult: ...
    def learn(self, content: str, tags: list[str] | None = None,
              category: str = "knowledge") -> str | None: ...
              # returns block_id or None if rejected (duplicate)
```

**Power-user methods** (explicit control):

```python
    # Raw retrieval (no rendering, no reinforcement)
    def recall(self, name: str, query: str | None = None,
               top_k: int = 5) -> list[ScoredBlock]: ...

    # Rendering only (no retrieval)
    def render(self, blocks: list[ScoredBlock], template: str,
               token_budget: int | None = None) -> str: ...

    # Explicit lifecycle (normally automatic)
    def consolidate(self) -> int: ...    # returns blocks promoted
    def curate(self) -> int: ...         # returns blocks archived

    # Memory management
    def forget(self, query: str, confirm: bool = False) -> list[str]: ...
    def register_frame(self, frame: FrameDefinition) -> None: ...
```

**Automatic triggers** (the library handles these):

| Event | Triggers |
|-------|----------|
| `end_session()` | `consolidate()` if inbox_size ≥ threshold |
| `begin_session()` | `curate()` if active_hours_since_last_curate ≥ 40 |
| `learn()` | Immediate hash-based dedup (reject exact duplicates) |
| Self-block change during consolidate/curate | Cache invalidation for SELF frame |

The developer calls `begin_session`, `frame`, `learn`, `end_session`.
Everything else is automatic.

---

### 3.8 — Refined Schema

The `blocks` table, incorporating all refinements:

```sql
CREATE TABLE blocks (
    id                  TEXT PRIMARY KEY,       -- sha256(normalized_content)[:16]
    file_path           TEXT NOT NULL UNIQUE,
    category            TEXT NOT NULL,
    source              TEXT NOT NULL,
    created_at          TEXT NOT NULL,

    -- State
    status              TEXT NOT NULL DEFAULT 'inbox',   -- inbox | active | archived
    archive_reason      TEXT,                            -- decayed | superseded | forgotten

    -- Scoring signals (stored)
    confidence          REAL NOT NULL DEFAULT 0.50,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    decay_lambda        REAL NOT NULL DEFAULT 0.01,
    last_reinforced_at  REAL NOT NULL DEFAULT 0.0,      -- cumulative active hours
    self_alignment      REAL,

    -- Embedding
    embedding           BLOB,
    embedding_model     TEXT,

    -- Metadata
    token_count         INTEGER,
    last_session_id     TEXT
);

-- hours_since_reinforcement: REMOVED (computed at query time)
-- hours_since_reinforcement was:
--   system.current_active_hours() - blocks.last_reinforced_at
```

**Supporting tables** (unchanged from 017, except status values):

```sql
CREATE TABLE block_tags (
    block_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    tag      TEXT NOT NULL,
    PRIMARY KEY (block_id, tag)
);

CREATE TABLE edges (
    from_id  TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    to_id    TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    type     TEXT NOT NULL DEFAULT 'relates_to',
    weight   REAL NOT NULL DEFAULT 0.5,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (from_id, to_id)
);

CREATE TABLE contradictions (
    block_a_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    block_b_id TEXT NOT NULL REFERENCES blocks(id) ON DELETE CASCADE,
    score      REAL NOT NULL,
    resolved   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (block_a_id, block_b_id)
);

CREATE TABLE frames (
    name           TEXT PRIMARY KEY,
    weights_json   TEXT NOT NULL,
    filters_json   TEXT NOT NULL,
    guarantees_json TEXT NOT NULL DEFAULT '[]',
    template       TEXT NOT NULL,
    token_budget   INTEGER NOT NULL,
    cache_json     TEXT,                    -- NULL = no caching
    source         TEXT NOT NULL DEFAULT 'user',
    created_at     TEXT NOT NULL
);

CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    task_type       TEXT NOT NULL DEFAULT 'general',
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    start_active_hours REAL NOT NULL
);

CREATE TABLE system_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

**Indexes:**
```sql
CREATE INDEX idx_blocks_status ON blocks(status);
CREATE INDEX idx_blocks_last_reinforced ON blocks(last_reinforced_at);
CREATE INDEX idx_block_tags_tag ON block_tags(tag);
CREATE INDEX idx_edges_from ON edges(from_id);
CREATE INDEX idx_edges_to ON edges(to_id);
CREATE INDEX idx_contradictions_unresolved
    ON contradictions(block_a_id, block_b_id) WHERE resolved = 0;
```

---

### 3.9 — Refined Recall Pipeline (End-to-End)

Tracing `system.frame("attention", query="Celery race condition", top_k=5)`:

```
L4 (operations/recall.py):
│
├─ Look up frame definition from L3
│   → ATTENTION: weights, filters, guarantees=[], cache=None
│
├─ Compute effective weights
│   → query is not None, so use weights as-is
│
├─ Compute current_active_hours
│   → total_active_hours + (now - session.started_at)
│
├─ Call L2 pipeline.retrieve(query, filters, effective_weights, current_ah, conn, embed_svc)
│   │
│   ├─ Stage 1: Pre-filter (L1 query)
│   │   SELECT id, embedding, confidence, reinforcement_count,
│   │          decay_lambda, last_reinforced_at, self_alignment
│   │   FROM blocks WHERE status = 'active'
│   │   AND last_reinforced_at > :cutoff
│   │   → candidate pool
│   │
│   ├─ Stage 2: Vector similarity
│   │   query_vec = await embed_svc.embed(query)
│   │   similarities = cosine(query_vec, candidate_vecs)
│   │   seeds = top_20_by_similarity
│   │
│   ├─ Stage 3: Graph expansion
│   │   neighbour_ids from edges of seeds
│   │   expansion_blocks loaded (scoring fields only, no embedding)
│   │   expansion_blocks.similarity = 0.0
│   │
│   └─ Stage 4: Composite scoring
│       for each (seeds + expansion_blocks):
│           h = current_ah - block.last_reinforced_at
│           recency = exp(-λ × h)
│           reinforcement = log(1 + count) / log(1 + max_count)
│           centrality = weighted_degree / max_degree
│           block.score = score(sim, conf, recency, centrality, reinf, weights)
│       sort by score descending
│       → list[ScoredBlock] (top_k × 2 for suppression headroom)
│
├─ Call L3 frames.assemble(scored_blocks, frame_def, contradictions)
│   │
│   ├─ Load guaranteed blocks (guarantee_tags → none for ATTENTION)
│   ├─ Suppress contradictions (drop lower-confidence from pairs)
│   ├─ Token budget enforcement (greedy from top)
│   ├─ Render via template
│   └─ Return FrameResult(text, blocks, token_count, frame_name)
│
├─ Reinforce (L1 write — this is the ONLY side effect)
│   UPDATE blocks SET reinforcement_count = reinforcement_count + 1,
│                     last_reinforced_at = :current_active_hours
│   WHERE id IN (:returned_block_ids);
│   -- co-retrieval edge reinforcement
│   UPDATE edges SET reinforcement_count = reinforcement_count + 1
│   WHERE (from_id, to_id) IN (:co_retrieved_pairs);
│
└─ Return FrameResult to caller
```

**Total DB operations:** 3 SELECT + 1 UPDATE transaction.
**Side effects:** One UPDATE at the end. Everything above it is pure.

---

## Part 4: What Didn't Change

Most of the design is already correct. These elements survive the refactoring
unchanged:

| Element | Why it's already right |
|---------|----------------------|
| Four-layer architecture (L1→L4) | Clean separation; dependency direction is correct |
| Hybrid retrieval pipeline (4 stages) | Pre-filter + vector + graph + score is sound |
| Block immutability | Provenance, auditability, and trust depend on it |
| Content-hash IDs | O(1) dedup, content-addressable, no collisions on meaning |
| Near-duplicate handling (009's model) | Forget + create + inherit respects immutability |
| Graph expansion recovering stale blocks | The core graph value proposition |
| Co-retrieval edge reinforcement | Organic cluster formation is emergent and valuable |
| `learn()` is instant, `consolidate()` is heavy | Hot-path safety is non-negotiable |
| SQLAlchemy Core, not ORM | Right choice for this write pattern |
| WAL mode, NullPool, batch migrations | All correct SQLite practices |
| External deps as injected protocols | Mockable, swappable, decoupled |
| Block files as primary record | Database is a derived index — correct |
| Session-aware decay | Solves the holiday problem |
| Tags replace boolean flags | Richer, composable, filterable |
| Token budget enforcement (greedy from top) | Most important blocks always included |
| Contradiction suppression inside assembly | Callers always get clean results |

---

## Locked Design Decisions

| Decision | Rationale |
|----------|-----------|
| Five scoring components, always (similarity, confidence, recency, centrality, reinforcement) | Resolves 4-vs-5 inconsistency from 023; each signal is meaningfully distinct |
| One scoring function in shared `amgs/scoring.py` | Eliminates duplicate `memory/scoring.py` and `context/scoring.py` |
| Queryless retrieval: renormalize weights (drop similarity, scale others to 1.0) | No conditional in scoring function; pipeline decides weights before scoring |
| `guarantee_tags` replaces constitutional special-case | General mechanism; constitutionals configured as `guarantees=["self/constitutional"]` on SELF frame |
| `CachePolicy` on FrameDefinition replaces hardcoded SELF-only caching | Any frame can cache; SELF is just the one that does by default |
| Retrieval is side-effect-free; reinforcement is a separate L4 step | Eliminates `_recall(reinforce=False)`; makes L2 and L3 pure and testable |
| `last_reinforced_at` (cumulative active hours) replaces `hours_since_reinforcement` | Computed at query time; eliminates bulk UPDATE and stale-data risk |
| `total_active_hours` global counter in `system_config` | One value tracks all session activity; decay clock pauses between sessions |
| Three block states: inbox, active, archived (with archive_reason) | Clean state machine; replaces scattered status terms |
| Four decay tiers: permanent, durable, standard, ephemeral | Simplifies 6+ decay rates to 4; tag taxonomy still controls frame filtering |
| Dedup thresholds: >0.95 reject, 0.90–0.95 near-dedup, <0.90 distinct | 009's three-band model is canonical; supersedes 008's single threshold |
| `learn()` computes content hash for instant exact-dedup | O(1) rejection of exact duplicates at the hot path; no embedding needed |
| `context/self_frame.py` merged into `context/frames.py` | SELF is a configured frame, not a special module |
| `memory/decay.py` removed | Decay weight is computed inside `scoring.py`; no separate module needed |
| `types.py` centralises all data types | ScoredBlock, FrameResult, FrameDefinition, ScoringWeights all in one importable file |
| `consolidate()` auto-triggered at `end_session()` | Developer doesn't need to remember; inbox is always processed |
| `curate()` auto-triggered at `begin_session()` | Maintenance runs before the session starts; stale blocks pruned before retrieval |

---

## Open Questions

1. **Confidence evolution model**: Confidence starts at ~0.50, gets +0.01 per
   curate survival, -0.1 per contradiction. Is this enough dynamic range?
   Should user/agent be able to set initial confidence (e.g., "I'm very sure
   about this" → confidence = 0.85)?

2. **Template system**: Should rendering templates be simple string interpolation
   (Phase 1) or a lightweight template engine (Jinja2-style)? Phase 1 can use
   `str.format()` with named sections.

3. **Active hours precision**: `total_active_hours` is updated at session end.
   What if the process crashes mid-session? The active hours for that session are
   lost. Mitigation: periodic flush to `system_config` (every N minutes).

4. **Frame inheritance**: Exploration 016 defined `extends=` for frame
   inheritance. Does this survive the refactoring? (Yes — inheritance at
   registration time is compatible with the refined FrameDefinition model.)

5. **`forget()` confirmation flow**: `forget(query)` retrieves matching blocks
   and archives them. Should it return candidates for user confirmation before
   archiving? (`confirm=False` → immediate; `confirm=True` → return candidates
   without archiving.) This is defined in the API but the UX isn't specified.

---

## Variations

- [ ] Trace `consolidate()` through the refined architecture end-to-end
- [ ] Define the exact `types.py` file with all dataclass definitions
- [ ] Walk through the SELF frame cache lifecycle with `CachePolicy`
- [ ] Define the initial Alembic migration with the refined schema
- [ ] Identify the critical-path modules for MVP Phase 1 implementation
