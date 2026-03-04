# Title: Implementation Priority — Sequence, Dependencies, and Risks

## Status: complete

## Question

Twenty-six explorations and six playgrounds define the elfmem design completely.
The next phase is implementation. The wrong build order wastes effort: building
the wrong abstractions early causes cascading rework; building the right things
in the wrong order leaves you with untestable code until the very end.

What is the correct build sequence? What must be right from day one? Where are the
risks? What does "done" look like at each step?

---

## The Core Tension: Layers vs. Slices

Two build strategies exist:

### Strategy A: Horizontal Layers
Build one complete layer before moving to the next.

```
All of L1 (storage) → All of L2 (memory) → All of L3 (context) → All of L4 (API)
```

**Problem:** No working software until the very end. Abstractions built in L1
might be wrong for L3's needs — discovered too late. The test suite cannot run
until all four layers exist.

### Strategy B: Vertical Slices
Build thin end-to-end paths through all layers for one capability at a time.

```
learn() slice → consolidate() slice → recall() slice → curate() slice
```

**Problem:** Without a solid foundation (types, ports, schema), each slice builds
on shifting ground. The first slice must still establish shared contracts.

### Resolution: Foundation First, Then Vertical Slices

The right approach combines both:

1. **Foundation** (days 1–3): Establish all shared contracts — types, protocols, schema.
   These are the hardest things to change later. Get them right first.

2. **Vertical slices** (days 4–N): Build each lifecycle operation end-to-end through
   all layers. Working software after each slice.

This is the sequence below.

---

## What Is "Sticky" — The High Cost of Getting Wrong

Some decisions, once made in code, are expensive to change:

| Decision | Why it's sticky | Risk if wrong |
|----------|----------------|---------------|
| SQLite schema | Alembic migration required for every change; deployed databases carry old schemas | Cascading field name changes; data loss during migration |
| `types.py` vocabulary | Every module imports these types; `ScoredBlock`, `FrameResult`, `BlockStatus` are used everywhere | Cascading rename/restructure across all layers |
| Port protocols | All adapters and tests coded against these contracts; changing breaks everything | Re-implement all adapters; all tests updated |
| Async boundary | Mixing sync/async in SQLAlchemy is painful to refactor later | Thread-pool hacks; deadlocks; unpredictable performance |
| `scoring.py` formula | Stored scores in the database reflect the formula in use; changing the formula invalidates stored data | Scores drift silently if formula changes post-deployment |

These five are the foundation. Spend the most time getting them right.

---

## The Eight Steps

### Step 0: Project Skeleton + Tooling

**Build:**
```
elfmem/
├── __init__.py
├── py.typed                     # PEP 561 typing marker
pyproject.toml                   # deps: sqlalchemy, alembic, pydantic, numpy, litellm, instructor, pyyaml
tests/
├── conftest.py                  # shared fixtures (in-memory system, mocks)
├── __init__.py
alembic/
├── env.py                       # render_as_batch=True, StaticPool for tests
└── versions/                    # initial migration goes here
```

**Non-negotiable tooling decisions:**
- `pytest-asyncio` — all tests are async; set `asyncio_mode = "auto"` in `pytest.ini`
- `ruff` — linter/formatter; no style debates
- `mypy` — strict type checking from day one
- SQLAlchemy Core (not ORM) — locked in exploration 019
- `asyncio` throughout — mixing sync/async is worse than committing fully

**Done when:** `pytest` runs (0 tests pass, 0 fail); `mypy` and `ruff` pass on empty codebase.

---

### Step 1: Types + Ports (The Vocabulary)

**Build:**
```python
# elfmem/types.py
@dataclass
class ScoredBlock:
    id: str
    content: str
    tags: list[str]
    similarity: float
    confidence: float
    recency: float
    centrality: float
    reinforcement: float
    score: float
    was_expanded: bool = False
    status: str = "active"

@dataclass
class FrameResult:
    text: str
    blocks: list[ScoredBlock]
    frame_name: str
    cached: bool = False

class BlockStatus(str, Enum):
    INBOX = "inbox"
    ACTIVE = "active"
    ARCHIVED = "archived"

class ArchiveReason(str, Enum):
    DECAYED = "decayed"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"

class DecayTier(str, Enum):
    PERMANENT = "permanent"   # λ = 0.00001
    DURABLE   = "durable"     # λ = 0.001
    STANDARD  = "standard"    # λ = 0.010
    EPHEMERAL = "ephemeral"   # λ = 0.050

# elfmem/ports/services.py
class LLMService(Protocol):
    async def score_self_alignment(self, block: str, self_context: str) -> float: ...
    async def infer_self_tags(self, block: str, self_context: str) -> list[str]: ...
    async def detect_contradiction(self, block_a: str, block_b: str) -> float: ...

class EmbeddingService(Protocol):
    async def embed(self, text: str) -> np.ndarray: ...
```

**Why ports before everything:** All adapters and all tests code against these
protocols. Changing them later means changing every adapter and every test.
Define them once; they are the stable seam between business logic and infrastructure.

**Done when:** Types and protocols importable; `mypy` clean.

---

### Step 2: Scoring (The First Module)

**Build:**
```python
# elfmem/scoring.py
@dataclass
class ScoringWeights:
    similarity:    float
    confidence:    float
    recency:       float
    centrality:    float
    reinforcement: float

    @model_validator(mode="after")
    def weights_sum_to_one(self): ...   # raises ValidationError if not

    def renormalized_without_similarity(self) -> "ScoringWeights":
        # Drop similarity; rescale remaining four to sum to 1.0
        ...

SELF_WEIGHTS     = ScoringWeights(similarity=0.10, confidence=0.30, recency=0.05, centrality=0.25, reinforcement=0.30)
ATTENTION_WEIGHTS = ScoringWeights(similarity=0.35, confidence=0.15, recency=0.25, centrality=0.15, reinforcement=0.10)
TASK_WEIGHTS      = ScoringWeights(similarity=0.20, confidence=0.20, recency=0.20, centrality=0.20, reinforcement=0.20)

def compute_score(
    similarity: float, confidence: float, recency: float,
    centrality: float, reinforcement: float,
    weights: ScoringWeights,
) -> float:
    return (weights.similarity    × similarity
          + weights.confidence    × confidence
          + weights.recency       × recency
          + weights.centrality    × centrality
          + weights.reinforcement × reinforcement)

def compute_recency(tier: DecayTier, hours_since: float) -> float:
    λ = {DecayTier.PERMANENT: 0.00001, DecayTier.DURABLE: 0.001,
         DecayTier.STANDARD: 0.010,    DecayTier.EPHEMERAL: 0.050}[tier]
    return math.exp(-λ × hours_since)

def log_normalise_reinforcement(count: int, max_count: int) -> float:
    if max_count == 0: return 0.0
    return math.log(1 + count) / math.log(1 + max_count)
```

**Why scoring before storage:** Pure Python — zero external dependencies. The 12
scoring test cases can all pass before a single SQL query is written. This validates
the most critical formula and builds confidence early.

**Done when:** All 12 scoring playground test cases pass; `mypy` clean.

---

### Step 3: Schema + Storage (The Foundation)

**Build:**
```python
# elfmem/db/models.py  (schema source of truth for Alembic + queries + tests)
blocks = Table("blocks", metadata,
    Column("id",                  String(16), primary_key=True),
    Column("content",             Text,   nullable=False),
    Column("confidence",          Float,  nullable=False, default=0.5),
    Column("status",              String, nullable=False, default="inbox"),
    Column("archive_reason",      String, nullable=True),
    Column("decay_tier",          String, nullable=False, default="standard"),
    Column("last_reinforced_at",  Float,  nullable=False, default=0.0),
    Column("reinforcement_count", Integer, nullable=False, default=0),
    Column("self_alignment",      Float,  nullable=True),
    Column("embedding",           LargeBinary, nullable=True),
    Column("embedding_model",     String, nullable=True),
    Column("created_at",          Float,  nullable=False),  # total_active_hours at creation
    Column("source",              String, nullable=True),
)

block_tags = Table("block_tags", metadata, ...)
edges      = Table("edges", metadata, ...)
contradictions = Table("contradictions", metadata, ...)
sessions   = Table("sessions", metadata, ...)
system_config = Table("system_config", metadata, ...)
frames     = Table("frames", metadata, ...)

# elfmem/db/engine.py
def make_engine(db_path: str) -> Engine:
    # NullPool for file DBs; StaticPool for :memory:
    # event.listens_for → PRAGMA WAL, foreign_keys, synchronous=NORMAL
    ...

# alembic/versions/001_initial.py
# Creates all tables; seeds built-in frames; seeds system_config defaults
```

**Critical details that must be right:**
- `last_reinforced_at` stores cumulative active hours (not wall-clock timestamp)
- `embedding` is BLOB — use `tobytes()`/`frombuffer(np.float32)` consistently
- `render_as_batch=True` globally in Alembic `env.py` — SQLite ALTER TABLE is restricted
- `PRAGMA journal_mode=WAL` — allows concurrent reads during async writes
- `StaticPool` for `:memory:` test databases — all connections share one instance
- Seed data in the initial migration, not in application code

**Done when:** `alembic upgrade head` creates all tables; seed data present; basic
`SELECT`/`INSERT`/`UPDATE`/`DELETE` queries work against `:memory:` database.

---

### Step 4: Mock Adapters (The Testing Unlock)

**Build:**
```python
# elfmem/adapters/mock.py

class MockLLMService(LLMService):
    """Deterministic, configurable mock for all LLM calls."""

    def __init__(self,
        alignment_scores: dict[str, float] | None = None,   # override per block content
        default_alignment: float = 0.65,
        default_tags: list[str] | None = None,
        contradiction_scores: dict[tuple, float] | None = None,
        default_contradiction: float = 0.10,
    ): ...

    async def score_self_alignment(self, block, self_context) -> float:
        return self.alignment_scores.get(block, self.default_alignment)

    async def infer_self_tags(self, block, self_context) -> list[str]:
        return self.default_tags or []

    async def detect_contradiction(self, block_a, block_b) -> float:
        key = (min(block_a, block_b), max(block_a, block_b))
        return self.contradiction_scores.get(key, self.default_contradiction)


class MockEmbeddingService(EmbeddingService):
    """Reproducible embeddings: same text → same vector, always."""

    def __init__(self, dimensions: int = 64, similarity_overrides: dict | None = None):
        self.dimensions = dimensions
        self.similarity_overrides = similarity_overrides or {}

    async def embed(self, text: str) -> np.ndarray:
        rng = np.random.default_rng(hash(text) % (2**32))
        vec = rng.random(self.dimensions).astype(np.float32)
        return vec / np.linalg.norm(vec)
```

**The key insight for mock embeddings:** Text-hash seeding means the same text
always produces the same embedding — but semantically similar text produces
**unrelated** embeddings (hash is not semantic). For tests that rely on
similarity, use `similarity_overrides` to inject a controlled cosine similarity
between two specific texts.

**Why mocks before real adapters:** This step unlocks the entire test suite.
From here, all business logic can be fully tested without API keys or network calls.
The real LiteLLM adapter can be built last, knowing all logic is verified.

**Done when:** `MockLLMService` and `MockEmbeddingService` satisfy the `LLMService`
and `EmbeddingService` protocols; `mypy` passes; basic test fixtures work.

---

### Step 5: Vertical Slice 1 — learn() and consolidate()

**Build:**
```python
# elfmem/memory/blocks.py
def compute_content_hash(content: str) -> str:     # sha256[:16]
def is_exact_duplicate(db, content_hash: str) -> bool:
def insert_inbox_block(db, content, source, tags) -> str:  # returns block_id

# elfmem/operations/learn.py
async def learn(db, content, tags, source) -> str:
    content_hash = compute_content_hash(content)
    if is_exact_duplicate(db, content_hash): return content_hash  # silent reject
    return insert_inbox_block(db, content, source, tags)

# elfmem/operations/consolidate.py
async def consolidate(db, llm: LLMService, embedding: EmbeddingService, config) -> int:
    inbox = fetch_inbox_blocks(db)
    if not inbox: return 0
    for block in inbox:
        vec = await embedding.embed(block.content)
        # near-dup check against active blocks
        # if near-dup: forget(existing) + inherit + continue
        # if exact-dup by embedding: reject
        alignment = await llm.score_self_alignment(block.content, self_context)
        tags = await llm.infer_self_tags(...) if alignment >= threshold else []
        promote_to_active(db, block, embedding=vec, alignment=alignment, tags=tags)
        build_edges(db, block, active_blocks, config)
    return len(inbox)
```

**Integrate into MemorySystem:**
```python
# elfmem/api.py
class MemorySystem:
    async def begin_session(self) -> None: ...
    async def end_session(self) -> None:
        await consolidate(...)   # always consolidate on session end
    async def learn(self, content: str, ...) -> str: ...
    async def consolidate(self) -> int: ...
```

**Done when:** TC-L-001 through TC-L-006 + TC-L-012 through TC-L-014 pass (learn
and consolidate test cases from the lifecycle playground).

---

### Step 6: Vertical Slice 2 — recall() and frame()

**Build:**
```python
# elfmem/memory/graph.py
def compute_centrality(db, block_ids: list[str]) -> dict[str, float]: ...
def expand_1hop(db, seed_ids: list[str]) -> list[str]: ...
def reinforce_edges(db, co_retrieved: list[str]) -> None: ...

# elfmem/memory/retrieval.py
async def hybrid_retrieve(
    db, embedding: EmbeddingService, query: str | None,
    frame_def: FrameDefinition, config,
) -> list[ScoredBlock]:
    # Stage 1: pre-filter
    # Stage 2: vector search (skip for SELF)
    # Stage 3: graph expand (skip for SELF)
    # Stage 4: composite score + contradiction suppress + top-K

# elfmem/context/frames.py
SELF_FRAME      = FrameDefinition(weights=SELF_WEIGHTS, ...)
ATTENTION_FRAME = FrameDefinition(weights=ATTENTION_WEIGHTS, ...)
TASK_FRAME      = FrameDefinition(weights=TASK_WEIGHTS, ...)

# elfmem/context/rendering.py
def render(blocks: list[ScoredBlock], template: str, token_budget: int) -> str: ...

# elfmem/operations/recall.py
async def recall(db, embedding, query, frame_def, config) -> FrameResult:
    blocks = await hybrid_retrieve(...)
    reinforce_blocks(db, [b.id for b in blocks])
    reinforce_edges(db, co_retrieved=[b.id for b in blocks])
    text = render(blocks, frame_def.template, frame_def.token_budget)
    return FrameResult(text=text, blocks=blocks, ...)

# elfmem/api.py — add frame()
async def frame(self, name: str, query: str | None = None) -> FrameResult: ...
```

**Done when:** All retrieval playground test cases + frame playground test cases pass.

---

### Step 7: Vertical Slice 3 — curate()

**Build:**
```python
# elfmem/operations/curate.py
async def curate(db, config) -> CurateResult:
    total_active_hours = get_total_active_hours(db)
    active_blocks = fetch_active_blocks(db)

    # 1. Compute recency for all blocks; archive below prune_threshold
    archived = []
    for block in active_blocks:
        hours_since = total_active_hours - block.last_reinforced_at
        recency = compute_recency(block.decay_tier, hours_since)
        if recency < config.prune_threshold:
            archive_block(db, block.id, reason=ArchiveReason.DECAYED)
            archived.append(block.id)

    # 2. Prune weak edges
    prune_weak_edges(db, threshold=config.edge_prune_threshold)

    # 3. Reinforce top-N active blocks (by composite score)
    reinforce_top_n(db, n=config.curate_reinforce_top_n)

    return CurateResult(archived=len(archived), ...)

# elfmem/api.py — integrate into begin_session()
async def begin_session(self) -> None:
    if should_curate(db, config):
        await curate(db, config)
    increment_session_counter(db)
```

**Done when:** Remaining lifecycle playground test cases (curate subset) pass.

---

### Step 8: Real LLM Adapters

**Build:**
```python
# elfmem/adapters/models.py
class AlignmentScore(BaseModel): score: float = Field(ge=0.0, le=1.0)
class SelfTagInference(BaseModel): tags: list[str]
class ContradictionScore(BaseModel): score: float = Field(ge=0.0, le=1.0)

# elfmem/adapters/litellm.py
class LiteLLMAdapter(LLMService):
    def __init__(self, config: LLMConfig, prompts: PromptsConfig | None = None): ...

class LiteLLMEmbeddingAdapter(EmbeddingService):
    def __init__(self, config: EmbeddingConfig): ...
```

**Why last:** All business logic is verified with mocks. The real adapter is
just a thin wrapper — if the logic is right with mocks, it will be right with
real LLMs. This is the only step that requires API keys.

**Done when:** Integration test with real provider (e.g., `gpt-4o-mini`) returns
valid structured outputs; `MemorySystem.from_config()` works with a YAML file.

---

### Step 9: Config + Factory

**Build:**
```python
# elfmem/config.py  (may be partially built in earlier steps)
class AMGSConfig(BaseModel):
    llm: LLMConfig
    embeddings: EmbeddingConfig
    memory: MemoryConfig
    prompts: PromptsConfig

# elfmem/api.py — factory
@classmethod
def from_config(cls, db_path: str, config: AMGSConfig | str | dict | None = None) -> "MemorySystem":
    ...
```

**Done when:** The worked example from exploration 023 runs end-to-end:
```python
system = MemorySystem.from_config("test.db", "amgs.yaml")
async with system.session():
    await system.learn("I prefer explicit error handling over silent failures.")
    result = await system.frame("attention", query="error handling strategies")
    print(result.text)
```

---

## Dependency Map

```
Step 0: Skeleton + Tooling
  ↓
Step 1: Types + Ports             ← Nothing depends on this yet; this must exist first
  ↓
Step 2: Scoring                   ← Depends on: Types
  ↓
Step 3: Schema + Storage          ← Depends on: Types
  ↓
Step 4: Mock Adapters             ← Depends on: Ports
  ↓
Step 5: learn() + consolidate()   ← Depends on: Types, Ports, Schema, Mocks
  ↓
Step 6: recall() + frame()        ← Depends on: Types, Ports, Schema, Scoring, Mocks
  ↓
Step 7: curate()                  ← Depends on: Schema, Scoring, recall()
  ↓
Step 8: Real LLM Adapters         ← Depends on: Ports, Scoring
  ↓
Step 9: Config + Factory          ← Depends on: everything above
```

Steps 2 and 3 can proceed in parallel after Step 1. Steps 5–7 must be sequential
(learn before recall; recall before curate). Step 8 can begin any time after Step 4.

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| **R1: SQLite async design** — SQLAlchemy async with SQLite requires `aiosqlite` driver or greenlet; mixing sync/async causes deadlocks | High | High | Decide at Step 0: use `aiosqlite` throughout, or run sync DB ops in a thread pool. Commit fully. |
| **R2: Schema regret** — A field or table design is wrong and discovered after blocks are stored | Medium | High | Review schema against all explorations before running migrations. Schema stays in `models.py`. |
| **R3: Mock similarity** — Tests for retrieval depend on similarity values; hash-seeded embeddings aren't semantically meaningful | High | Medium | Use `similarity_overrides` in MockEmbeddingService; inject exact similarity values for test pairs. |
| **R4: Scoring drift** — Formula changed during implementation, historical scores invalidated | Low | High | Treat `scoring.py` as frozen after Step 2. Any change is a breaking version. |
| **R5: Type annotation debt** — `mypy` skipped early, then impossible to fix later | Medium | Medium | Run `mypy --strict` from Step 0. Strict from day one is easier than adding strict later. |
| **R6: Consolidate performance** — N blocks × 3 LLM calls = slow for large inboxes | Low | Medium | Phase 1 serial calls are acceptable. Noted as batching opportunity in exploration 025. |

---

## What "Done" Looks Like

The implementation is complete when this works:

```python
import asyncio
from elfmem import MemorySystem

async def main():
    system = MemorySystem.from_config("agent.db", {
        "llm": {"model": "gpt-4o-mini"},
        "embeddings": {"model": "text-embedding-3-small", "dimensions": 1536},
    })

    async with system.session():
        # Learn new knowledge
        await system.learn(
            "I always explain my reasoning before giving recommendations.",
            tags=["self/style"],
        )
        await system.learn(
            "Use Celery with Redis for background tasks in Django applications.",
        )

        # Retrieve context
        self_ctx  = await system.frame("self")
        attn_ctx  = await system.frame("attention", query="background job processing")
        task_ctx  = await system.frame("task",      query="background job processing")

        print(self_ctx.text)   # → "You are an agent that..."
        print(attn_ctx.text)   # → "## Relevant Knowledge\n\n[Celery block]..."
        print(len(task_ctx.blocks))  # → ≤5

asyncio.run(main())
```

And all 64 playground test cases pass against `MockLLMService` + `MockEmbeddingService`.

---

## Locked Design Decisions

| Decision | Rationale |
|----------|-----------|
| Foundation first (types, ports, schema), then vertical slices | Sticky decisions locked early; no re-work of shared contracts |
| `asyncio` throughout — no sync/async mixing | Simpler than mixing; `aiosqlite` or thread pool for SQLite |
| `mypy --strict` from day one | Adding strict later is nearly impossible on a real codebase |
| Mock adapters before real adapters | Full test suite runs without API keys; all logic verified cheaply |
| Scoring as first module | Zero dependencies; 12 tests pass immediately; validates the core formula |
| `learn()` then `recall()` then `curate()` slice order | Data must exist before retrieval; retrieval signals needed by curate |
| Real LLM adapters last | Logic verified with mocks first; adapter is a thin wrapper, not business logic |
| Config and factory last | Convenience wrapper; should not be on the critical path |
